import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentAuditEvent


APPROVAL_DECISION_EVENT = "approval_decision"


@dataclass(frozen=True, slots=True)
class ApprovalAuditRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    actor_id: str
    actor_authenticated: bool
    auth_method: str | None
    approved: bool
    comment: str | None
    occurred_at: datetime


class ApprovalAuditConflictError(RuntimeError):
    pass


class ApprovalAuditStore(Protocol):
    async def get_for_run(
        self,
        run_id: uuid.UUID,
    ) -> ApprovalAuditRecord | None: ...

    async def append_decision(
        self,
        *,
        event_id: uuid.UUID,
        run_id: uuid.UUID,
        actor_id: str,
        actor_authenticated: bool,
        auth_method: str | None,
        approved: bool,
        comment: str | None,
    ) -> tuple[ApprovalAuditRecord, bool]: ...


class SqlApprovalAuditStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_run(
        self,
        run_id: uuid.UUID,
    ) -> ApprovalAuditRecord | None:
        result = await self._session.execute(
            select(AgentAuditEvent).where(
                AgentAuditEvent.run_id == run_id,
                AgentAuditEvent.event_type == APPROVAL_DECISION_EVENT,
            )
        )
        event = result.scalar_one_or_none()
        return _to_record(event) if event is not None else None

    async def append_decision(
        self,
        *,
        event_id: uuid.UUID,
        run_id: uuid.UUID,
        actor_id: str,
        actor_authenticated: bool,
        auth_method: str | None,
        approved: bool,
        comment: str | None,
    ) -> tuple[ApprovalAuditRecord, bool]:
        if (
            not actor_id.strip()
            or actor_id != actor_id.strip()
            or len(actor_id) > 255
        ):
            raise ValueError("审批人标识无效")
        if comment is not None and len(comment) > 2000:
            raise ValueError("审批备注过长")
        if actor_authenticated is not (auth_method is not None):
            raise ValueError("审批身份认证状态与认证方式不一致")
        if auth_method is not None and (
            not auth_method.strip()
            or auth_method != auth_method.strip()
            or len(auth_method) > 50
        ):
            raise ValueError("审批认证方式无效")
        existing = await self.get_for_run(run_id)
        if existing is not None:
            return _existing_or_conflict(
                existing,
                event_id=event_id,
                actor_id=actor_id,
                actor_authenticated=actor_authenticated,
                auth_method=auth_method,
                approved=approved,
                comment=comment,
            )

        event = AgentAuditEvent(
            id=event_id,
            run_id=run_id,
            event_type=APPROVAL_DECISION_EVENT,
            actor_id=actor_id,
            actor_authenticated=actor_authenticated,
            auth_method=auth_method,
            approved=approved,
            comment=comment,
        )
        self._session.add(event)
        try:
            await self._session.commit()
            await self._session.refresh(event)
        except IntegrityError as exc:
            await self._session.rollback()
            existing = await self.get_for_run(run_id)
            if existing is not None:
                return _existing_or_conflict(
                    existing,
                    event_id=event_id,
                    actor_id=actor_id,
                    actor_authenticated=actor_authenticated,
                    auth_method=auth_method,
                    approved=approved,
                    comment=comment,
                )
            raise ApprovalAuditConflictError(
                "审批事件 ID 已被其他运行记录使用"
            ) from exc
        return _to_record(event), True


def _existing_or_conflict(
    existing: ApprovalAuditRecord,
    *,
    event_id: uuid.UUID,
    actor_id: str,
    actor_authenticated: bool,
    auth_method: str | None,
    approved: bool,
    comment: str | None,
) -> tuple[ApprovalAuditRecord, bool]:
    if (
        existing.id == event_id
        and existing.actor_id == actor_id
        and existing.actor_authenticated is actor_authenticated
        and existing.auth_method == auth_method
        and existing.approved is approved
        and existing.comment == comment
    ):
        return existing, False
    raise ApprovalAuditConflictError("该 Agent 运行已有不同的审批决策事件")


def _to_record(event: AgentAuditEvent) -> ApprovalAuditRecord:
    return ApprovalAuditRecord(
        id=event.id,
        run_id=event.run_id,
        actor_id=event.actor_id,
        actor_authenticated=event.actor_authenticated,
        auth_method=event.auth_method,
        approved=event.approved,
        comment=event.comment,
        occurred_at=event.occurred_at,
    )
