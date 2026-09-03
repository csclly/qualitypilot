import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.agent.audit import (
    ApprovalAuditConflictError,
    SqlApprovalAuditStore,
)
from app.agent.drafting import EvidenceBasedDraftGenerator
from app.agent.protocols import AgentRuntimeContext
from app.agent.state import AgentEvidence
from app.agent.workflow import QualityAgentWorkflow, get_quality_agent_workflow
from app.db import SessionLocal
from app.main import app


@pytest.mark.integration
async def test_sql_audit_store_is_idempotent_and_rejects_conflict() -> None:
    run_id = uuid.uuid4()
    event_id = uuid.uuid4()
    async with SessionLocal() as session:
        store = SqlApprovalAuditStore(session)
        created, was_created = await store.append_decision(
            event_id=event_id,
            run_id=run_id,
            actor_id="engineer-001",
            actor_authenticated=True,
            auth_method="api_key_sha256",
            approved=True,
            comment="确认执行",
        )
        replayed, replay_created = await store.append_decision(
            event_id=event_id,
            run_id=run_id,
            actor_id="engineer-001",
            actor_authenticated=True,
            auth_method="api_key_sha256",
            approved=True,
            comment="确认执行",
        )

        assert was_created is True
        assert replay_created is False
        assert replayed == created
        assert created.actor_authenticated is True
        assert created.auth_method == "api_key_sha256"

        with pytest.raises(ApprovalAuditConflictError):
            await store.append_decision(
                event_id=uuid.uuid4(),
                run_id=run_id,
                actor_id="engineer-002",
                actor_authenticated=False,
                auth_method=None,
                approved=False,
                comment="拒绝执行",
            )


@pytest.mark.integration
async def test_database_rejects_audit_event_mutations_and_rolls_back_fixture() -> None:
    event_id = uuid.uuid4()
    run_id = uuid.uuid4()
    async with SessionLocal() as session:
        transaction = await session.begin()
        invalid_savepoint = await session.begin_nested()
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    """
                    INSERT INTO agent_audit_events
                        (id, run_id, event_type, actor_id, actor_authenticated,
                         auth_method, approved, comment)
                    VALUES
                        (:id, :run_id, 'approval_decision', 'invalid-actor',
                         true, null, true, null)
                    """
                ),
                {"id": uuid.uuid4(), "run_id": uuid.uuid4()},
            )
        await invalid_savepoint.rollback()
        await session.execute(
            text(
                """
                INSERT INTO agent_audit_events
                    (id, run_id, event_type, actor_id, approved, comment)
                VALUES
                    (:id, :run_id, 'approval_decision', 'engineer-immutable', true, null)
                """
            ),
            {"id": event_id, "run_id": run_id},
        )
        mutations = (
            "UPDATE agent_audit_events SET approved = false WHERE id = :id",
            "DELETE FROM agent_audit_events WHERE id = :id",
            "TRUNCATE TABLE agent_audit_events",
        )
        for statement in mutations:
            savepoint = await session.begin_nested()
            with pytest.raises(DBAPIError, match="immutable"):
                parameters = {"id": event_id} if ":id" in statement else None
                await session.execute(text(statement), parameters)
            await savepoint.rollback()
        await transaction.rollback()


@pytest.mark.integration
async def test_approval_api_persists_audit_event_in_postgresql() -> None:
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    workflow = QualityAgentWorkflow()

    async def retrieve(
        _question: str,
        *,
        top_k: int,
        mode: str,
    ) -> list[AgentEvidence]:
        assert top_k == 1
        assert mode == "keyword"
        return []

    await workflow.start(
        {
            "run_id": str(run_id),
            "question": "是否批准测试草稿？",
            "search_mode": "keyword",
            "top_k": 1,
            "status": "created",
            "evidence": [],
            "business_records": [],
            "business_tool_failures": [],
            "final_response": None,
        },
        AgentRuntimeContext(
            retriever=retrieve,
            generator=EvidenceBasedDraftGenerator(),
        ),
    )
    app.dependency_overrides[get_quality_agent_workflow] = lambda: workflow
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                json={
                    "approved": False,
                    "actor_id": "integration-engineer",
                    "request_id": str(request_id),
                    "comment": "集成测试拒绝",
                },
            )
            events = await client.get(
                f"/api/v1/agent/runs/{run_id}/audit-events"
            )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "rejected"
        assert response.json()["approval_event"]["id"] == str(request_id)
        assert response.json()["approval_event"]["actor_authenticated"] is False
        assert response.json()["approval_event"]["auth_method"] is None
        assert events.status_code == 200
        assert events.json()[0]["actor_id"] == "integration-engineer"
    finally:
        app.dependency_overrides.pop(get_quality_agent_workflow, None)
