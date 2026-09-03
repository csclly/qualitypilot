import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from app.agent.business_tools import (
    BusinessRecord,
    BusinessSystem,
    BusinessToolUnavailableError,
    InMemoryReadOnlyBusinessTool,
)
from app.agent.audit import (
    ApprovalAuditConflictError,
    ApprovalAuditRecord,
)
from app.agent.security import (
    API_KEY_AUTH_METHOD,
    APPROVER_ROLE,
    ApprovalPrincipal,
)
from app.agent.run_errors import (
    AgentErrorStage,
    AgentRunErrorRecord,
    ClassifiedAgentError,
)
from app.api.routes.agent import (
    get_agent_run_error_store,
    get_approval_audit_store,
    get_approval_principal,
    get_read_only_business_tools,
)
from app.agent.retrieval import get_agent_evidence_retriever_builder
from app.agent.state import AgentEvidence
from app.agent.workflow import QualityAgentWorkflow, get_quality_agent_workflow
from app.main import app
from app.services.embedding.errors import EmbeddingTimeoutError


class FakeApprovalAuditStore:
    def __init__(self) -> None:
        self.record: ApprovalAuditRecord | None = None

    async def get_for_run(
        self,
        run_id: uuid.UUID,
    ) -> ApprovalAuditRecord | None:
        if self.record is not None and self.record.run_id == run_id:
            return self.record
        return None

    async def append_decision(
        self,
        *,
        event_id: uuid.UUID,
        run_id: uuid.UUID,
        actor_id: str,
        actor_authenticated: bool,
        auth_method: str | None,
        approved: bool,
        comment: str | None,
    ) -> tuple[ApprovalAuditRecord, bool]:
        if self.record is not None:
            if (
                self.record.id == event_id
                and self.record.run_id == run_id
                and self.record.actor_id == actor_id
                and self.record.actor_authenticated is actor_authenticated
                and self.record.auth_method == auth_method
                and self.record.approved is approved
                and self.record.comment == comment
            ):
                return self.record, False
            raise ApprovalAuditConflictError("审批事件冲突")
        self.record = ApprovalAuditRecord(
            id=event_id,
            run_id=run_id,
            actor_id=actor_id,
            actor_authenticated=actor_authenticated,
            auth_method=auth_method,
            approved=approved,
            comment=comment,
            occurred_at=datetime.now(UTC),
        )
        return self.record, True


class FakeAgentRunErrorStore:
    def __init__(self) -> None:
        self.records: list[AgentRunErrorRecord] = []

    async def append(
        self,
        *,
        run_id: uuid.UUID,
        stage: AgentErrorStage,
        error: ClassifiedAgentError,
    ) -> AgentRunErrorRecord:
        record = AgentRunErrorRecord(
            id=uuid.uuid4(),
            run_id=run_id,
            stage=stage,
            error_kind=error.error_kind,
            message=error.message,
            retryable=error.retryable,
            occurred_at=datetime.now(UTC),
        )
        self.records.append(record)
        return record

    async def list_for_run(
        self,
        run_id: uuid.UUID,
    ) -> list[AgentRunErrorRecord]:
        return [record for record in self.records if record.run_id == run_id]


def _fake_retriever_builder(_db: object, _provider_factory: object):
    async def retrieve(
        _question: str,
        *,
        top_k: int,
        mode: str,
    ) -> list[AgentEvidence]:
        assert top_k == 2
        assert mode == "keyword"
        return [
            {
                "chunk_id": str(uuid.uuid4()),
                "document_id": str(uuid.uuid4()),
                "document_title": "AOI 桥接处理规范",
                "source_uri": "upload://aoi.txt",
                "original_filename": "AOI规范.txt",
                "chunk_index": 1,
                "content": "先确认缺陷位置，再检查钢网与锡膏印刷。",
                "score": 0.88,
                "match_type": "keyword",
                "vector_score": None,
                "keyword_score": 0.88,
            }
        ]

    return retrieve


def _fake_business_tools():
    question = "AOI 发现桥接怎么办？"
    return (
        InMemoryReadOnlyBusinessTool(
            name="mes-reader",
            system=BusinessSystem.MES,
            responses={
                question: [
                    BusinessRecord(
                        system=BusinessSystem.MES,
                        record_id="MES-API-1",
                        record_type="batch_status",
                        summary="批次已暂停",
                    )
                ]
            },
        ),
        InMemoryReadOnlyBusinessTool(
            name="qms-reader",
            system=BusinessSystem.QMS,
            error=BusinessToolUnavailableError(),
        ),
    )


async def test_agent_api_start_get_and_approve() -> None:
    workflow = QualityAgentWorkflow()
    audit_store = FakeApprovalAuditStore()
    error_store = FakeAgentRunErrorStore()
    principal = ApprovalPrincipal(
        actor_id="verified-engineer-001",
        roles=frozenset({APPROVER_ROLE}),
        authenticated=True,
        auth_method=API_KEY_AUTH_METHOD,
    )
    app.dependency_overrides[get_quality_agent_workflow] = lambda: workflow
    app.dependency_overrides[get_agent_evidence_retriever_builder] = (
        lambda: _fake_retriever_builder
    )
    app.dependency_overrides[get_read_only_business_tools] = _fake_business_tools
    app.dependency_overrides[get_approval_audit_store] = lambda: audit_store
    app.dependency_overrides[get_approval_principal] = lambda: principal
    app.dependency_overrides[get_agent_run_error_store] = lambda: error_store
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            started = await client.post(
                "/api/v1/agent/runs",
                json={
                    "question": "AOI 发现桥接怎么办？",
                    "top_k": 2,
                    "search_mode": "keyword",
                },
            )

            assert started.status_code == 202, started.text
            body = started.json()
            run_id = body["run_id"]
            assert body["status"] == "pending_approval"
            assert body["approval_required"] is True
            assert body["evidence"][0]["document_title"] == "AOI 桥接处理规范"
            assert body["business_records"][0]["record_id"] == "MES-API-1"
            assert body["business_tool_failures"][0]["kind"] == "unavailable"
            assert body["draft"]["generation_mode"] == "deterministic_fallback"
            assert body["draft"]["citations"] == [body["evidence"][0]["chunk_id"]]
            assert body["draft"]["business_record_references"] == [
                {"tool_name": "mes-reader", "record_id": "MES-API-1"}
            ]
            assert body["final_response"] is None

            fetched = await client.get(f"/api/v1/agent/runs/{run_id}")
            assert fetched.status_code == 200
            assert fetched.json() == body

            app.dependency_overrides[get_approval_principal] = lambda: (
                ApprovalPrincipal(
                    actor_id="authenticated-viewer",
                    roles=frozenset(),
                    authenticated=True,
                    auth_method=API_KEY_AUTH_METHOD,
                )
            )
            forbidden = await client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                json={"approved": True},
            )
            assert forbidden.status_code == 403
            assert audit_store.record is None
            app.dependency_overrides[get_approval_principal] = lambda: principal

            approval_request_id = str(uuid.uuid4())
            approval_payload = {
                "approved": True,
                "actor_id": "spoofed-client-identity",
                "request_id": approval_request_id,
                "comment": "现场工程师已确认",
            }
            approved = await client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                json=approval_payload,
            )
            assert approved.status_code == 200, approved.text
            approved_body = approved.json()
            assert approved_body["status"] == "completed"
            assert approved_body["approval_required"] is False
            assert approved_body["approved"] is True
            assert approved_body["approval_event"]["actor_id"] == (
                "verified-engineer-001"
            )
            assert approved_body["approval_event"]["actor_authenticated"] is True
            assert approved_body["approval_event"]["auth_method"] == (
                "api_key_sha256"
            )
            assert approved_body["approval_event"]["id"] == approval_request_id
            assert approved_body["approval_event"]["approved"] is True
            assert approved_body["final_response"] == approved_body["draft"]

            replayed = await client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                json=approval_payload,
            )
            assert replayed.status_code == 200
            assert replayed.json() == approved_body

            events = await client.get(
                f"/api/v1/agent/runs/{run_id}/audit-events"
            )
            assert events.status_code == 200
            assert events.json() == [approved_body["approval_event"]]

            repeated = await client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                json={"approved": True},
            )
            assert repeated.status_code == 409
    finally:
        app.dependency_overrides.pop(get_quality_agent_workflow, None)
        app.dependency_overrides.pop(
            get_agent_evidence_retriever_builder,
            None,
        )
        app.dependency_overrides.pop(get_read_only_business_tools, None)
        app.dependency_overrides.pop(get_approval_audit_store, None)
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_agent_run_error_store, None)


async def test_agent_api_validates_input_and_returns_missing_run() -> None:
    workflow = QualityAgentWorkflow()
    audit_store = FakeApprovalAuditStore()
    error_store = FakeAgentRunErrorStore()
    app.dependency_overrides[get_quality_agent_workflow] = lambda: workflow
    app.dependency_overrides[get_approval_audit_store] = lambda: audit_store
    app.dependency_overrides[get_agent_run_error_store] = lambda: error_store
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            blank = await client.post(
                "/api/v1/agent/runs",
                json={"question": "   "},
            )
            missing = await client.get(
                f"/api/v1/agent/runs/{uuid.uuid4()}"
            )
            missing_events = await client.get(
                f"/api/v1/agent/runs/{uuid.uuid4()}/audit-events"
            )

        assert blank.status_code == 422
        assert missing.status_code == 404
        assert missing_events.status_code == 404
    finally:
        app.dependency_overrides.pop(get_quality_agent_workflow, None)
        app.dependency_overrides.pop(get_approval_audit_store, None)
        app.dependency_overrides.pop(get_agent_run_error_store, None)


async def test_agent_api_persists_sanitized_node_error_and_returns_run_id() -> None:
    workflow = QualityAgentWorkflow()
    error_store = FakeAgentRunErrorStore()

    def failing_retriever_builder(_db: object, _provider_factory: object):
        async def retrieve(
            _question: str,
            *,
            top_k: int,
            mode: str,
        ) -> list[AgentEvidence]:
            assert top_k == 1
            assert mode == "keyword"
            raise EmbeddingTimeoutError(
                "secret-host=internal.example token=should-not-leak"
            )

        return retrieve

    app.dependency_overrides[get_quality_agent_workflow] = lambda: workflow
    app.dependency_overrides[get_agent_evidence_retriever_builder] = (
        lambda: failing_retriever_builder
    )
    app.dependency_overrides[get_agent_run_error_store] = lambda: error_store
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            failed = await client.post(
                "/api/v1/agent/runs",
                json={
                    "question": "触发检索超时",
                    "top_k": 1,
                    "search_mode": "keyword",
                },
            )
            run_id = failed.headers["X-Agent-Run-Id"]
            errors = await client.get(
                f"/api/v1/agent/runs/{run_id}/errors"
            )

        assert failed.status_code == 503
        assert uuid.UUID(run_id)
        assert "should-not-leak" not in failed.text
        assert errors.status_code == 200
        assert errors.json()[0]["stage"] == "retrieval"
        assert errors.json()[0]["error_kind"] == "embedding_timeout"
        assert errors.json()[0]["message"] == "Embedding 服务调用超时"
        assert errors.json()[0]["retryable"] is True
        assert "should-not-leak" not in str(errors.json())
    finally:
        app.dependency_overrides.pop(get_quality_agent_workflow, None)
        app.dependency_overrides.pop(
            get_agent_evidence_retriever_builder,
            None,
        )
        app.dependency_overrides.pop(get_agent_run_error_store, None)
