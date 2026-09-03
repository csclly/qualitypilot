import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
import pytest

from app.agent.alerts import (
    AgentAlertOutboxRecord,
    SqlAgentAlertOutboxStore,
    alert_fingerprint,
)
from app.agent.metrics import AgentMetricsSnapshot
from app.agent.security import (
    API_KEY_AUTH_METHOD,
    ALERT_OPERATOR_ROLE,
    ApprovalPrincipal,
)
from app.api.routes.agent import get_approval_principal
from app.api.routes.agent_alerts import (
    get_alert_metrics_store,
    get_alert_outbox_store,
)
from app.db import SessionLocal
from app.main import app


def _snapshot(*, error_events: int, threshold: int) -> AgentMetricsSnapshot:
    generated_at = datetime(2026, 8, 8, 4, 15, tzinfo=UTC)
    return AgentMetricsSnapshot(
        generated_at=generated_at,
        window_started_at=generated_at - timedelta(hours=24),
        window_hours=24,
        approval_decisions=0,
        authenticated_approvals=0,
        approved_decisions=0,
        rejected_decisions=0,
        error_events=error_events,
        affected_runs=error_events,
        retryable_errors=error_events,
        errors_by_stage={"retrieval": error_events} if error_events else {},
        errors_by_kind={"embedding_timeout": error_events} if error_events else {},
        alert_status="warning" if error_events >= threshold else "ok",
        alert_threshold=threshold,
    )


def _unique_warning_snapshot() -> AgentMetricsSnapshot:
    original = _snapshot(error_events=5, threshold=5)
    hour_offset = uuid.uuid4().int % 500_000
    generated_at = original.generated_at + timedelta(hours=hour_offset)
    return replace(
        original,
        generated_at=generated_at,
        window_started_at=generated_at - timedelta(hours=original.window_hours),
    )


class FakeMetricsStore:
    def __init__(self, snapshot: AgentMetricsSnapshot) -> None:
        self.value = snapshot
        self.calls = 0

    async def snapshot(
        self,
        *,
        window_hours: int,
        alert_threshold: int,
    ) -> AgentMetricsSnapshot:
        self.calls += 1
        assert window_hours == self.value.window_hours
        assert alert_threshold == self.value.alert_threshold
        return self.value


class FakeOutboxStore:
    def __init__(self) -> None:
        self.record: AgentAlertOutboxRecord | None = None
        self.calls = 0

    async def enqueue(
        self,
        snapshot: AgentMetricsSnapshot,
    ) -> tuple[AgentAlertOutboxRecord, bool]:
        self.calls += 1
        if self.record is not None:
            return self.record, False
        self.record = AgentAlertOutboxRecord(
            id=uuid.uuid4(),
            fingerprint=alert_fingerprint(snapshot),
            window_started_at=snapshot.window_started_at,
            window_ended_at=snapshot.generated_at,
            window_hours=snapshot.window_hours,
            error_events=snapshot.error_events,
            alert_threshold=snapshot.alert_threshold,
            status="pending",
            attempt_count=0,
            next_attempt_at=datetime.now(UTC),
            lease_token=None,
            lease_expires_at=None,
            delivered_at=None,
            last_error_kind=None,
            created_at=datetime.now(UTC),
        )
        return self.record, True


async def test_alert_evaluation_requires_authentication() -> None:
    metrics = FakeMetricsStore(_snapshot(error_events=5, threshold=5))
    outbox = FakeOutboxStore()
    app.dependency_overrides[get_approval_principal] = lambda: ApprovalPrincipal(
        actor_id="unverified",
        roles=frozenset(),
        authenticated=False,
        auth_method=None,
    )
    app.dependency_overrides[get_alert_metrics_store] = lambda: metrics
    app.dependency_overrides[get_alert_outbox_store] = lambda: outbox
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/v1/agent/alerts/evaluate")

        assert response.status_code == 401
        assert metrics.calls == 0
        assert outbox.calls == 0
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_alert_metrics_store, None)
        app.dependency_overrides.pop(get_alert_outbox_store, None)


async def test_alert_evaluation_requires_authorized_role() -> None:
    metrics = FakeMetricsStore(_snapshot(error_events=5, threshold=5))
    outbox = FakeOutboxStore()
    app.dependency_overrides[get_approval_principal] = lambda: ApprovalPrincipal(
        actor_id="authenticated-observer",
        roles=frozenset(),
        authenticated=True,
        auth_method=API_KEY_AUTH_METHOD,
    )
    app.dependency_overrides[get_alert_metrics_store] = lambda: metrics
    app.dependency_overrides[get_alert_outbox_store] = lambda: outbox
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/v1/agent/alerts/evaluate")

        assert response.status_code == 403
        assert metrics.calls == 0
        assert outbox.calls == 0
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_alert_metrics_store, None)
        app.dependency_overrides.pop(get_alert_outbox_store, None)


async def test_alert_evaluation_queues_warning_once_and_skips_ok() -> None:
    warning_metrics = FakeMetricsStore(_snapshot(error_events=5, threshold=5))
    outbox = FakeOutboxStore()
    principal = ApprovalPrincipal(
        actor_id="operations-engineer",
        roles=frozenset({ALERT_OPERATOR_ROLE}),
        authenticated=True,
        auth_method=API_KEY_AUTH_METHOD,
    )
    app.dependency_overrides[get_approval_principal] = lambda: principal
    app.dependency_overrides[get_alert_metrics_store] = lambda: warning_metrics
    app.dependency_overrides[get_alert_outbox_store] = lambda: outbox
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            first = await client.post("/api/v1/agent/alerts/evaluate")
            duplicate = await client.post("/api/v1/agent/alerts/evaluate")
            ok_metrics = FakeMetricsStore(_snapshot(error_events=4, threshold=5))
            app.dependency_overrides[get_alert_metrics_store] = lambda: ok_metrics
            normal = await client.post("/api/v1/agent/alerts/evaluate")

        assert first.status_code == 200
        assert first.json()["triggered"] is True
        assert first.json()["queued"] is True
        assert first.json()["alert"]["status"] == "pending"
        assert duplicate.json()["triggered"] is True
        assert duplicate.json()["queued"] is False
        assert duplicate.json()["alert"]["id"] == first.json()["alert"]["id"]
        assert normal.json()["triggered"] is False
        assert normal.json()["queued"] is False
        assert normal.json()["alert"] is None
        assert outbox.calls == 2
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_alert_metrics_store, None)
        app.dependency_overrides.pop(get_alert_outbox_store, None)


@pytest.mark.integration
async def test_sql_alert_outbox_deduplicates_same_hour_window() -> None:
    snapshot = _unique_warning_snapshot()
    async with SessionLocal() as session:
        store = SqlAgentAlertOutboxStore(session)
        first, first_created = await store.enqueue(snapshot)
        duplicate, duplicate_created = await store.enqueue(snapshot)

    assert first_created is True
    assert duplicate_created is False
    assert duplicate == first
    assert first.status == "pending"
    assert len(first.fingerprint) == 64


@pytest.mark.integration
async def test_sql_alert_outbox_rejects_non_triggered_snapshot() -> None:
    async with SessionLocal() as session:
        store = SqlAgentAlertOutboxStore(session)
        with pytest.raises(ValueError, match="达到阈值"):
            await store.enqueue(_snapshot(error_events=4, threshold=5))


@pytest.mark.integration
async def test_sql_alert_outbox_handles_concurrent_duplicate_enqueue() -> None:
    original = _unique_warning_snapshot()
    snapshot = replace(
        original,
        generated_at=original.generated_at + timedelta(hours=1),
        window_started_at=original.window_started_at + timedelta(hours=1),
        error_events=6,
    )

    async with SessionLocal() as first_session, SessionLocal() as second_session:
        results = await asyncio.gather(
            SqlAgentAlertOutboxStore(first_session).enqueue(snapshot),
            SqlAgentAlertOutboxStore(second_session).enqueue(snapshot),
        )

    records = [record for record, _ in results]
    created_flags = [created for _, created in results]
    assert created_flags.count(True) == 1
    assert created_flags.count(False) == 1
    assert records[0].id == records[1].id
