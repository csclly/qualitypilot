import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from pydantic import ValidationError
import pytest
from sqlalchemy import update

from app.agent.alert_delivery import (
    AgentAlertDeliveryService,
    AlertDeliveryError,
    WebhookAgentAlertDeliveryProvider,
)
from app.agent.alerts import AgentAlertOutboxRecord, SqlAgentAlertOutboxStore
from app.agent.metrics import AgentMetricsSnapshot
from app.agent.security import (
    API_KEY_AUTH_METHOD,
    ALERT_OPERATOR_ROLE,
    ApprovalPrincipal,
)
from app.api.routes.agent import get_approval_principal
from app.api.routes.agent_alerts import (
    get_alert_delivery_provider,
    get_alert_outbox_store,
)
from app.core.config import Settings
from app.db import SessionLocal
from app.main import app
from app.models import AgentAlertOutbox


def _record(*, status: str = "pending", attempt_count: int = 0) -> AgentAlertOutboxRecord:
    now = datetime(2026, 8, 8, 8, tzinfo=UTC)
    lease_token = uuid.uuid4() if status == "delivering" else None
    return AgentAlertOutboxRecord(
        id=uuid.uuid4(),
        fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
        window_started_at=now - timedelta(hours=24),
        window_ended_at=now,
        window_hours=24,
        error_events=7,
        alert_threshold=5,
        status=status,  # type: ignore[arg-type]
        attempt_count=attempt_count,
        next_attempt_at=now,
        lease_token=lease_token,
        lease_expires_at=(now + timedelta(seconds=60) if lease_token else None),
        delivered_at=now if status == "delivered" else None,
        last_error_kind=None,
        created_at=now,
    )


def _snapshot() -> AgentMetricsSnapshot:
    generated_at = datetime.now(UTC) + timedelta(hours=uuid.uuid4().int % 100_000)
    return AgentMetricsSnapshot(
        generated_at=generated_at,
        window_started_at=generated_at - timedelta(hours=24),
        window_hours=24,
        approval_decisions=0,
        authenticated_approvals=0,
        approved_decisions=0,
        rejected_decisions=0,
        error_events=7,
        affected_runs=2,
        retryable_errors=1,
        errors_by_stage={"retrieval": 7},
        errors_by_kind={"embedding_timeout": 7},
        alert_status="warning",
        alert_threshold=5,
    )


class FakeDeliveryStore:
    def __init__(self, record: AgentAlertOutboxRecord | None) -> None:
        self.record = record
        self.claim_calls = 0

    async def enqueue(self, snapshot: AgentMetricsSnapshot):
        raise AssertionError("投递测试不应创建新告警")

    async def claim_next(self, *, lease_seconds: int, now=None):
        self.claim_calls += 1
        if self.record is None or self.record.status != "pending":
            return None
        self.record = replace(
            self.record,
            status="delivering",
            attempt_count=self.record.attempt_count + 1,
            lease_token=uuid.uuid4(),
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
        )
        return self.record

    async def mark_delivered(self, record, *, delivered_at=None):
        self.record = replace(
            record,
            status="delivered",
            lease_token=None,
            lease_expires_at=None,
            delivered_at=delivered_at or datetime.now(UTC),
            last_error_kind=None,
        )
        return self.record

    async def mark_failed(
        self,
        record,
        *,
        error_kind,
        retryable,
        max_attempts,
        retry_base_delay_seconds,
        retry_max_delay_seconds,
        now=None,
    ):
        terminal = not retryable or record.attempt_count >= max_attempts
        self.record = replace(
            record,
            status="failed" if terminal else "pending",
            lease_token=None,
            lease_expires_at=None,
            last_error_kind=error_kind,
        )
        return self.record


class FakeDeliveryProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def deliver(self, alert: AgentAlertOutboxRecord) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


async def test_webhook_provider_sends_minimal_idempotent_payload() -> None:
    requests: list[Request] = []

    async def handler(request: Request) -> Response:
        requests.append(request)
        return Response(204)

    client = AsyncClient(transport=MockTransport(handler))
    provider = WebhookAgentAlertDeliveryProvider(
        webhook_url="https://alerts.example.test/qualitypilot",
        bearer_token="test-delivery-token",
        timeout_seconds=3,
        client=client,
    )
    alert = replace(_record(status="delivering", attempt_count=2))
    try:
        await provider.deliver(alert)
    finally:
        await client.aclose()

    assert len(requests) == 1
    request = requests[0]
    payload = json.loads(request.content)
    assert request.headers["Idempotency-Key"] == alert.fingerprint
    assert request.headers["Authorization"] == "Bearer test-delivery-token"
    assert payload["event_type"] == "agent_error_threshold_reached"
    assert payload["alert_id"] == str(alert.id)
    assert payload["attempt"] == 2
    assert "lease_token" not in payload


@pytest.mark.parametrize(
    ("status_code", "kind", "retryable"),
    [(429, "webhook_retryable_http", True), (503, "webhook_retryable_http", True), (400, "webhook_rejected", False)],
)
async def test_webhook_provider_classifies_http_errors(
    status_code: int,
    kind: str,
    retryable: bool,
) -> None:
    client = AsyncClient(transport=MockTransport(lambda request: Response(status_code)))
    provider = WebhookAgentAlertDeliveryProvider(
        webhook_url="https://alerts.example.test/qualitypilot",
        bearer_token=None,
        timeout_seconds=3,
        client=client,
    )
    try:
        with pytest.raises(AlertDeliveryError) as caught:
            await provider.deliver(_record(status="delivering", attempt_count=1))
    finally:
        await client.aclose()

    assert caught.value.kind == kind
    assert caught.value.retryable is retryable


def test_webhook_provider_rejects_insecure_remote_url() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        WebhookAgentAlertDeliveryProvider(
            webhook_url="http://alerts.example.test/hook",
            bearer_token=None,
            timeout_seconds=3,
        )


def test_alert_delivery_settings_require_lease_longer_than_timeout() -> None:
    with pytest.raises(ValidationError, match="LEASE_SECONDS"):
        Settings(
            _env_file=None,
            agent_alert_lease_seconds=5,
            agent_alert_delivery_timeout_seconds=5,
        )


@pytest.mark.parametrize(
    ("error", "max_attempts", "outcome", "status"),
    [
        (AlertDeliveryError("webhook_timeout", retryable=True), 3, "retry_scheduled", "pending"),
        (AlertDeliveryError("webhook_rejected", retryable=False), 3, "failed", "failed"),
        (RuntimeError("private webhook response"), 1, "failed", "failed"),
    ],
)
async def test_delivery_service_handles_success_and_sanitized_failures(
    error: Exception,
    max_attempts: int,
    outcome: str,
    status: str,
) -> None:
    store = FakeDeliveryStore(_record())
    service = AgentAlertDeliveryService(
        store=store,
        provider=FakeDeliveryProvider(error),
        lease_seconds=30,
        max_attempts=max_attempts,
        retry_base_delay_seconds=1,
        retry_max_delay_seconds=30,
    )

    result = await service.process_one()

    assert result.outcome == outcome
    assert result.alert is not None
    assert result.alert.status == status
    if isinstance(error, RuntimeError):
        assert result.alert.last_error_kind == "unexpected"
        assert "private webhook response" not in result.alert.last_error_kind


async def test_delivery_service_marks_success_and_then_becomes_idle() -> None:
    store = FakeDeliveryStore(_record())
    provider = FakeDeliveryProvider()
    service = AgentAlertDeliveryService(
        store=store,
        provider=provider,
        lease_seconds=30,
        max_attempts=3,
        retry_base_delay_seconds=1,
        retry_max_delay_seconds=30,
    )

    delivered = await service.process_one()
    idle = await service.process_one()

    assert delivered.outcome == "delivered"
    assert delivered.alert is not None
    assert delivered.alert.status == "delivered"
    assert delivered.alert.delivered_at is not None
    assert idle.outcome == "idle"
    assert provider.calls == 1


async def test_process_endpoint_requires_auth_before_configuration() -> None:
    store = FakeDeliveryStore(_record())
    app.dependency_overrides[get_approval_principal] = lambda: ApprovalPrincipal(
        actor_id="unverified",
        roles=frozenset(),
        authenticated=False,
        auth_method=None,
    )
    app.dependency_overrides[get_alert_outbox_store] = lambda: store
    app.dependency_overrides[get_alert_delivery_provider] = lambda: None
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/v1/agent/alerts/process")
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_alert_outbox_store, None)
        app.dependency_overrides.pop(get_alert_delivery_provider, None)

    assert response.status_code == 401
    assert store.claim_calls == 0


async def test_process_endpoint_delivers_available_alert() -> None:
    store = FakeDeliveryStore(_record())
    provider = FakeDeliveryProvider()
    app.dependency_overrides[get_approval_principal] = lambda: ApprovalPrincipal(
        actor_id="operations-engineer",
        roles=frozenset({ALERT_OPERATOR_ROLE}),
        authenticated=True,
        auth_method=API_KEY_AUTH_METHOD,
    )
    app.dependency_overrides[get_alert_outbox_store] = lambda: store
    app.dependency_overrides[get_alert_delivery_provider] = lambda: provider
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/v1/agent/alerts/process?limit=2")
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_alert_outbox_store, None)
        app.dependency_overrides.pop(get_alert_delivery_provider, None)

    assert response.status_code == 200
    assert response.json()["processed"] == 1
    assert response.json()["delivered"] == 1
    assert response.json()["results"][0]["alert"]["attempt_count"] == 1


async def test_process_endpoint_reports_missing_webhook_configuration() -> None:
    store = FakeDeliveryStore(_record())
    app.dependency_overrides[get_approval_principal] = lambda: ApprovalPrincipal(
        actor_id="operations-engineer",
        roles=frozenset({ALERT_OPERATOR_ROLE}),
        authenticated=True,
        auth_method=API_KEY_AUTH_METHOD,
    )
    app.dependency_overrides[get_alert_outbox_store] = lambda: store
    app.dependency_overrides[get_alert_delivery_provider] = lambda: None
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/v1/agent/alerts/process")
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_alert_outbox_store, None)
        app.dependency_overrides.pop(get_alert_delivery_provider, None)

    assert response.status_code == 503
    assert response.json()["detail"] == "尚未配置告警 Webhook"
    assert store.claim_calls == 0


@pytest.mark.integration
async def test_sql_outbox_concurrent_claim_and_lease_recovery() -> None:
    scheduled_at = datetime(1900, 1, 1, tzinfo=UTC) + timedelta(
        microseconds=uuid.uuid4().int % 1_000_000
    )
    async with SessionLocal() as setup_session:
        store = SqlAgentAlertOutboxStore(setup_session)
        queued, _ = await store.enqueue(_snapshot())
        await setup_session.execute(
            update(AgentAlertOutbox)
            .where(AgentAlertOutbox.id == queued.id)
            .values(next_attempt_at=scheduled_at)
        )
        await setup_session.commit()

    first_session = SessionLocal()
    second_session = SessionLocal()
    try:
        claims = await asyncio.gather(
            SqlAgentAlertOutboxStore(first_session).claim_next(
                lease_seconds=5,
                now=scheduled_at,
            ),
            SqlAgentAlertOutboxStore(second_session).claim_next(
                lease_seconds=5,
                now=scheduled_at,
            ),
        )
    finally:
        await first_session.close()
        await second_session.close()

    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0].id == queued.id
    assert claimed[0].attempt_count == 1

    async with SessionLocal() as recovery_session:
        recovered = await SqlAgentAlertOutboxStore(recovery_session).claim_next(
            lease_seconds=5,
            now=scheduled_at + timedelta(seconds=6),
        )
        assert recovered is not None
        assert recovered.id == queued.id
        assert recovered.attempt_count == 2
        assert recovered.lease_token != claimed[0].lease_token
        async with SessionLocal() as stale_session:
            with pytest.raises(ValueError, match="租约已失效"):
                await SqlAgentAlertOutboxStore(stale_session).mark_delivered(
                    claimed[0]
                )
        failed = await SqlAgentAlertOutboxStore(recovery_session).mark_failed(
            recovered,
            error_kind="webhook_rejected",
            retryable=False,
            max_attempts=5,
            retry_base_delay_seconds=1,
            retry_max_delay_seconds=30,
            now=scheduled_at + timedelta(seconds=7),
        )

    assert failed.status == "failed"
    assert failed.lease_token is None
    assert failed.last_error_kind == "webhook_rejected"


@pytest.mark.integration
async def test_sql_outbox_schedules_retry_and_marks_delivery() -> None:
    scheduled_at = datetime(1901, 1, 1, tzinfo=UTC) + timedelta(
        microseconds=uuid.uuid4().int % 1_000_000
    )
    async with SessionLocal() as session:
        store = SqlAgentAlertOutboxStore(session)
        queued, _ = await store.enqueue(_snapshot())
        await session.execute(
            update(AgentAlertOutbox)
            .where(AgentAlertOutbox.id == queued.id)
            .values(next_attempt_at=scheduled_at)
        )
        await session.commit()

        claimed = await store.claim_next(lease_seconds=30, now=scheduled_at)
        assert claimed is not None
        assert claimed.id == queued.id
        retry = await store.mark_failed(
            claimed,
            error_kind="webhook_timeout",
            retryable=True,
            max_attempts=3,
            retry_base_delay_seconds=10,
            retry_max_delay_seconds=60,
            now=scheduled_at,
        )
        assert retry.status == "pending"
        assert retry.next_attempt_at == scheduled_at + timedelta(seconds=10)
        assert retry.last_error_kind == "webhook_timeout"

        too_early = await store.claim_next(
            lease_seconds=30,
            now=scheduled_at + timedelta(seconds=9),
        )
        assert too_early is None
        second_attempt = await store.claim_next(
            lease_seconds=30,
            now=scheduled_at + timedelta(seconds=10),
        )
        assert second_attempt is not None
        assert second_attempt.id == queued.id
        assert second_attempt.attempt_count == 2
        delivered = await store.mark_delivered(
            second_attempt,
            delivered_at=scheduled_at + timedelta(seconds=11),
        )

    assert delivered.status == "delivered"
    assert delivered.delivered_at == scheduled_at + timedelta(seconds=11)
    assert delivered.lease_token is None
    assert delivered.last_error_kind is None
