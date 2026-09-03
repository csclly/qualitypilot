import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.metrics import AgentMetricsSnapshot
from app.models import AgentAlertOutbox


AlertDeliveryStatus = Literal["pending", "delivering", "delivered", "failed"]


@dataclass(frozen=True, slots=True)
class AgentAlertOutboxRecord:
    id: uuid.UUID
    fingerprint: str
    window_started_at: datetime
    window_ended_at: datetime
    window_hours: int
    error_events: int
    alert_threshold: int
    status: AlertDeliveryStatus
    attempt_count: int
    next_attempt_at: datetime
    lease_token: uuid.UUID | None
    lease_expires_at: datetime | None
    delivered_at: datetime | None
    last_error_kind: str | None
    created_at: datetime


class AgentAlertOutboxStore(Protocol):
    async def enqueue(
        self,
        snapshot: AgentMetricsSnapshot,
    ) -> tuple[AgentAlertOutboxRecord, bool]: ...

    async def claim_next(
        self,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AgentAlertOutboxRecord | None: ...

    async def mark_delivered(
        self,
        record: AgentAlertOutboxRecord,
        *,
        delivered_at: datetime | None = None,
    ) -> AgentAlertOutboxRecord: ...

    async def mark_failed(
        self,
        record: AgentAlertOutboxRecord,
        *,
        error_kind: str,
        retryable: bool,
        max_attempts: int,
        retry_base_delay_seconds: float,
        retry_max_delay_seconds: float,
        now: datetime | None = None,
    ) -> AgentAlertOutboxRecord: ...


class SqlAgentAlertOutboxStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        snapshot: AgentMetricsSnapshot,
    ) -> tuple[AgentAlertOutboxRecord, bool]:
        if (
            snapshot.alert_status != "warning"
            or snapshot.error_events < snapshot.alert_threshold
        ):
            raise ValueError("只有达到阈值的 Agent 指标才能进入告警 Outbox")
        fingerprint = alert_fingerprint(snapshot)
        existing = await self._get_by_fingerprint(fingerprint)
        if existing is not None:
            return existing, False

        event = AgentAlertOutbox(
            fingerprint=fingerprint,
            window_started_at=snapshot.window_started_at,
            window_ended_at=snapshot.generated_at,
            window_hours=snapshot.window_hours,
            error_events=snapshot.error_events,
            alert_threshold=snapshot.alert_threshold,
            status="pending",
        )
        self._session.add(event)
        try:
            await self._session.commit()
            await self._session.refresh(event)
        except IntegrityError:
            await self._session.rollback()
            existing = await self._get_by_fingerprint(fingerprint)
            if existing is None:
                raise
            return existing, False
        return _to_record(event), True

    async def claim_next(
        self,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AgentAlertOutboxRecord | None:
        if lease_seconds <= 0:
            raise ValueError("告警投递租约必须大于 0 秒")
        claimed_at = now or datetime.now(UTC)
        lease_token = uuid.uuid4()
        result = await self._session.execute(
            select(AgentAlertOutbox)
            .where(
                or_(
                    and_(
                        AgentAlertOutbox.status == "pending",
                        AgentAlertOutbox.next_attempt_at <= claimed_at,
                    ),
                    and_(
                        AgentAlertOutbox.status == "delivering",
                        AgentAlertOutbox.lease_expires_at <= claimed_at,
                    ),
                )
            )
            .order_by(
                AgentAlertOutbox.next_attempt_at,
                AgentAlertOutbox.created_at,
                AgentAlertOutbox.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        event = result.scalar_one_or_none()
        if event is None:
            await self._session.rollback()
            return None
        event.status = "delivering"
        event.attempt_count += 1
        event.lease_token = lease_token
        event.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
        await self._session.commit()
        await self._session.refresh(event)
        return _to_record(event)

    async def mark_delivered(
        self,
        record: AgentAlertOutboxRecord,
        *,
        delivered_at: datetime | None = None,
    ) -> AgentAlertOutboxRecord:
        lease_token = _require_active_lease(record)
        result = await self._session.execute(
            update(AgentAlertOutbox)
            .where(
                AgentAlertOutbox.id == record.id,
                AgentAlertOutbox.status == "delivering",
                AgentAlertOutbox.lease_token == lease_token,
            )
            .values(
                status="delivered",
                lease_token=None,
                lease_expires_at=None,
                delivered_at=delivered_at or datetime.now(UTC),
                last_error_kind=None,
            )
            .returning(AgentAlertOutbox)
        )
        event = result.scalar_one_or_none()
        if event is None:
            await self._session.rollback()
            raise ValueError("告警投递租约已失效，不能确认送达")
        await self._session.commit()
        return _to_record(event)

    async def mark_failed(
        self,
        record: AgentAlertOutboxRecord,
        *,
        error_kind: str,
        retryable: bool,
        max_attempts: int,
        retry_base_delay_seconds: float,
        retry_max_delay_seconds: float,
        now: datetime | None = None,
    ) -> AgentAlertOutboxRecord:
        lease_token = _require_active_lease(record)
        if not error_kind.strip() or len(error_kind) > 100:
            raise ValueError("告警投递错误类型必须为 1—100 个字符")
        if max_attempts <= 0:
            raise ValueError("告警最大投递次数必须大于 0")
        if retry_base_delay_seconds < 0 or retry_max_delay_seconds <= 0:
            raise ValueError("告警重试延迟配置无效")

        failed_at = now or datetime.now(UTC)
        terminal = not retryable or record.attempt_count >= max_attempts
        delay_seconds = min(
            retry_base_delay_seconds * (2 ** max(record.attempt_count - 1, 0)),
            retry_max_delay_seconds,
        )
        result = await self._session.execute(
            update(AgentAlertOutbox)
            .where(
                AgentAlertOutbox.id == record.id,
                AgentAlertOutbox.status == "delivering",
                AgentAlertOutbox.lease_token == lease_token,
            )
            .values(
                status="failed" if terminal else "pending",
                next_attempt_at=(
                    failed_at
                    if terminal
                    else failed_at + timedelta(seconds=delay_seconds)
                ),
                lease_token=None,
                lease_expires_at=None,
                last_error_kind=error_kind.strip(),
            )
            .returning(AgentAlertOutbox)
        )
        event = result.scalar_one_or_none()
        if event is None:
            await self._session.rollback()
            raise ValueError("告警投递租约已失效，不能记录失败")
        await self._session.commit()
        return _to_record(event)

    async def _get_by_fingerprint(
        self,
        fingerprint: str,
    ) -> AgentAlertOutboxRecord | None:
        result = await self._session.execute(
            select(AgentAlertOutbox).where(
                AgentAlertOutbox.fingerprint == fingerprint
            )
        )
        event = result.scalar_one_or_none()
        return _to_record(event) if event is not None else None


def alert_fingerprint(snapshot: AgentMetricsSnapshot) -> str:
    bucket = snapshot.generated_at.replace(minute=0, second=0, microsecond=0)
    canonical = (
        f"agent-errors|{snapshot.window_hours}|{bucket.isoformat()}|"
        f"{snapshot.alert_threshold}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _to_record(event: AgentAlertOutbox) -> AgentAlertOutboxRecord:
    return AgentAlertOutboxRecord(
        id=event.id,
        fingerprint=event.fingerprint,
        window_started_at=event.window_started_at,
        window_ended_at=event.window_ended_at,
        window_hours=event.window_hours,
        error_events=event.error_events,
        alert_threshold=event.alert_threshold,
        status=event.status,
        attempt_count=event.attempt_count,
        next_attempt_at=event.next_attempt_at,
        lease_token=event.lease_token,
        lease_expires_at=event.lease_expires_at,
        delivered_at=event.delivered_at,
        last_error_kind=event.last_error_kind,
        created_at=event.created_at,
    )


def _require_active_lease(record: AgentAlertOutboxRecord) -> uuid.UUID:
    if record.status != "delivering" or record.lease_token is None:
        raise ValueError("告警记录没有有效投递租约")
    return record.lease_token
