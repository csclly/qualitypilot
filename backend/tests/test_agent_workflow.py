import uuid

import pytest

from app.agent.business_tools import (
    BusinessRecord,
    BusinessSystem,
    BusinessToolUnavailableError,
    InMemoryReadOnlyBusinessTool,
)
from app.agent.protocols import AgentRuntimeContext
from app.agent.state import (
    AgentBusinessRecord,
    AgentBusinessToolFailure,
    AgentEvidence,
    AgentRecommendation,
)
from app.agent.workflow import (
    AgentNodeExecutionError,
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
        business_records: list[AgentBusinessRecord],
        business_tool_failures: list[AgentBusinessToolFailure],
    ) -> AgentRecommendation:
        return {
            "summary": (
                f"问题：{question}；证据数：{len(evidence)}；"
                f"业务记录：{len(business_records)}"
            ),
            "suggested_actions": ["检查钢网开口"],
            "risk_notes": ["需要人工确认"],
            "citations": [evidence[0]["chunk_id"]],
            "business_record_references": [
                {
                    "tool_name": item["tool_name"],
                    "record_id": item["record_id"],
                }
                for item in business_records
            ],
            "generation_mode": "model",
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

    question = "回流焊连续桥接应该检查什么？"
    mes = InMemoryReadOnlyBusinessTool(
        name="mes-reader",
        system=BusinessSystem.MES,
        responses={
            question: [
                BusinessRecord(
                    system=BusinessSystem.MES,
                    record_id="MES-001",
                    record_type="batch_status",
                    summary="批次已暂停",
                )
            ]
        },
    )
    qms = InMemoryReadOnlyBusinessTool(
        name="qms-reader",
        system=BusinessSystem.QMS,
        error=BusinessToolUnavailableError(),
    )
    return AgentRuntimeContext(
        retriever=retrieve,
        generator=FakeGenerator(),
        business_tools=(mes, qms),
        business_tool_timeout_seconds=0.1,
    )


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
    assert pending["business_records"][0]["record_id"] == "MES-001"
    assert pending["business_tool_failures"][0]["kind"] == "unavailable"
    assert pending["draft"]["suggested_actions"] == ["检查钢网开口"]
    assert pending["draft"]["business_record_references"] == [
        {"tool_name": "mes-reader", "record_id": "MES-001"}
    ]
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


async def test_agent_workflow_tags_business_context_failure() -> None:
    async def retrieve(
        _question: str,
        *,
        top_k: int,
        mode: str,
    ) -> list[AgentEvidence]:
        return _evidence()

    workflow = QualityAgentWorkflow()
    with pytest.raises(AgentNodeExecutionError) as caught:
        await workflow.start(
            {
                "run_id": str(uuid.uuid4()),
                "question": "业务上下文错误",
                "search_mode": "keyword",
                "top_k": 1,
                "status": "created",
                "evidence": [],
                "final_response": None,
            },
            AgentRuntimeContext(
                retriever=retrieve,
                generator=FakeGenerator(),
                business_tool_timeout_seconds=0,
            ),
        )

    assert caught.value.stage == "business_context"
    assert isinstance(caught.value.cause, ValueError)


async def test_agent_workflow_tags_unexpected_drafting_failure() -> None:
    async def retrieve(
        _question: str,
        *,
        top_k: int,
        mode: str,
    ) -> list[AgentEvidence]:
        return _evidence()

    class FailingGenerator:
        async def generate(
            self,
            question: str,
            evidence: list[AgentEvidence],
            business_records: list[AgentBusinessRecord],
            business_tool_failures: list[AgentBusinessToolFailure],
        ) -> AgentRecommendation:
            raise RuntimeError("sensitive drafting failure")

    workflow = QualityAgentWorkflow()
    with pytest.raises(AgentNodeExecutionError) as caught:
        await workflow.start(
            {
                "run_id": str(uuid.uuid4()),
                "question": "草稿错误",
                "search_mode": "keyword",
                "top_k": 1,
                "status": "created",
                "evidence": [],
                "final_response": None,
            },
            AgentRuntimeContext(
                retriever=retrieve,
                generator=FailingGenerator(),
            ),
        )

    assert caught.value.stage == "drafting"
    assert isinstance(caught.value.cause, RuntimeError)
