from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url

from app.agent.workflow import QualityAgentWorkflow


def to_psycopg_conninfo(database_url: str) -> str:
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise ValueError("Agent 持久化只支持 PostgreSQL 数据库")
    return url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def create_checkpoint_pool(
    database_url: str,
    *,
    min_size: int,
    max_size: int,
) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=to_psycopg_conninfo(database_url),
        min_size=min_size,
        max_size=max_size,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )


def build_postgres_agent_workflow(
    pool: AsyncConnectionPool,
) -> QualityAgentWorkflow:
    serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
    checkpointer = AsyncPostgresSaver(pool, serde=serializer)
    return QualityAgentWorkflow(checkpointer)


@asynccontextmanager
async def persistent_agent_workflow(
    database_url: str,
    *,
    min_size: int,
    max_size: int,
) -> AsyncIterator[QualityAgentWorkflow]:
    pool = create_checkpoint_pool(
        database_url,
        min_size=min_size,
        max_size=max_size,
    )
    await pool.open(wait=True)
    try:
        yield build_postgres_agent_workflow(pool)
    finally:
        await pool.close()
