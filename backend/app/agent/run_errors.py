import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRunErrorEvent
from app.services.embedding.errors import (
    EmbeddingAPIError,
    EmbeddingConfigurationError,
    EmbeddingResponseError,
    EmbeddingTimeoutError,
    EmbeddingTransportError,
)


AgentErrorStage = Literal[
    "retrieval",
    "business_context",
    "drafting",
    "workflow",
]
AGENT_ERROR_STAGES = frozenset(
    {"retrieval", "business_context", "drafting", "workflow"}
)


@dataclass(frozen=True, slots=True)
class AgentRunErrorRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    stage: AgentErrorStage
    error_kind: str
    message: str
    retryable: bool
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ClassifiedAgentError:
    error_kind: str
    message: str
    retryable: bool


class AgentRunErrorStore(Protocol):
    async def append(
        self,
        *,
        run_id: uuid.UUID,
        stage: AgentErrorStage,
        error: ClassifiedAgentError,
    ) -> AgentRunErrorRecord: ...

    async def list_for_run(
        self,
        run_id: uuid.UUID,
    ) -> list[AgentRunErrorRecord]: ...


class SqlAgentRunErrorStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        run_id: uuid.UUID,
        stage: AgentErrorStage,
        error: ClassifiedAgentError,
    ) -> AgentRunErrorRecord:
        if stage not in AGENT_ERROR_STAGES:
            raise ValueError("Agent 错误阶段无效")
        if not error.error_kind.strip() or len(error.error_kind) > 100:
            raise ValueError("Agent 错误类型无效")
        if not error.message.strip() or len(error.message) > 1000:
            raise ValueError("Agent 错误消息无效")
        await self._session.rollback()
        event = AgentRunErrorEvent(
            run_id=run_id,
            stage=stage,
            error_kind=error.error_kind,
            message=error.message,
            retryable=error.retryable,
        )
        self._session.add(event)
        await self._session.commit()
        await self._session.refresh(event)
        return _to_record(event)

    async def list_for_run(
        self,
        run_id: uuid.UUID,
    ) -> list[AgentRunErrorRecord]:
        result = await self._session.execute(
            select(AgentRunErrorEvent)
            .where(AgentRunErrorEvent.run_id == run_id)
            .order_by(
                AgentRunErrorEvent.occurred_at,
                AgentRunErrorEvent.id,
            )
        )
        return [_to_record(event) for event in result.scalars()]


def classify_agent_error(exc: Exception) -> ClassifiedAgentError:
    if isinstance(exc, EmbeddingConfigurationError):
        return ClassifiedAgentError(
            error_kind="embedding_configuration",
            message="Embedding 服务未配置",
            retryable=False,
        )
    if isinstance(exc, EmbeddingTimeoutError):
        return ClassifiedAgentError(
            error_kind="embedding_timeout",
            message="Embedding 服务调用超时",
            retryable=True,
        )
    if isinstance(exc, EmbeddingAPIError):
        return ClassifiedAgentError(
            error_kind="embedding_api",
            message="Embedding 服务返回异常",
            retryable=exc.retryable,
        )
    if isinstance(exc, EmbeddingResponseError):
        return ClassifiedAgentError(
            error_kind="embedding_response",
            message="Embedding 服务响应不符合契约",
            retryable=False,
        )
    if isinstance(exc, EmbeddingTransportError):
        return ClassifiedAgentError(
            error_kind="embedding_transport",
            message="Embedding 服务暂时不可用",
            retryable=True,
        )
    return ClassifiedAgentError(
        error_kind="unexpected",
        message="Agent 节点发生未分类错误",
        retryable=False,
    )


def _to_record(event: AgentRunErrorEvent) -> AgentRunErrorRecord:
    return AgentRunErrorRecord(
        id=event.id,
        run_id=event.run_id,
        stage=event.stage,
        error_kind=event.error_kind,
        message=event.message,
        retryable=event.retryable,
        occurred_at=event.occurred_at,
    )
