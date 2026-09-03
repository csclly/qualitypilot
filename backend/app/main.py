from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.alert_delivery import WebhookAgentAlertDeliveryProvider
from app.agent.alert_scheduler import AgentAlertScheduler, SqlAgentAlertCycleRunner
from app.agent.persistence import persistent_agent_workflow
from app.api.routes import (
    agent,
    agent_alerts,
    agent_metrics,
    agent_retention,
    health,
    knowledge,
    observability,
    search,
)
from app.core.config import get_settings
from app.db import SessionLocal, close_db, init_db
from app.observability import HttpObservability, HttpObservabilityMiddleware


settings = get_settings()
http_observability = HttpObservability()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    alert_provider: WebhookAgentAlertDeliveryProvider | None = None
    alert_scheduler: AgentAlertScheduler | None = None
    app.state.agent_alert_scheduler = None
    try:
        async with persistent_agent_workflow(
            settings.database_url,
            min_size=settings.agent_checkpoint_pool_min_size,
            max_size=settings.agent_checkpoint_pool_max_size,
        ) as workflow:
            app.state.quality_agent_workflow = workflow
            if settings.agent_alert_scheduler_enabled:
                assert settings.agent_alert_webhook_url is not None
                alert_provider = WebhookAgentAlertDeliveryProvider(
                    webhook_url=(
                        settings.agent_alert_webhook_url.get_secret_value()
                    ),
                    bearer_token=(
                        settings.agent_alert_webhook_bearer_token.get_secret_value()
                        if settings.agent_alert_webhook_bearer_token is not None
                        else None
                    ),
                    timeout_seconds=(
                        settings.agent_alert_delivery_timeout_seconds
                    ),
                )
                runner = SqlAgentAlertCycleRunner(
                    session_factory=SessionLocal,
                    provider=alert_provider,
                    window_hours=settings.agent_alert_scheduler_window_hours,
                    alert_threshold=settings.agent_error_alert_threshold,
                    batch_size=settings.agent_alert_scheduler_batch_size,
                    lease_seconds=settings.agent_alert_lease_seconds,
                    max_attempts=settings.agent_alert_max_attempts,
                    retry_base_delay_seconds=(
                        settings.agent_alert_retry_base_delay_seconds
                    ),
                    retry_max_delay_seconds=(
                        settings.agent_alert_retry_max_delay_seconds
                    ),
                )
                alert_scheduler = AgentAlertScheduler(
                    runner=runner,
                    interval_seconds=(
                        settings.agent_alert_scheduler_interval_seconds
                    ),
                )
                app.state.agent_alert_scheduler = alert_scheduler
                alert_scheduler.start()
            try:
                yield
            finally:
                if alert_scheduler is not None:
                    await alert_scheduler.stop()
                if alert_provider is not None:
                    await alert_provider.aclose()
                app.state.agent_alert_scheduler = None
    finally:
        app.state.quality_agent_workflow = None
        await close_db()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="QualityPilot AI quality-management backend.",
    lifespan=lifespan,
)
app.state.http_observability = http_observability

app.add_middleware(
    HttpObservabilityMiddleware,
    observability=http_observability,
    metrics_enabled=settings.observability_metrics_enabled,
    request_logs_enabled=settings.observability_request_logs_enabled,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Agent-Run-Id",
        "X-Request-ID",
        "X-Total-Count",
        "X-Limit",
        "X-Offset",
    ],
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")
app.include_router(agent_metrics.router, prefix="/api/v1")
app.include_router(agent_alerts.router, prefix="/api/v1")
app.include_router(agent_retention.router, prefix="/api/v1")
app.include_router(observability.router, prefix="/api/v1")


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs"}
