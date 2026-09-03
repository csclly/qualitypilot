import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.retention import (
    CheckpointArchiveConflictError,
    CheckpointArchiveNotFoundError,
    CheckpointArchiveTooRecentError,
    SqlAgentRetentionStore,
)
from app.agent.security import ApprovalPrincipal
from app.api.routes.agent import get_approval_principal
from app.core.config import get_settings
from app.db import get_db
from app.schemas import (
    AgentCheckpointArchiveActionResponse,
    AgentCheckpointArchiveListResponse,
    AgentCheckpointArchiveRequest,
    AgentCheckpointArchiveResponse,
    AgentCheckpointRestoreRequest,
    AgentRetentionPreviewResponse,
)


router = APIRouter(prefix="/agent/retention", tags=["agent-retention"])


def get_agent_retention_store(
    db: AsyncSession = Depends(get_db),
) -> SqlAgentRetentionStore:
    return SqlAgentRetentionStore(db)


@router.get("/preview", response_model=AgentRetentionPreviewResponse)
async def preview_agent_retention(
    principal: Annotated[ApprovalPrincipal, Depends(get_approval_principal)],
    store: Annotated[SqlAgentRetentionStore, Depends(get_agent_retention_store)],
    older_than_days: Annotated[int | None, Query(ge=1, le=3650)] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> AgentRetentionPreviewResponse:
    _require_retention_reader(principal)
    settings = get_settings()
    preview = await store.preview(
        older_than_days=(
            older_than_days
            if older_than_days is not None
            else settings.agent_checkpoint_retention_days
        ),
        limit=(
            limit
            if limit is not None
            else settings.agent_checkpoint_archive_preview_limit
        ),
    )
    return AgentRetentionPreviewResponse.model_validate(preview)


@router.get(
    "/checkpoint-archives",
    response_model=AgentCheckpointArchiveListResponse,
)
async def list_checkpoint_archives(
    principal: Annotated[ApprovalPrincipal, Depends(get_approval_principal)],
    store: Annotated[SqlAgentRetentionStore, Depends(get_agent_retention_store)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AgentCheckpointArchiveListResponse:
    _require_retention_reader(principal)
    items = await store.list_archives(limit=limit)
    return AgentCheckpointArchiveListResponse(
        items=[
            AgentCheckpointArchiveResponse.model_validate(item)
            for item in items
        ]
    )


@router.post(
    "/checkpoints/{thread_id}/archive",
    response_model=AgentCheckpointArchiveActionResponse,
)
async def archive_checkpoint_thread(
    thread_id: uuid.UUID,
    payload: AgentCheckpointArchiveRequest,
    principal: Annotated[ApprovalPrincipal, Depends(get_approval_principal)],
    store: Annotated[SqlAgentRetentionStore, Depends(get_agent_retention_store)],
) -> AgentCheckpointArchiveActionResponse:
    _require_retention_operator(principal)
    if payload.confirm_thread_id != thread_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="归档确认的线程 ID 与路径不一致",
        )
    settings = get_settings()
    try:
        archive, changed = await store.archive_thread(
            thread_id=thread_id,
            older_than_days=(
                payload.older_than_days
                if payload.older_than_days is not None
                else settings.agent_checkpoint_retention_days
            ),
            actor_id=principal.actor_id,
            actor_authenticated=principal.authenticated,
            auth_method=principal.auth_method,
        )
    except CheckpointArchiveNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CheckpointArchiveTooRecentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CheckpointArchiveConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentCheckpointArchiveActionResponse(
        changed=changed,
        archive=AgentCheckpointArchiveResponse.model_validate(archive),
    )


@router.post(
    "/checkpoint-archives/{archive_id}/restore",
    response_model=AgentCheckpointArchiveActionResponse,
)
async def restore_checkpoint_archive(
    archive_id: uuid.UUID,
    payload: AgentCheckpointRestoreRequest,
    principal: Annotated[ApprovalPrincipal, Depends(get_approval_principal)],
    store: Annotated[SqlAgentRetentionStore, Depends(get_agent_retention_store)],
) -> AgentCheckpointArchiveActionResponse:
    _require_retention_operator(principal)
    if payload.confirm_archive_id != archive_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="恢复确认的归档 ID 与路径不一致",
        )
    try:
        archive, changed = await store.restore_archive(
            archive_id=archive_id,
            actor_id=principal.actor_id,
        )
    except CheckpointArchiveNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CheckpointArchiveConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentCheckpointArchiveActionResponse(
        changed=changed,
        archive=AgentCheckpointArchiveResponse.model_validate(archive),
    )


def _require_retention_operator(principal: ApprovalPrincipal) -> None:
    if not principal.authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="保留期治理需要已认证的运维主体",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not principal.can_manage_retention:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前认证主体没有保留期治理权限",
        )


def _require_retention_reader(principal: ApprovalPrincipal) -> None:
    if not principal.authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="保留期治理需要已认证的运维主体",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not principal.can_view_retention:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前认证主体没有保留期治理读取权限",
        )
