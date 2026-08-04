from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.persistence import persistent_agent_workflow
from app.api.routes import agent, health, knowledge, search
from app.core.config import get_settings
from app.db import close_db, init_db


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        async with persistent_agent_workflow(
            settings.database_url,
            min_size=settings.agent_checkpoint_pool_min_size,
            max_size=settings.agent_checkpoint_pool_max_size,
        ) as workflow:
            app.state.quality_agent_workflow = workflow
            yield
    finally:
        app.state.quality_agent_workflow = None
        await close_db()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="QualityPilot AI quality-management backend.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs"}
