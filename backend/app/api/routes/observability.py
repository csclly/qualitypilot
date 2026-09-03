from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.agent.security import ApprovalPrincipal
from app.api.routes.agent import get_approval_principal
from app.core.config import get_settings
from app.observability import HttpObservability


router = APIRouter(prefix="/observability", tags=["observability"])


def get_http_observability(request: Request) -> HttpObservability:
    return request.app.state.http_observability


@router.get("/metrics", response_class=Response)
async def get_prometheus_metrics(
    principal: Annotated[ApprovalPrincipal, Depends(get_approval_principal)],
    observability: Annotated[
        HttpObservability,
        Depends(get_http_observability),
    ],
) -> Response:
    if not get_settings().observability_metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not principal.authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="监控指标需要已认证的运维主体",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not principal.can_view_alerts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前认证主体没有监控指标读取权限",
        )
    payload, content_type = observability.render()
    return Response(content=payload, headers={"Content-Type": content_type})
