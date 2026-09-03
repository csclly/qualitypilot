import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
import pytest
from sqlalchemy import delete, update

from app.agent.alert_scheduler import (
    AgentAlertCycleResult,
    AgentAlertScheduler,
    SqlAgentAlertCycleRunner,
)
from app.agent.alerts import SqlAgentAlertOutboxStore
from app.agent.metrics import AgentMetricsSnapshot
from app.agent.security import (
    API_KEY_AUTH_METHOD,
    ALERT_VIEWER_ROLE,
    ApprovalPrincipal,
)
from app.api.routes.agent import get_approval_principal
from app.core.config import Settings
from app.db import SessionLocal
from app.main import app
from app.models import AgentAlertOutbox


class FakeCycleRunner:
    def __init__(self, values: list[AgentAlertCycleResult | Exception]) -> None:
        self.values = values
        self.calls = 0
        self.called = asyncio.Event()

    async def run_cycle(self) -> AgentAlertCycleResult:
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        self.called.set()
        if isinstance(value, Exception):
            raise value
        return value


class RecordingProvider:
    def __init__(self) -> None:
        self.alert_ids: list[uuid.UUID] = []

    async def deliver(self, alert) -> None:
        self.alert_ids.append(alert.id)


def _cycle_result(
    *,
    queued: bool = True,
    processed: int = 2,
    delivered: int = 1,
    retry_scheduled: int = 1,
    failed: int = 0,
) -> AgentAlertCycleResult:
    return AgentAlertCycleResult(
        warning=True,
        queued=queued,
        processed=processed,
        delivered=delivered,
        retry_scheduled=retry_scheduled,
        failed=failed,
    )


async def test_scheduler_accumulates_cycle_status() -> None:
    scheduler = AgentAlertScheduler(
        runner=FakeCycleRunner([_cycle_result()]),
        interval_seconds=60,
    )

    succeeded = await scheduler.run_cycle_once()
    snapshot = scheduler.snapshot()

    assert succeeded is True
    assert snapshot.enabled is True
    assert snapshot.running is False
    assert snapshot.cycles_completed == 1
    assert snapshot.cycles_failed == 0
    assert snapshot.alerts_queued == 1
    assert snapshot.alerts_processed == 2
    assert snapshot.alerts_delivered == 1
    assert snapshot.alerts_retry_scheduled == 1
    assert snapshot.last_cycle_started_at is not None
    assert snapshot.last_cycle_completed_at is not None
    assert snapshot.last_error_kind is None


async def test_scheduler_isolates_failure_and_recovers_next_cycle() -> None:
    runner = FakeCycleRunner(
        [RuntimeError("private database URL and token"), _cycle_result()]
    )
    scheduler = AgentAlertScheduler(runner=runner, interval_seconds=60)

    failed = await scheduler.run_cycle_once()
    failed_snapshot = scheduler.snapshot()
    recovered = await scheduler.run_cycle_once()
    recovered_snapshot = scheduler.snapshot()

    assert failed is False
    assert failed_snapshot.cycles_failed == 1
    assert failed_snapshot.last_error_kind == "RuntimeError"
    assert "private" not in failed_snapshot.last_error_kind
    assert recovered is True
    assert recovered_snapshot.cycles_completed == 1
    assert recovered_snapshot.cycles_failed == 1
    assert recovered_snapshot.last_error_kind is None


async def test_scheduler_start_is_idempotent_and_stop_is_graceful() -> None:
    runner = FakeCycleRunner([_cycle_result(processed=0, delivered=0, retry_scheduled=0)])
    scheduler = AgentAlertScheduler(runner=runner, interval_seconds=3600)

    assert scheduler.start() is True
    assert scheduler.start() is False
    await asyncio.wait_for(runner.called.wait(), timeout=1)
    assert scheduler.snapshot().running is True
    await scheduler.stop()

    assert scheduler.snapshot().running is False
    assert runner.calls == 1
    await scheduler.stop()


def test_scheduler_configuration_requires_webhook_when_enabled() -> None:
    with pytest.raises(ValidationError, match="WEBHOOK_URL"):
        Settings(
            _env_file=None,
            agent_alert_scheduler_enabled=True,
            agent_alert_webhook_url=None,
        )


async def test_scheduler_status_endpoint_requires_authentication() -> None:
    app.dependency_overrides[get_approval_principal] = lambda: ApprovalPrincipal(
        actor_id="unverified",
        roles=frozenset(),
        authenticated=False,
        auth_method=None,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/agent/alerts/scheduler")
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)

    assert response.status_code == 401


async def test_scheduler_status_endpoint_requires_authorized_role() -> None:
    app.dependency_overrides[get_approval_principal] = lambda: ApprovalPrincipal(
        actor_id="authenticated-observer",
        roles=frozenset(),
        authenticated=True,
        auth_method=API_KEY_AUTH_METHOD,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/agent/alerts/scheduler")
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)

    assert response.status_code == 403


async def test_scheduler_status_endpoint_reports_disabled_and_active() -> None:
    principal = ApprovalPrincipal(
        actor_id="operations-engineer",
        roles=frozenset({ALERT_VIEWER_ROLE}),
        authenticated=True,
        auth_method=API_KEY_AUTH_METHOD,
    )
    app.dependency_overrides[get_approval_principal] = lambda: principal
    original = getattr(app.state, "agent_alert_scheduler", None)
    try:
        app.state.agent_alert_scheduler = None
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            disabled = await client.get("/api/v1/agent/alerts/scheduler")

            scheduler = AgentAlertScheduler(
                runner=FakeCycleRunner([_cycle_result()]),
                interval_seconds=60,
            )
            await scheduler.run_cycle_once()
            app.state.agent_alert_scheduler = scheduler
            active = await client.get("/api/v1/agent/alerts/scheduler")
    finally:
        app.state.agent_alert_scheduler = original
        app.dependency_overrides.pop(get_approval_principal, None)

    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["running"] is False
    assert active.status_code == 200
    assert active.json()["enabled"] is True
    assert active.json()["cycles_completed"] == 1
    assert active.json()["alerts_delivered"] == 1


@pytest.mark.integration
async def test_sql_cycle_runner_delivers_existing_outbox_record() -> None:
    scheduled_at = datetime(1902, 1, 1, tzinfo=UTC) + timedelta(
        microseconds=uuid.uuid4().int % 1_000_000
    )
    generated_at = datetime.now(UTC) + timedelta(
        hours=uuid.uuid4().int % 100_000
    )
    snapshot = AgentMetricsSnapshot(
        generated_at=generated_at,
        window_started_at=generated_at - timedelta(hours=24),
        window_hours=24,
        approval_decisions=0,
        authenticated_approvals=0,
        approved_decisions=0,
        rejected_decisions=0,
        error_events=5,
        affected_runs=1,
        retryable_errors=1,
        errors_by_stage={"retrieval": 5},
        errors_by_kind={"embedding_timeout": 5},
        alert_status="warning",
        alert_threshold=5,
    )
    async with SessionLocal() as setup_session:
        queued, _ = await SqlAgentAlertOutboxStore(setup_session).enqueue(snapshot)
        await setup_session.execute(
            update(AgentAlertOutbox)
            .where(AgentAlertOutbox.id == queued.id)
            .values(next_attempt_at=scheduled_at)
        )
        await setup_session.commit()

    provider = RecordingProvider()
    runner = SqlAgentAlertCycleRunner(
        session_factory=SessionLocal,
        provider=provider,
        window_hours=24,
        alert_threshold=10_000,
        batch_size=1,
        lease_seconds=30,
        max_attempts=3,
        retry_base_delay_seconds=1,
        retry_max_delay_seconds=30,
    )
    result = await runner.run_cycle()

    try:
        assert result.warning is False
        assert result.queued is False
        assert result.processed == 1
        assert result.delivered == 1
        assert provider.alert_ids == [queued.id]
        async with SessionLocal() as verify_session:
            delivered = await verify_session.get(AgentAlertOutbox, queued.id)
            assert delivered is not None
            assert delivered.status == "delivered"
            assert delivered.delivered_at is not None
    finally:
        async with SessionLocal() as cleanup_session:
            await cleanup_session.execute(
                delete(AgentAlertOutbox).where(AgentAlertOutbox.id == queued.id)
            )
            await cleanup_session.commit()
