import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.exposition import CONTENT_TYPE_LATEST, generate_latest
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import Message, Receive, Scope, Send


logger = logging.getLogger(__name__)
REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class HttpObservability:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.requests = Counter(
            "qualitypilot_http_requests_total",
            "Total HTTP requests completed by the QualityPilot API.",
            ("method", "route", "status_code"),
            registry=self.registry,
        )
        self.duration = Histogram(
            "qualitypilot_http_request_duration_seconds",
            "QualityPilot API request latency in seconds.",
            ("method", "route"),
            registry=self.registry,
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
        )
        self.in_flight = Gauge(
            "qualitypilot_http_requests_in_flight",
            "Current in-flight QualityPilot API requests.",
            registry=self.registry,
        )

    def record(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        self.requests.labels(method, route, str(status_code)).inc()
        self.duration.labels(method, route).observe(duration_seconds)

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST


class HttpObservabilityMiddleware:
    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        observability: HttpObservability,
        metrics_enabled: bool = True,
        request_logs_enabled: bool = True,
    ) -> None:
        self.app = app
        self.observability = observability
        self.metrics_enabled = metrics_enabled
        self.request_logs_enabled = request_logs_enabled

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        scope.setdefault("state", {})["request_id"] = request_id
        method = str(scope.get("method", "UNKNOWN")).upper()
        status_code = 500
        route_template = "unmatched"
        recorded = False
        started_at = time.perf_counter()
        if self.metrics_enabled:
            self.observability.in_flight.inc()

        def record_request() -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            duration_seconds = max(time.perf_counter() - started_at, 0.0)
            if self.metrics_enabled:
                self.observability.in_flight.dec()
                self.observability.record(
                    method=method,
                    route=route_template,
                    status_code=status_code,
                    duration_seconds=duration_seconds,
                )
            if self.request_logs_enabled:
                logger.info(
                    "http_request %s",
                    json.dumps(
                        {
                            "request_id": request_id,
                            "method": method,
                            "route": route_template,
                            "status_code": status_code,
                            "duration_ms": round(duration_seconds * 1000, 3),
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )

        async def send_with_request_id(message: Message) -> None:
            nonlocal route_template, status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                route_template = _route_template(scope)
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                record_request()

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            record_request()


def _request_id(candidate: str | None) -> str:
    if candidate is not None and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


def _route_template(scope: Scope) -> str:
    fastapi_scope = scope.get("fastapi")
    if isinstance(fastapi_scope, dict):
        effective_context = fastapi_scope.get("effective_route_context")
        effective_path = getattr(effective_context, "path", None)
        if isinstance(effective_path, str) and effective_path:
            return effective_path
    route: Any = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "unmatched"
