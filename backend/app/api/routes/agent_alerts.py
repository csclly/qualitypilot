from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.alerts import AgentAlertOutboxStore, SqlAgentAlertOutboxStore
from app.agent.alert_delivery import (
    AgentAlertDeliveryProvider,
    AgentAlertDeliveryService,
    WebhookAgentAlertDeliveryProvider,
)
from app.agent.alert_scheduler import (
    AgentAlertScheduler,
    disabled_alert_scheduler_status,
)
from app.agent.metrics import AgentMetricsStore, SqlAgentMetricsStore
from app.agent.security import ApprovalPrincipal
from app.api.routes.agent import get_approval_principal
from app.core.config import get_settings
from app.db import get_db
from app.schemas import (
    AgentAlertEvaluationResponse,
    AgentAlertDeliveryResultResponse,
    AgentAlertOutboxResponse,
    AgentAlertProcessingResponse,
    AgentAlertSchedulerStatusResponse,
    AgentMetricsResponse,
)


router = APIRouter(prefix="/agent/alerts", tags=["agent-alerts"])


def get_alert_metrics_store(
    db: AsyncSession = Depends(get_db),
) -> AgentMetricsStore:
    return SqlAgentMetricsStore(db)


def get_alert_outbox_store(
    db: AsyncSession = Depends(get_db),
) -> AgentAlertOutboxStore:
    return SqlAgentAlertOutboxStore(db)


async def get_alert_delivery_provider(
) -> AsyncIterator[AgentAlertDeliveryProvider | None]:
    settings = get_settings()
    if settings.agent_alert_webhook_url is None:
        yield None
        return
    try:
        provider = WebhookAgentAlertDeliveryProvider(
            webhook_url=settings.agent_alert_webhook_url.get_secret_value(),
            bearer_token=(
                settings.agent_alert_webhook_bearer_token.get_secret_value()
                if settings.agent_alert_webhook_bearer_token is not None
                else None
            ),
            timeout_seconds=settings.agent_alert_delivery_timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="告警 Webhook 配置无效",
        ) from exc
    try:
        yield provider
    finally:
        await provider.aclose()


@router.post("/evaluate", response_model=AgentAlertEvaluationResponse)
async def evaluate_agent_alert(
    principal: Annotated[ApprovalPrincipal, Depends(get_approval_principal)],
    metrics_store: Annotated[
        AgentMetricsStore,
        Depends(get_alert_metrics_store),
    ],
    outbox_store: Annotated[
        AgentAlertOutboxStore,
        Depends(get_alert_outbox_store),
    ],
    window_hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> AgentAlertEvaluationResponse:
    if not principal.authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="告警评估需要已认证的运维主体",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not principal.can_operate_alerts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前认证主体没有告警评估权限",
        )

    settings = get_settings()
    snapshot = await metrics_store.snapshot(
        window_hours=window_hours,
        alert_threshold=settings.agent_error_alert_threshold,
    )
    metrics_response = AgentMetricsResponse.model_validate(snapshot)
    if snapshot.alert_status != "warning":
        return AgentAlertEvaluationResponse(
            triggered=False,
            queued=False,
            metrics=metrics_response,
        )

    alert, created = await outbox_store.enqueue(snapshot)
    return AgentAlertEvaluationResponse(
        triggered=True,
        queued=created,
        metrics=metrics_response,
        alert=AgentAlertOutboxResponse.model_validate(alert),
    )


@router.post("/process", response_model=AgentAlertProcessingResponse)
async def process_agent_alerts(
    principal: Annotated[ApprovalPrincipal, Depends(get_approval_principal)],
    outbox_store: Annotated[
        AgentAlertOutboxStore,
        Depends(get_alert_outbox_store),
    ],
    delivery_provider: Annotated[
        AgentAlertDeliveryProvider | None,
        Depends(get_alert_delivery_provider),
    ],
    limit: Annotated[int, Query(ge=1, le=10)] = 1,
) -> AgentAlertProcessingResponse:
    if not principal.authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="告警投递需要已认证的运维主体",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not principal.can_operate_alerts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前认证主体没有告警投递权限",
        )
    if delivery_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="尚未配置告警 Webhook",
        )

    settings = get_settings()
    service = AgentAlertDeliveryService(
        store=outbox_store,
        provider=delivery_provider,
        lease_seconds=settings.agent_alert_lease_seconds,
        max_attempts=settings.agent_alert_max_attempts,
        retry_base_delay_seconds=(
            settings.agent_alert_retry_base_delay_seconds
        ),
        retry_max_delay_seconds=settings.agent_alert_retry_max_delay_seconds,
    )
    results: list[AgentAlertDeliveryResultResponse] = []
    for _ in range(limit):
        result = await service.process_one()
        if result.outcome == "idle":
            break
        results.append(AgentAlertDeliveryResultResponse.model_validate(result))

    return AgentAlertProcessingResponse(
        processed=len(results),
        delivered=sum(item.outcome == "delivered" for item in results),
        retry_scheduled=sum(
            item.outcome == "retry_scheduled" for item in results
        ),
        failed=sum(item.outcome == "failed" for item in results),
        results=results,
    )


@router.get(
    "/scheduler",
    response_model=AgentAlertSchedulerStatusResponse,
)
async def get_agent_alert_scheduler_status(
    request: Request,
    principal: Annotated[ApprovalPrincipal, Depends(get_approval_principal)],
) -> AgentAlertSchedulerStatusResponse:
    if not principal.authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="告警调度状态需要已认证的运维主体",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not principal.can_view_alerts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前认证主体没有告警调度状态查询权限",
        )
    scheduler: AgentAlertScheduler | None = getattr(
        request.app.state,
        "agent_alert_scheduler",
        None,
    )
    snapshot = (
        scheduler.snapshot()
        if scheduler is not None
        else disabled_alert_scheduler_status()
    )
    return AgentAlertSchedulerStatusResponse.model_validate(snapshot)
