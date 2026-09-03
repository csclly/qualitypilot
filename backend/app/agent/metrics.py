from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentAuditEvent, AgentRunErrorEvent


@dataclass(frozen=True, slots=True)
class AgentMetricsSnapshot:
    generated_at: datetime
    window_started_at: datetime
    window_hours: int
    approval_decisions: int
    authenticated_approvals: int
    approved_decisions: int
    rejected_decisions: int
    error_events: int
    affected_runs: int
    retryable_errors: int
    errors_by_stage: dict[str, int]
    errors_by_kind: dict[str, int]
    alert_status: Literal["ok", "warning"]
    alert_threshold: int


class AgentMetricsStore(Protocol):
    async def snapshot(
        self,
        *,
        window_hours: int,
        alert_threshold: int,
    ) -> AgentMetricsSnapshot: ...


class SqlAgentMetricsStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def snapshot(
        self,
        *,
        window_hours: int,
        alert_threshold: int,
    ) -> AgentMetricsSnapshot:
        if not 1 <= window_hours <= 720:
            raise ValueError("Agent 指标时间窗口必须在 1 到 720 小时之间")
        if alert_threshold <= 0:
            raise ValueError("Agent 错误告警阈值必须大于 0")

        generated_at = datetime.now(UTC)
        window_started_at = generated_at - timedelta(hours=window_hours)
        approval_row = (
            await self._session.execute(
                select(
                    func.count(AgentAuditEvent.id),
                    func.count(AgentAuditEvent.id).filter(
                        AgentAuditEvent.actor_authenticated.is_(True)
                    ),
                    func.count(AgentAuditEvent.id).filter(
                        AgentAuditEvent.approved.is_(True)
                    ),
                    func.count(AgentAuditEvent.id).filter(
                        AgentAuditEvent.approved.is_(False)
                    ),
                ).where(AgentAuditEvent.occurred_at >= window_started_at)
            )
        ).one()
        error_row = (
            await self._session.execute(
                select(
                    func.count(AgentRunErrorEvent.id),
                    func.count(distinct(AgentRunErrorEvent.run_id)),
                    func.count(AgentRunErrorEvent.id).filter(
                        AgentRunErrorEvent.retryable.is_(True)
                    ),
                ).where(AgentRunErrorEvent.occurred_at >= window_started_at)
            )
        ).one()
        stage_rows = (
            await self._session.execute(
                select(
                    AgentRunErrorEvent.stage,
                    func.count(AgentRunErrorEvent.id),
                )
                .where(AgentRunErrorEvent.occurred_at >= window_started_at)
                .group_by(AgentRunErrorEvent.stage)
                .order_by(AgentRunErrorEvent.stage)
            )
        ).all()
        kind_rows = (
            await self._session.execute(
                select(
                    AgentRunErrorEvent.error_kind,
                    func.count(AgentRunErrorEvent.id),
                )
                .where(AgentRunErrorEvent.occurred_at >= window_started_at)
                .group_by(AgentRunErrorEvent.error_kind)
                .order_by(AgentRunErrorEvent.error_kind)
            )
        ).all()

        error_events = int(error_row[0])
        return AgentMetricsSnapshot(
            generated_at=generated_at,
            window_started_at=window_started_at,
            window_hours=window_hours,
            approval_decisions=int(approval_row[0]),
            authenticated_approvals=int(approval_row[1]),
            approved_decisions=int(approval_row[2]),
            rejected_decisions=int(approval_row[3]),
            error_events=error_events,
            affected_runs=int(error_row[1]),
            retryable_errors=int(error_row[2]),
            errors_by_stage={stage: int(count) for stage, count in stage_rows},
            errors_by_kind={kind: int(count) for kind, count in kind_rows},
            alert_status=(
                "warning" if error_events >= alert_threshold else "ok"
            ),
            alert_threshold=alert_threshold,
        )
