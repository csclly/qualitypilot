from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.drafting import ResilientRecommendationGenerator
from app.agent.protocols import (
    AgentRuntimeContext,
    EvidenceRetriever,
    RecommendationGenerator,
)
from app.agent.retrieval import (
    AgentEvidenceRetrieverBuilder,
    get_agent_evidence_retriever_builder,
)
from app.agent.workflow import (
    AgentRunNotAwaitingApprovalError,
    AgentRunNotFoundError,
    QualityAgentWorkflow,
    get_quality_agent_workflow,
)
from app.api.errors import embedding_http_exception
from app.core.config import get_settings
from app.db import get_db
from app.schemas import (
    AgentApprovalRequest,
    AgentRunCreate,
    AgentRunResponse,
)
from app.services.embedding.errors import EmbeddingServiceError
from app.services.embedding.workflow import (
    EmbeddingProviderFactory,
    get_embedding_provider_factory,
)
from app.services.generation.factory import create_generation_provider


router = APIRouter(prefix="/agent/runs", tags=["agent"])


def get_recommendation_generator() -> RecommendationGenerator:
    settings = get_settings()
    return ResilientRecommendationGenerator(
        create_generation_provider,
        max_retries=settings.generation_max_retries,
        retry_base_delay_seconds=settings.generation_retry_base_delay_seconds,
    )


def _response(state: dict) -> AgentRunResponse:
    return AgentRunResponse.model_validate(
        {
            **state,
            "approval_required": state.get("status") == "pending_approval",
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
    db: AsyncSession = Depends(get_db),
) -> AgentRunResponse:
    run_id = str(uuid.uuid4())
    retriever: EvidenceRetriever = retriever_builder(db, provider_factory)
    context = AgentRuntimeContext(retriever=retriever, generator=generator)
    try:
        result = await workflow.start(
            {
                "run_id": run_id,
                "question": payload.question,
                "search_mode": payload.search_mode.value,
                "top_k": payload.top_k,
                "status": "created",
                "evidence": [],
                "final_response": None,
            },
            context,
        )
    except EmbeddingServiceError as exc:
        raise embedding_http_exception(exc) from exc
    return _response(result)


@router.get("/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: uuid.UUID,
    workflow: Annotated[QualityAgentWorkflow, Depends(get_quality_agent_workflow)],
) -> AgentRunResponse:
    result = await workflow.get(str(run_id))
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent 运行记录不存在",
        )
    return _response(result)


@router.post("/{run_id}/approval", response_model=AgentRunResponse)
async def approve_agent_run(
    run_id: uuid.UUID,
    payload: AgentApprovalRequest,
    workflow: Annotated[QualityAgentWorkflow, Depends(get_quality_agent_workflow)],
) -> AgentRunResponse:
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return _response(result)
