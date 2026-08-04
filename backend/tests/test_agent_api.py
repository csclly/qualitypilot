import uuid

from httpx import ASGITransport, AsyncClient

from app.agent.retrieval import get_agent_evidence_retriever_builder
from app.agent.state import AgentEvidence
from app.agent.workflow import QualityAgentWorkflow, get_quality_agent_workflow
from app.main import app


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


async def test_agent_api_start_get_and_approve() -> None:
    workflow = QualityAgentWorkflow()
    app.dependency_overrides[get_quality_agent_workflow] = lambda: workflow
    app.dependency_overrides[get_agent_evidence_retriever_builder] = (
        lambda: _fake_retriever_builder
    )
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
            assert body["final_response"] is None

            fetched = await client.get(f"/api/v1/agent/runs/{run_id}")
            assert fetched.status_code == 200
            assert fetched.json() == body

            approved = await client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                json={"approved": True, "comment": "现场工程师已确认"},
            )
            assert approved.status_code == 200, approved.text
            approved_body = approved.json()
            assert approved_body["status"] == "completed"
            assert approved_body["approval_required"] is False
            assert approved_body["approved"] is True
            assert approved_body["final_response"] == approved_body["draft"]

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


async def test_agent_api_validates_input_and_returns_missing_run() -> None:
    workflow = QualityAgentWorkflow()
    app.dependency_overrides[get_quality_agent_workflow] = lambda: workflow
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

        assert blank.status_code == 422
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(get_quality_agent_workflow, None)
