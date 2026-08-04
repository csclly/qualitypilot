import uuid

import pytest

from app.agent.protocols import AgentRuntimeContext
from app.agent.state import AgentEvidence, AgentRecommendation
from app.agent.workflow import (
    AgentRunNotAwaitingApprovalError,
    QualityAgentWorkflow,
)


def _evidence() -> list[AgentEvidence]:
    return [
        {
            "chunk_id": str(uuid.uuid4()),
            "document_id": str(uuid.uuid4()),
            "document_title": "回流焊桥接处理规范",
            "source_uri": "upload://bridge.md",
            "original_filename": "桥接规范.md",
            "chunk_index": 0,
            "content": "连续桥接应检查钢网开口、锡膏印刷参数和回流曲线。",
            "score": 0.92,
            "match_type": "hybrid",
            "vector_score": 0.87,
            "keyword_score": 0.95,
        }
    ]


class FakeGenerator:
    async def generate(
        self,
        question: str,
        evidence: list[AgentEvidence],
    ) -> AgentRecommendation:
        return {
            "summary": f"问题：{question}；证据数：{len(evidence)}",
            "suggested_actions": ["检查钢网开口"],
            "risk_notes": ["需要人工确认"],
        }


def _context() -> AgentRuntimeContext:
    async def retrieve(
        _question: str,
        *,
        top_k: int,
        mode: str,
    ) -> list[AgentEvidence]:
        assert top_k == 3
        assert mode == "hybrid"
        return _evidence()

    return AgentRuntimeContext(retriever=retrieve, generator=FakeGenerator())


async def test_agent_workflow_pauses_then_resumes_after_approval() -> None:
    workflow = QualityAgentWorkflow()
    run_id = str(uuid.uuid4())

    pending = await workflow.start(
        {
            "run_id": run_id,
            "question": "回流焊连续桥接应该检查什么？",
            "search_mode": "hybrid",
            "top_k": 3,
            "status": "created",
            "evidence": [],
            "final_response": None,
        },
        _context(),
    )

    assert pending["status"] == "pending_approval"
    assert len(pending["evidence"]) == 1
    assert pending["draft"]["suggested_actions"] == ["检查钢网开口"]
    assert "approved" not in pending

    completed = await workflow.approve(
        run_id,
        approved=True,
        comment="已结合现场数据确认",
    )

    assert completed["status"] == "completed"
    assert completed["approved"] is True
    assert completed["approval_comment"] == "已结合现场数据确认"
    assert completed["final_response"] == completed["draft"]

    stored = await workflow.get(run_id)
    assert stored is not None
    assert stored["status"] == "completed"


async def test_agent_workflow_rejection_does_not_publish_draft() -> None:
    workflow = QualityAgentWorkflow()
    run_id = str(uuid.uuid4())
    await workflow.start(
        {
            "run_id": run_id,
            "question": "回流焊连续桥接应该检查什么？",
            "search_mode": "hybrid",
            "top_k": 3,
            "status": "created",
            "evidence": [],
            "final_response": None,
        },
        _context(),
    )

    rejected = await workflow.approve(
        run_id,
        approved=False,
        comment="证据不足",
    )

    assert rejected["status"] == "rejected"
    assert rejected["approved"] is False
    assert rejected["final_response"] is None

    with pytest.raises(AgentRunNotAwaitingApprovalError):
        await workflow.approve(run_id, approved=True, comment=None)
