import logging
from functools import lru_cache
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.audit import (
    ApprovalAuditConflictError,
    ApprovalAuditRecord,
    ApprovalAuditStore,
    SqlApprovalAuditStore,
)
from app.agent.business_tools import ReadOnlyBusinessTool
from app.agent.drafting import (
    EvidenceBasedDraftGenerator,
    ResilientRecommendationGenerator,
)
from app.agent.protocols import (
    AgentRuntimeContext,
    EvidenceRetriever,
    RecommendationGenerator,
)
from app.agent.retrieval import (
    AgentEvidenceRetrieverBuilder,
    get_agent_evidence_retriever_builder,
)
from app.agent.run_errors import (
    AgentErrorStage,
    AgentRunErrorRecord,
    AgentRunErrorStore,
    SqlAgentRunErrorStore,
    classify_agent_error,
)
from app.agent.security import (
    ApprovalAuthenticationError,
    ApprovalPrincipal,
    OidcJwtAuthenticator,
    authenticate_agent_principal,
    build_oidc_authenticator,
)
from app.agent.workflow import (
    AgentNodeExecutionError,
    AgentRunNotAwaitingApprovalError,
    AgentRunNotFoundError,
    QualityAgentWorkflow,
    get_quality_agent_workflow,
)
from app.api.errors import embedding_http_exception
from app.core.config import Settings, get_settings
from app.db import get_db
from app.schemas import (
    AgentApprovalRequest,
    AgentApprovalAuditResponse,
    AgentRunCreate,
    AgentRunErrorResponse,
    AgentRunResponse,
)
from app.services.embedding.errors import EmbeddingServiceError
from app.services.embedding.workflow import (
    EmbeddingProviderFactory,
    get_embedding_provider_factory,
)
from app.services.generation.factory import create_generation_provider


router = APIRouter(prefix="/agent/runs", tags=["agent"])
logger = logging.getLogger(__name__)


def get_read_only_business_tools() -> tuple[ReadOnlyBusinessTool, ...]:
    """真实 MES/QMS 连接器接入前默认不提供业务工具。"""

    return ()


def get_approval_audit_store(
    db: AsyncSession = Depends(get_db),
) -> ApprovalAuditStore:
    return SqlApprovalAuditStore(db)


@lru_cache
def get_agent_oidc_authenticator() -> OidcJwtAuthenticator | None:
    return build_oidc_authenticator(get_settings())


async def get_approval_principal(
    authorization: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
    oidc_authenticator: OidcJwtAuthenticator | None = Depends(
        get_agent_oidc_authenticator
    ),
) -> ApprovalPrincipal:
    try:
        return await authenticate_agent_principal(
            authorization,
            settings,
            oidc_authenticator,
        )
    except ApprovalAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_agent_run_error_store(
    db: AsyncSession = Depends(get_db),
) -> AgentRunErrorStore:
    return SqlAgentRunErrorStore(db)


def get_recommendation_generator() -> RecommendationGenerator:
    settings = get_settings()
    return ResilientRecommendationGenerator(
        create_generation_provider,
        max_retries=settings.generation_max_retries,
        retry_base_delay_seconds=settings.generation_retry_base_delay_seconds,
    )


def _response(
    state: dict,
    approval_event: ApprovalAuditRecord | None = None,
) -> AgentRunResponse:
    serialized_event = None
    if approval_event is not None:
        serialized_event = {
            "id": approval_event.id,
            "run_id": approval_event.run_id,
            "event_type": "approval_decision",
            "actor_id": approval_event.actor_id,
            "actor_authenticated": approval_event.actor_authenticated,
            "auth_method": approval_event.auth_method,
            "approved": approval_event.approved,
            "comment": approval_event.comment,
            "occurred_at": approval_event.occurred_at,
        }
    return AgentRunResponse.model_validate(
        {
            **state,
            "approval_required": state.get("status") == "pending_approval",
            "approval_event": serialized_event,
        }
    )


@router.post(
    "",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_agent_run(
    payload: AgentRunCreate,
    workflow: Annotated[QualityAgentWorkflow, Depends(get_quality_agent_workflow)],
    retriever_builder: Annotated[
        AgentEvidenceRetrieverBuilder,
        Depends(get_agent_evidence_retriever_builder),
    ],
    generator: Annotated[
        RecommendationGenerator,
        Depends(get_recommendation_generator),
    ],
    provider_factory: Annotated[
        EmbeddingProviderFactory,
        Depends(get_embedding_provider_factory),
    ],
    business_tools: Annotated[
        tuple[ReadOnlyBusinessTool, ...],
        Depends(get_read_only_business_tools),
    ],
    error_store: Annotated[
        AgentRunErrorStore,
        Depends(get_agent_run_error_store),
    ],
    db: AsyncSession = Depends(get_db),
) -> AgentRunResponse:
    run_id = str(uuid.uuid4())
    retriever: EvidenceRetriever = retriever_builder(db, provider_factory)
    settings = get_settings()
    context = AgentRuntimeContext(
        retriever=retriever,
        generator=generator if payload.use_model else EvidenceBasedDraftGenerator(),
        business_tools=business_tools,
        business_tool_limit=settings.agent_business_tool_limit,
        business_tool_timeout_seconds=settings.agent_business_tool_timeout_seconds,
    )
    try:
        result = await workflow.start(
            {
                "run_id": run_id,
                "question": payload.question,
                "search_mode": payload.search_mode.value,
                "top_k": payload.top_k,
                "status": "created",
                "evidence": [],
                "business_records": [],
                "business_tool_failures": [],
                "final_response": None,
            },
            context,
        )
    except AgentNodeExecutionError as exc:
        await _record_error_safely(
            error_store,
            run_id=uuid.UUID(run_id),
            stage=exc.stage,
            exc=exc.cause,
        )
        if isinstance(exc.cause, EmbeddingServiceError):
            http_error = embedding_http_exception(exc.cause)
            http_error.headers = {
                **(http_error.headers or {}),
                "X-Agent-Run-Id": run_id,
            }
            raise http_error from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent 节点执行失败",
            headers={"X-Agent-Run-Id": run_id},
        ) from exc
    except Exception as exc:
        await _record_error_safely(
            error_store,
            run_id=uuid.UUID(run_id),
            stage="workflow",
            exc=exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent 工作流执行失败",
            headers={"X-Agent-Run-Id": run_id},
        ) from exc
    return _response(result)


@router.get("/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: uuid.UUID,
    workflow: Annotated[QualityAgentWorkflow, Depends(get_quality_agent_workflow)],
    audit_store: Annotated[
        ApprovalAuditStore,
        Depends(get_approval_audit_store),
    ],
) -> AgentRunResponse:
    result = await workflow.get(str(run_id))
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent 运行记录不存在",
        )
    approval_event = await audit_store.get_for_run(run_id)
    return _response(result, approval_event)


@router.get(
    "/{run_id}/audit-events",
    response_model=list[AgentApprovalAuditResponse],
)
async def list_agent_audit_events(
    run_id: uuid.UUID,
    workflow: Annotated[QualityAgentWorkflow, Depends(get_quality_agent_workflow)],
    audit_store: Annotated[
        ApprovalAuditStore,
        Depends(get_approval_audit_store),
    ],
) -> list[AgentApprovalAuditResponse]:
    approval_event = await audit_store.get_for_run(run_id)
    if approval_event is not None:
        return [
            AgentApprovalAuditResponse(
                id=approval_event.id,
                run_id=approval_event.run_id,
                actor_id=approval_event.actor_id,
                actor_authenticated=approval_event.actor_authenticated,
                auth_method=approval_event.auth_method,
                approved=approval_event.approved,
                comment=approval_event.comment,
                occurred_at=approval_event.occurred_at,
            )
        ]
    if await workflow.get(str(run_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent 运行记录不存在",
        )
    return []


@router.get(
    "/{run_id}/errors",
    response_model=list[AgentRunErrorResponse],
)
async def list_agent_run_errors(
    run_id: uuid.UUID,
    workflow: Annotated[QualityAgentWorkflow, Depends(get_quality_agent_workflow)],
    error_store: Annotated[
        AgentRunErrorStore,
        Depends(get_agent_run_error_store),
    ],
) -> list[AgentRunErrorResponse]:
    errors = await error_store.list_for_run(run_id)
    if errors:
        return [AgentRunErrorResponse.model_validate(error) for error in errors]
    if await workflow.get(str(run_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent 运行记录不存在",
        )
    return []


@router.post("/{run_id}/approval", response_model=AgentRunResponse)
async def approve_agent_run(
    run_id: uuid.UUID,
    payload: AgentApprovalRequest,
    workflow: Annotated[QualityAgentWorkflow, Depends(get_quality_agent_workflow)],
    audit_store: Annotated[
        ApprovalAuditStore,
        Depends(get_approval_audit_store),
    ],
    principal: Annotated[
        ApprovalPrincipal,
        Depends(get_approval_principal),
    ],
) -> AgentRunResponse:
    if principal.authenticated and not principal.can_approve:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前认证主体没有质量审批角色",
        )
    actor_id = principal.actor_id if principal.authenticated else payload.actor_id
    current = await workflow.get(str(run_id))
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent 运行记录不存在",
        )
    existing_event = await audit_store.get_for_run(run_id)
    if current.get("status") != "pending_approval":
        if _is_idempotent_replay(
            current,
            existing_event,
            payload,
            actor_id=actor_id,
            principal=principal,
        ):
            return _response(current, existing_event)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent 运行当前不等待审批",
        )
    try:
        approval_event, _ = await audit_store.append_decision(
            event_id=payload.request_id,
            run_id=run_id,
            actor_id=actor_id,
            actor_authenticated=principal.authenticated,
            auth_method=principal.auth_method,
            approved=payload.approved,
            comment=payload.comment,
        )
    except ApprovalAuditConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    try:
        result = await workflow.approve(
            str(run_id),
            approved=payload.approved,
            comment=payload.comment,
        )
    except AgentRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except AgentRunNotAwaitingApprovalError as exc:
        current = await workflow.get(str(run_id))
        if current is not None and _is_idempotent_replay(
            current,
            approval_event,
            payload,
            actor_id=actor_id,
            principal=principal,
        ):
            return _response(current, approval_event)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return _response(result, approval_event)


def _is_idempotent_replay(
    state: dict,
    event: ApprovalAuditRecord | None,
    payload: AgentApprovalRequest,
    *,
    actor_id: str,
    principal: ApprovalPrincipal,
) -> bool:
    return (
        event is not None
        and event.id == payload.request_id
        and event.actor_id == actor_id
        and event.actor_authenticated is principal.authenticated
        and event.auth_method == principal.auth_method
        and event.approved is payload.approved
        and event.comment == payload.comment
        and state.get("approved") is payload.approved
    )


async def _record_error_safely(
    store: AgentRunErrorStore,
    *,
    run_id: uuid.UUID,
    stage: AgentErrorStage,
    exc: Exception,
) -> AgentRunErrorRecord | None:
    try:
        return await store.append(
            run_id=run_id,
            stage=stage,
            error=classify_agent_error(exc),
        )
    except Exception as store_exc:
        logger.error(
            "Agent 错误历史写入失败 run_id=%s stage=%s error_type=%s",
            run_id,
            stage,
            type(store_exc).__name__,
        )
        return None
