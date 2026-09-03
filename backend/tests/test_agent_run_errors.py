import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.agent.run_errors import (
    ClassifiedAgentError,
    SqlAgentRunErrorStore,
    classify_agent_error,
)
from app.db import SessionLocal
from app.services.embedding.errors import (
    EmbeddingAPIError,
    EmbeddingConfigurationError,
    EmbeddingResponseError,
    EmbeddingTimeoutError,
    EmbeddingTransportError,
)


@pytest.mark.parametrize(
    ("exception", "kind", "retryable"),
    [
        (EmbeddingConfigurationError("secret"), "embedding_configuration", False),
        (EmbeddingTimeoutError("secret"), "embedding_timeout", True),
        (EmbeddingTransportError("secret"), "embedding_transport", True),
        (
            EmbeddingAPIError("secret", status_code=503),
            "embedding_api",
            True,
        ),
        (EmbeddingResponseError("secret"), "embedding_response", False),
        (RuntimeError("secret"), "unexpected", False),
    ],
)
def test_classifies_errors_without_exposing_original_message(
    exception: Exception,
    kind: str,
    retryable: bool,
) -> None:
    classified = classify_agent_error(exception)

    assert classified.error_kind == kind
    assert classified.retryable is retryable
    assert "secret" not in classified.message


@pytest.mark.integration
async def test_sql_error_store_appends_and_lists_sanitized_events() -> None:
    run_id = uuid.uuid4()
    async with SessionLocal() as session:
        store = SqlAgentRunErrorStore(session)
        created = await store.append(
            run_id=run_id,
            stage="retrieval",
            error=ClassifiedAgentError(
                error_kind="embedding_timeout",
                message="Embedding 服务调用超时",
                retryable=True,
            ),
        )
        records = await store.list_for_run(run_id)

    assert records == [created]
    assert created.stage == "retrieval"
    assert created.retryable is True


@pytest.mark.integration
async def test_database_rejects_error_event_mutations_and_rolls_back_fixture() -> None:
    event_id = uuid.uuid4()
    async with SessionLocal() as session:
        transaction = await session.begin()
        await session.execute(
            text(
                """
                INSERT INTO agent_run_error_events
                    (id, run_id, stage, error_kind, message, retryable)
                VALUES
                    (:id, :run_id, 'workflow', 'unexpected',
                     'Agent 节点发生未分类错误', false)
                """
            ),
            {"id": event_id, "run_id": uuid.uuid4()},
        )
        mutations = (
            "UPDATE agent_run_error_events SET retryable = true WHERE id = :id",
            "DELETE FROM agent_run_error_events WHERE id = :id",
            "TRUNCATE TABLE agent_run_error_events",
        )
        for statement in mutations:
            savepoint = await session.begin_nested()
            with pytest.raises(DBAPIError, match="immutable"):
                parameters = {"id": event_id} if ":id" in statement else None
                await session.execute(text(statement), parameters)
            await savepoint.rollback()
        await transaction.rollback()
