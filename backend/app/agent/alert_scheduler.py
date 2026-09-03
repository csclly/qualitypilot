import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.alert_delivery import (
    AgentAlertDeliveryProvider,
    AgentAlertDeliveryService,
)
from app.agent.alerts import SqlAgentAlertOutboxStore
from app.agent.metrics import SqlAgentMetricsStore


@dataclass(frozen=True, slots=True)
class AgentAlertCycleResult:
    warning: bool
    queued: bool
    processed: int
    delivered: int
    retry_scheduled: int
    failed: int


@dataclass(frozen=True, slots=True)
class AgentAlertSchedulerStatus:
    enabled: bool
    running: bool
    cycles_completed: int
    cycles_failed: int
    alerts_queued: int
    alerts_processed: int
    alerts_delivered: int
    alerts_retry_scheduled: int
    alerts_failed: int
    last_cycle_started_at: datetime | None
    last_cycle_completed_at: datetime | None
    last_error_kind: str | None


class AgentAlertCycleRunner(Protocol):
    async def run_cycle(self) -> AgentAlertCycleResult: ...


class SqlAgentAlertCycleRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        provider: AgentAlertDeliveryProvider,
        window_hours: int,
        alert_threshold: int,
        batch_size: int,
        lease_seconds: int,
        max_attempts: int,
        retry_base_delay_seconds: float,
        retry_max_delay_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._window_hours = window_hours
        self._alert_threshold = alert_threshold
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds

    async def run_cycle(self) -> AgentAlertCycleResult:
        async with self._session_factory() as session:
            metrics_store = SqlAgentMetricsStore(session)
            outbox_store = SqlAgentAlertOutboxStore(session)
            snapshot = await metrics_store.snapshot(
                window_hours=self._window_hours,
                alert_threshold=self._alert_threshold,
            )
            queued = False
            if snapshot.alert_status == "warning":
                _, queued = await outbox_store.enqueue(snapshot)

            delivery_service = AgentAlertDeliveryService(
                store=outbox_store,
                provider=self._provider,
                lease_seconds=self._lease_seconds,
                max_attempts=self._max_attempts,
                retry_base_delay_seconds=self._retry_base_delay_seconds,
                retry_max_delay_seconds=self._retry_max_delay_seconds,
            )
            processed = delivered = retry_scheduled = failed = 0
            for _ in range(self._batch_size):
                result = await delivery_service.process_one()
                if result.outcome == "idle":
                    break
                processed += 1
                if result.outcome == "delivered":
                    delivered += 1
                elif result.outcome == "retry_scheduled":
                    retry_scheduled += 1
                elif result.outcome == "failed":
                    failed += 1

        return AgentAlertCycleResult(
            warning=snapshot.alert_status == "warning",
            queued=queued,
            processed=processed,
            delivered=delivered,
            retry_scheduled=retry_scheduled,
            failed=failed,
        )


class AgentAlertScheduler:
    def __init__(
        self,
        *,
        runner: AgentAlertCycleRunner,
        interval_seconds: float,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("告警调度间隔必须大于 0")
        self._runner = runner
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._cycles_completed = 0
        self._cycles_failed = 0
        self._alerts_queued = 0
        self._alerts_processed = 0
        self._alerts_delivered = 0
        self._alerts_retry_scheduled = 0
        self._alerts_failed = 0
        self._last_cycle_started_at: datetime | None = None
        self._last_cycle_completed_at: datetime | None = None
        self._last_error_kind: str | None = None

    def start(self) -> bool:
        if self._task is not None and not self._task.done():
            return False
        self._task = asyncio.create_task(
            self._run_forever(),
            name="qualitypilot-agent-alert-scheduler",
        )
        return True

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def run_cycle_once(self) -> bool:
        self._last_cycle_started_at = datetime.now(UTC)
        try:
            result = await self._runner.run_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._cycles_failed += 1
            self._last_error_kind = type(exc).__name__
            self._last_cycle_completed_at = datetime.now(UTC)
            return False

        self._cycles_completed += 1
        self._alerts_queued += int(result.queued)
        self._alerts_processed += result.processed
        self._alerts_delivered += result.delivered
        self._alerts_retry_scheduled += result.retry_scheduled
        self._alerts_failed += result.failed
        self._last_error_kind = None
        self._last_cycle_completed_at = datetime.now(UTC)
        return True

    def snapshot(self) -> AgentAlertSchedulerStatus:
        return AgentAlertSchedulerStatus(
            enabled=True,
            running=self._task is not None and not self._task.done(),
            cycles_completed=self._cycles_completed,
            cycles_failed=self._cycles_failed,
            alerts_queued=self._alerts_queued,
            alerts_processed=self._alerts_processed,
            alerts_delivered=self._alerts_delivered,
            alerts_retry_scheduled=self._alerts_retry_scheduled,
            alerts_failed=self._alerts_failed,
            last_cycle_started_at=self._last_cycle_started_at,
            last_cycle_completed_at=self._last_cycle_completed_at,
            last_error_kind=self._last_error_kind,
        )

    async def _run_forever(self) -> None:
        while True:
            await self.run_cycle_once()
            await asyncio.sleep(self._interval_seconds)


def disabled_alert_scheduler_status() -> AgentAlertSchedulerStatus:
    return AgentAlertSchedulerStatus(
        enabled=False,
        running=False,
        cycles_completed=0,
        cycles_failed=0,
        alerts_queued=0,
        alerts_processed=0,
        alerts_delivered=0,
        alerts_retry_scheduled=0,
        alerts_failed=0,
        last_cycle_started_at=None,
        last_cycle_completed_at=None,
        last_error_kind=None,
    )
