import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.drafting import EvidenceBasedDraftGenerator
from app.agent.retrieval import get_agent_evidence_retriever_builder
from app.agent.workflow import QualityAgentWorkflow, get_quality_agent_workflow
from app.api.routes.agent import get_agent_run_error_store, get_recommendation_generator
from app.main import app
from test_agent_api import FakeAgentRunErrorStore, _fake_retriever_builder


@pytest.mark.parametrize("use_model,expected_calls", [(False, 0), (True, 1), (None, 1)])
async def test_explicit_rules_bypass_generator_and_preserve_default(
    monkeypatch, use_model, expected_calls,
):
    calls = 0

    class RecordingGenerator:
        async def generate(self, question, evidence, records, failures):
            nonlocal calls
            calls += 1
            return await EvidenceBasedDraftGenerator().generate(
                question, evidence, records, failures,
            )

    workflow = QualityAgentWorkflow()
    errors = FakeAgentRunErrorStore()
    generator = RecordingGenerator()
    for dependency, replacement in [
        (get_quality_agent_workflow, lambda: workflow),
        (get_agent_evidence_retriever_builder, lambda: _fake_retriever_builder),
        (get_agent_run_error_store, lambda: errors),
        (get_recommendation_generator, lambda: generator),
    ]:
        monkeypatch.setitem(app.dependency_overrides, dependency, replacement)
    payload = {"question": "AOI桥连如何复核？", "search_mode": "keyword", "top_k": 2}
    if use_model is not None:
        payload["use_model"] = use_model
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/agent/runs", json=payload)
    assert response.status_code == 202, response.text
    body = response.json()
    assert calls == expected_calls
    assert body["draft"]["generation_mode"] == "deterministic_fallback"
    assert body["draft"]["citations"] == [body["evidence"][0]["chunk_id"]]
    assert body["approval_required"] is True
    assert body["status"] == "pending_approval"
    assert body["final_response"] is None
