from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.metrics import (
    AgentMetricsStore,
    SqlAgentMetricsStore,
)
from app.agent.security import ApprovalPrincipal
from app.api.routes.agent import get_approval_principal
from app.core.config import get_settings
from app.db import get_db
from app.schemas import AgentMetricsResponse


router = APIRouter(prefix="/agent/metrics", tags=["agent-metrics"])


def get_agent_metrics_store(
    db: AsyncSession = Depends(get_db),
) -> AgentMetricsStore:
    return SqlAgentMetricsStore(db)


@router.get("", response_model=AgentMetricsResponse)
async def get_agent_metrics(
    principal: Annotated[ApprovalPrincipal, Depends(get_approval_principal)],
    store: Annotated[AgentMetricsStore, Depends(get_agent_metrics_store)],
    window_hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> AgentMetricsResponse:
    if not principal.authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent 指标需要已认证的运维主体",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not principal.can_view_alerts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前认证主体没有 Agent 指标读取权限",
        )
    snapshot = await store.snapshot(
        window_hours=window_hours,
        alert_threshold=get_settings().agent_error_alert_threshold,
    )
    return AgentMetricsResponse.model_validate(snapshot)
