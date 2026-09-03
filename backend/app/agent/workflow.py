from fastapi import Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from app.agent.business_tools import (
    collect_business_context,
    to_agent_business_context,
)
from app.agent.protocols import AgentRuntimeContext
from app.agent.run_errors import AgentErrorStage
from app.agent.state import QualityAgentState


class AgentRunNotFoundError(Exception):
    pass


class AgentRunNotAwaitingApprovalError(Exception):
    pass


class AgentNodeExecutionError(RuntimeError):
    def __init__(self, stage: AgentErrorStage, cause: Exception) -> None:
        super().__init__(f"Agent 节点执行失败：{stage}")
        self.stage = stage
        self.cause = cause


async def _retrieve_evidence(
    state: QualityAgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> QualityAgentState:
    try:
        evidence = await runtime.context.retriever(
            state["question"],
            top_k=state["top_k"],
            mode=state["search_mode"],
        )
    except Exception as exc:
        raise AgentNodeExecutionError("retrieval", exc) from exc
    return {"status": "querying_business_context", "evidence": evidence}


async def _query_business_context(
    state: QualityAgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> QualityAgentState:
    try:
        results = await collect_business_context(
            state["question"],
            runtime.context.business_tools,
            limit_per_tool=runtime.context.business_tool_limit,
            timeout_seconds=runtime.context.business_tool_timeout_seconds,
        )
    except Exception as exc:
        raise AgentNodeExecutionError("business_context", exc) from exc
    records, failures = to_agent_business_context(results)
    return {
        "status": "drafting",
        "business_records": records,
        "business_tool_failures": failures,
    }


async def _draft_recommendation(
    state: QualityAgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> QualityAgentState:
    try:
        draft = await runtime.context.generator.generate(
            state["question"],
            state["evidence"],
            state.get("business_records", []),
            state.get("business_tool_failures", []),
        )
    except Exception as exc:
        raise AgentNodeExecutionError("drafting", exc) from exc
    return {"status": "pending_approval", "draft": draft}


def _request_approval(state: QualityAgentState) -> QualityAgentState:
    decision = interrupt(
        {
            "type": "quality_recommendation_approval",
            "run_id": state["run_id"],
            "question": state["question"],
            "evidence_count": len(state["evidence"]),
            "business_record_count": len(state.get("business_records", [])),
            "business_tool_failure_count": len(
                state.get("business_tool_failures", [])
            ),
            "draft": state["draft"],
        }
    )
    return {
        "approved": bool(decision["approved"]),
        "approval_comment": decision.get("comment"),
    }


def _route_approval(state: QualityAgentState) -> str:
    return "approved" if state["approved"] else "rejected"


def _finalize_approved(state: QualityAgentState) -> QualityAgentState:
    return {
        "status": "completed",
        "final_response": state["draft"],
    }


def _finalize_rejected(_: QualityAgentState) -> QualityAgentState:
    return {"status": "rejected", "final_response": None}


def _build_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(
        QualityAgentState,
        context_schema=AgentRuntimeContext,
    )
    builder.add_node("retrieve_evidence", _retrieve_evidence)
    builder.add_node("query_business_context", _query_business_context)
    builder.add_node("draft_recommendation", _draft_recommendation)
    builder.add_node("request_approval", _request_approval)
    builder.add_node("finalize_approved", _finalize_approved)
    builder.add_node("finalize_rejected", _finalize_rejected)
    builder.add_edge(START, "retrieve_evidence")
    builder.add_edge("retrieve_evidence", "query_business_context")
    builder.add_edge("query_business_context", "draft_recommendation")
    builder.add_edge("draft_recommendation", "request_approval")
    builder.add_conditional_edges(
        "request_approval",
        _route_approval,
        {
            "approved": "finalize_approved",
            "rejected": "finalize_rejected",
        },
    )
    builder.add_edge("finalize_approved", END)
    builder.add_edge("finalize_rejected", END)
    return builder.compile(checkpointer=checkpointer)


class QualityAgentWorkflow:
    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        saver = checkpointer if checkpointer is not None else InMemorySaver()
        self._graph = _build_graph(saver)

    @staticmethod
    def _config(run_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": run_id}}

    async def start(
        self,
        state: QualityAgentState,
        context: AgentRuntimeContext,
    ) -> QualityAgentState:
        result = await self._graph.ainvoke(
            state,
            self._config(state["run_id"]),
            context=context,
        )
        return self._strip_internal_fields(result)

    async def get(self, run_id: str) -> QualityAgentState | None:
        snapshot = await self._graph.aget_state(self._config(run_id))
        if not snapshot.values:
            return None
        return self._strip_internal_fields(snapshot.values)

    async def approve(
        self,
        run_id: str,
        *,
        approved: bool,
        comment: str | None,
    ) -> QualityAgentState:
        current = await self.get(run_id)
        if current is None:
            raise AgentRunNotFoundError("Agent 运行记录不存在")
        if current.get("status") != "pending_approval":
            raise AgentRunNotAwaitingApprovalError("Agent 当前不处于待审批状态")
        result = await self._graph.ainvoke(
            Command(resume={"approved": approved, "comment": comment}),
            self._config(run_id),
        )
        return self._strip_internal_fields(result)

    @staticmethod
    def _strip_internal_fields(state: dict) -> QualityAgentState:
        return {
            key: value
            for key, value in state.items()
            if not key.startswith("__")
        }


def get_quality_agent_workflow(request: Request) -> QualityAgentWorkflow:
    workflow = getattr(request.app.state, "quality_agent_workflow", None)
    if workflow is None:
        raise RuntimeError("Agent 工作流尚未完成应用生命周期初始化")
    return workflow
