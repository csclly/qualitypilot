import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
import pytest

from app.agent.audit import SqlApprovalAuditStore
from app.agent.metrics import AgentMetricsSnapshot, SqlAgentMetricsStore
from app.agent.run_errors import ClassifiedAgentError, SqlAgentRunErrorStore
from app.agent.security import (
    ALERT_VIEWER_ROLE,
    API_KEY_AUTH_METHOD,
    ApprovalPrincipal,
)
from app.api.routes.agent import get_approval_principal
from app.api.routes.agent_metrics import get_agent_metrics_store
from app.db import SessionLocal
from app.main import app


class FakeAgentMetricsStore:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def snapshot(
        self,
        *,
        window_hours: int,
        alert_threshold: int,
    ) -> AgentMetricsSnapshot:
        self.calls.append((window_hours, alert_threshold))
        generated_at = datetime.now(UTC)
        return AgentMetricsSnapshot(
            generated_at=generated_at,
            window_started_at=generated_at - timedelta(hours=window_hours),
            window_hours=window_hours,
            approval_decisions=3,
            authenticated_approvals=2,
            approved_decisions=2,
            rejected_decisions=1,
            error_events=5,
            affected_runs=4,
            retryable_errors=3,
            errors_by_stage={"retrieval": 4, "drafting": 1},
            errors_by_kind={"embedding_timeout": 4, "unexpected": 1},
            alert_status="warning",
            alert_threshold=alert_threshold,
        )


async def test_agent_metrics_api_returns_aggregate_only() -> None:
    store = FakeAgentMetricsStore()
    app.dependency_overrides[get_approval_principal] = lambda: ApprovalPrincipal(
        actor_id="metrics-reader",
        roles=frozenset({ALERT_VIEWER_ROLE}),
        authenticated=True,
        auth_method=API_KEY_AUTH_METHOD,
    )
    app.dependency_overrides[get_agent_metrics_store] = lambda: store
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v1/agent/metrics",
                params={"window_hours": 12},
            )
            invalid = await client.get(
                "/api/v1/agent/metrics",
                params={"window_hours": 0},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["window_hours"] == 12
        assert body["alert_status"] == "warning"
        assert body["errors_by_stage"] == {"retrieval": 4, "drafting": 1}
        assert "actor_id" not in body
        assert "message" not in body
        assert invalid.status_code == 422
        assert store.calls == [(12, 5)]
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_agent_metrics_store, None)


@pytest.mark.parametrize(
    ("principal", "expected_status"),
    [
        (
            ApprovalPrincipal("unverified", frozenset(), False, None),
            401,
        ),
        (
            ApprovalPrincipal(
                "authenticated-observer",
                frozenset(),
                True,
                API_KEY_AUTH_METHOD,
            ),
            403,
        ),
    ],
)
async def test_agent_metrics_api_requires_viewer_role(
    principal: ApprovalPrincipal,
    expected_status: int,
) -> None:
    store = FakeAgentMetricsStore()
    app.dependency_overrides[get_approval_principal] = lambda: principal
    app.dependency_overrides[get_agent_metrics_store] = lambda: store
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/agent/metrics")

        assert response.status_code == expected_status
        assert store.calls == []
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_agent_metrics_store, None)


@pytest.mark.integration
async def test_sql_metrics_snapshot_reports_incremental_events_and_threshold() -> None:
    run_id = uuid.uuid4()
    async with SessionLocal() as session:
        metrics = SqlAgentMetricsStore(session)
        baseline = await metrics.snapshot(window_hours=24, alert_threshold=10000)

        await SqlApprovalAuditStore(session).append_decision(
            event_id=uuid.uuid4(),
            run_id=run_id,
            actor_id="metrics-engineer",
            actor_authenticated=True,
            auth_method="api_key_sha256",
            approved=True,
            comment="指标测试",
        )
        errors = SqlAgentRunErrorStore(session)
        await errors.append(
            run_id=run_id,
            stage="retrieval",
            error=ClassifiedAgentError(
                error_kind="embedding_timeout",
                message="Embedding 服务调用超时",
                retryable=True,
            ),
        )
        await errors.append(
            run_id=run_id,
            stage="drafting",
            error=ClassifiedAgentError(
                error_kind="unexpected",
                message="Agent 节点发生未分类错误",
                retryable=False,
            ),
        )
        snapshot = await metrics.snapshot(
            window_hours=24,
            alert_threshold=baseline.error_events + 2,
        )

    assert snapshot.approval_decisions == baseline.approval_decisions + 1
    assert snapshot.authenticated_approvals == baseline.authenticated_approvals + 1
    assert snapshot.approved_decisions == baseline.approved_decisions + 1
    assert snapshot.error_events == baseline.error_events + 2
    assert snapshot.affected_runs == baseline.affected_runs + 1
    assert snapshot.retryable_errors == baseline.retryable_errors + 1
    assert snapshot.errors_by_stage["retrieval"] == (
        baseline.errors_by_stage.get("retrieval", 0) + 1
    )
    assert snapshot.errors_by_stage["drafting"] == (
        baseline.errors_by_stage.get("drafting", 0) + 1
    )
    assert snapshot.alert_status == "warning"
