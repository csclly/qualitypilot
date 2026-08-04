import uuid

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
import pytest
from pydantic import ValidationError

from app.agent.persistence import (
    build_postgres_agent_workflow,
    create_checkpoint_pool,
    to_psycopg_conninfo,
)
from app.agent.protocols import AgentRuntimeContext
from app.agent.state import AgentEvidence, AgentRecommendation
from app.core.config import Settings
from tests.conftest import TEST_DATABASE_URL


class FakeGenerator:
    async def generate(
        self,
        question: str,
        evidence: list[AgentEvidence],
    ) -> AgentRecommendation:
        return {
            "summary": f"{question}：{len(evidence)} 条证据",
            "suggested_actions": ["人工核查证据"],
            "risk_notes": ["测试草稿"],
        }


def _context() -> AgentRuntimeContext:
    async def retrieve(
        _question: str,
        *,
        top_k: int,
        mode: str,
    ) -> list[AgentEvidence]:
        assert top_k == 1
        assert mode == "keyword"
        return [
            {
                "chunk_id": str(uuid.uuid4()),
                "document_id": str(uuid.uuid4()),
                "document_title": "持久化测试文档",
                "source_uri": None,
                "original_filename": None,
                "chunk_index": 0,
                "content": "测试证据",
                "score": 1.0,
                "match_type": "keyword",
                "vector_score": None,
                "keyword_score": 1.0,
            }
        ]

    return AgentRuntimeContext(retriever=retrieve, generator=FakeGenerator())


def test_psycopg_conninfo_replaces_sqlalchemy_driver() -> None:
    conninfo = to_psycopg_conninfo(
        "postgresql+asyncpg://user:password@localhost:5432/database"
    )
    assert conninfo == "postgresql://user:password@localhost:5432/database"


def test_psycopg_conninfo_rejects_non_postgresql_database() -> None:
    with pytest.raises(ValueError, match="只支持 PostgreSQL"):
        to_psycopg_conninfo("sqlite+aiosqlite:///test.db")


def test_checkpoint_pool_configuration_requires_valid_range() -> None:
    with pytest.raises(
        ValidationError,
        match="AGENT_CHECKPOINT_POOL_MAX_SIZE",
    ):
        Settings(
            _env_file=None,
            agent_checkpoint_pool_min_size=5,
            agent_checkpoint_pool_max_size=2,
        )


@pytest.mark.integration
async def test_agent_checkpoint_survives_workflow_and_pool_recreation() -> None:
    run_id = str(uuid.uuid4())
    initial_state = {
        "run_id": run_id,
        "question": "进程重启后还能审批吗？",
        "search_mode": "keyword",
        "top_k": 1,
        "status": "created",
        "evidence": [],
        "final_response": None,
    }

    first_pool = create_checkpoint_pool(
        TEST_DATABASE_URL,
        min_size=1,
        max_size=2,
    )
    await first_pool.open(wait=True)
    try:
        first_workflow = build_postgres_agent_workflow(first_pool)
        pending = await first_workflow.start(initial_state, _context())
        assert pending["status"] == "pending_approval"
    finally:
        await first_pool.close()

    second_pool = create_checkpoint_pool(
        TEST_DATABASE_URL,
        min_size=1,
        max_size=2,
    )
    await second_pool.open(wait=True)
    try:
        second_workflow = build_postgres_agent_workflow(second_pool)
        restored = await second_workflow.get(run_id)
        assert restored is not None
        assert restored["status"] == "pending_approval"
        assert restored["question"] == initial_state["question"]

        completed = await second_workflow.approve(
            run_id,
            approved=True,
            comment="跨连接恢复成功",
        )
        assert completed["status"] == "completed"
        assert completed["approval_comment"] == "跨连接恢复成功"

        checkpointer = AsyncPostgresSaver(
            second_pool,
            serde=JsonPlusSerializer(allowed_msgpack_modules=None),
        )
        await checkpointer.adelete_thread(run_id)
    finally:
        await second_pool.close()
