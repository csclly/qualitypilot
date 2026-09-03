import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.agent.alerts import AgentAlertOutboxRecord, AgentAlertOutboxStore


AlertDeliveryOutcome = Literal[
    "idle",
    "delivered",
    "retry_scheduled",
    "failed",
]


class AlertDeliveryError(Exception):
    def __init__(self, kind: str, *, retryable: bool) -> None:
        super().__init__(kind)
        self.kind = kind
        self.retryable = retryable


class AgentAlertDeliveryProvider(Protocol):
    async def deliver(self, alert: AgentAlertOutboxRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentAlertDeliveryResult:
    outcome: AlertDeliveryOutcome
    alert: AgentAlertOutboxRecord | None


class WebhookAgentAlertDeliveryProvider:
    def __init__(
        self,
        *,
        webhook_url: str,
        bearer_token: str | None,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_url = webhook_url.strip()
        parsed_url = httpx.URL(normalized_url)
        if not parsed_url.host or parsed_url.scheme not in {"http", "https"}:
            raise ValueError("告警 Webhook URL 无效")
        if parsed_url.scheme != "https" and parsed_url.host not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("告警 Webhook 必须使用 HTTPS，回环地址除外")
        if parsed_url.userinfo:
            raise ValueError("告警 Webhook URL 不得包含用户信息")
        if timeout_seconds <= 0:
            raise ValueError("告警 Webhook 超时必须大于 0")

        self._webhook_url = normalized_url
        self._bearer_token = bearer_token.strip() if bearer_token else None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            http2=False,
        )

    async def deliver(self, alert: AgentAlertOutboxRecord) -> None:
        headers = {
            "Idempotency-Key": alert.fingerprint,
            "X-QualityPilot-Alert-Id": str(alert.id),
        }
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        try:
            response = await self._client.post(
                self._webhook_url,
                headers=headers,
                json={
                    "event_type": "agent_error_threshold_reached",
                    "alert_id": str(alert.id),
                    "fingerprint": alert.fingerprint,
                    "window_started_at": alert.window_started_at.isoformat(),
                    "window_ended_at": alert.window_ended_at.isoformat(),
                    "window_hours": alert.window_hours,
                    "error_events": alert.error_events,
                    "alert_threshold": alert.alert_threshold,
                    "attempt": alert.attempt_count,
                },
            )
        except httpx.TimeoutException as exc:
            raise AlertDeliveryError(
                "webhook_timeout",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise AlertDeliveryError(
                "webhook_transport",
                retryable=True,
            ) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise AlertDeliveryError("webhook_retryable_http", retryable=True)
        if response.is_error:
            raise AlertDeliveryError("webhook_rejected", retryable=False)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class AgentAlertDeliveryService:
    def __init__(
        self,
        *,
        store: AgentAlertOutboxStore,
        provider: AgentAlertDeliveryProvider,
        lease_seconds: int,
        max_attempts: int,
        retry_base_delay_seconds: float,
        retry_max_delay_seconds: float,
    ) -> None:
        self._store = store
        self._provider = provider
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds

    async def process_one(self) -> AgentAlertDeliveryResult:
        alert = await self._store.claim_next(lease_seconds=self._lease_seconds)
        if alert is None:
            return AgentAlertDeliveryResult(outcome="idle", alert=None)
        try:
            await self._provider.deliver(alert)
        except AlertDeliveryError as exc:
            updated = await self._store.mark_failed(
                alert,
                error_kind=exc.kind,
                retryable=exc.retryable,
                max_attempts=self._max_attempts,
                retry_base_delay_seconds=self._retry_base_delay_seconds,
                retry_max_delay_seconds=self._retry_max_delay_seconds,
            )
            return AgentAlertDeliveryResult(
                outcome=("failed" if updated.status == "failed" else "retry_scheduled"),
                alert=updated,
            )
        except Exception:
            updated = await self._store.mark_failed(
                alert,
                error_kind="unexpected",
                retryable=True,
                max_attempts=self._max_attempts,
                retry_base_delay_seconds=self._retry_base_delay_seconds,
                retry_max_delay_seconds=self._retry_max_delay_seconds,
            )
            return AgentAlertDeliveryResult(
                outcome=("failed" if updated.status == "failed" else "retry_scheduled"),
                alert=updated,
            )

        delivered = await self._store.mark_delivered(alert)
        return AgentAlertDeliveryResult(outcome="delivered", alert=delivered)
