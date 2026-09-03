import re

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.agent.security import (
    ALERT_VIEWER_ROLE,
    API_KEY_AUTH_METHOD,
    ApprovalPrincipal,
)
from app.api.routes.agent import get_approval_principal
from app.main import app
from app.observability import HttpObservability, HttpObservabilityMiddleware


async def test_middleware_propagates_request_id_and_uses_route_template() -> None:
    inner = FastAPI()
    observability = HttpObservability()

    @inner.get("/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    instrumented = HttpObservabilityMiddleware(
        inner,
        observability=observability,
        request_logs_enabled=False,
    )
    async with AsyncClient(
        transport=ASGITransport(app=instrumented),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/items/customer-secret-123",
            headers={"X-Request-ID": "upstream-request-001"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "upstream-request-001"
    metrics = observability.render()[0].decode("utf-8")
    assert 'route="/items/{item_id}"' in metrics
    assert "customer-secret-123" not in metrics
    assert 'status_code="200"' in metrics


async def test_middleware_replaces_unsafe_request_id() -> None:
    inner = FastAPI()
    observability = HttpObservability()

    @inner.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    instrumented = HttpObservabilityMiddleware(
        inner,
        observability=observability,
        request_logs_enabled=False,
    )
    async with AsyncClient(
        transport=ASGITransport(app=instrumented),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/health",
            headers={"X-Request-ID": "unsafe request id"},
        )

    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        response.headers["X-Request-ID"],
    )


@pytest.mark.parametrize(
    ("principal", "expected_status"),
    [
        (ApprovalPrincipal("unverified", frozenset(), False, None), 401),
        (
            ApprovalPrincipal(
                "authenticated-observer",
                frozenset(),
                True,
                API_KEY_AUTH_METHOD,
            ),
            403,
        ),
    ],
)
async def test_metrics_endpoint_requires_viewer_role(
    principal: ApprovalPrincipal,
    expected_status: int,
) -> None:
    app.dependency_overrides[get_approval_principal] = lambda: principal
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/observability/metrics")
        assert response.status_code == expected_status
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)


async def test_metrics_endpoint_exports_prometheus_text() -> None:
    principal = ApprovalPrincipal(
        "metrics-reader",
        frozenset({ALERT_VIEWER_ROLE}),
        True,
        API_KEY_AUTH_METHOD,
    )
    app.dependency_overrides[get_approval_principal] = lambda: principal
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            await client.get("/api/v1/health")
            response = await client.get("/api/v1/observability/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "qualitypilot_http_requests_total" in response.text
        assert 'route="/api/v1/health"' in response.text
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
