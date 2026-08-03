import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import math
from typing import TypeVar
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentChunk
from app.services.embedding.base import EmbeddingProvider
from app.services.embedding.errors import (
    EmbeddingAPIError,
    EmbeddingResponseError,
    EmbeddingServiceError,
    EmbeddingTransportError,
)
from app.services.embedding.factory import create_embedding_provider


EmbeddingProviderFactory = Callable[[], EmbeddingProvider]
ResultType = TypeVar("ResultType")


@dataclass(frozen=True, slots=True)
class EmbeddingBackfillResult:
    document_id: uuid.UUID
    total_chunks: int
    embedded_chunks: int
    skipped_chunks: int


class EmbeddingDocumentNotFoundError(LookupError):
    """Raised when a requested document does not exist."""


def get_embedding_provider_factory() -> EmbeddingProviderFactory:
    """Expose a lazy provider factory for FastAPI dependency overrides."""

    return create_embedding_provider


async def embed_texts(
    texts: Sequence[str],
    *,
    provider_factory: EmbeddingProviderFactory,
    max_retries: int,
    retry_base_delay_seconds: float,
) -> list[list[float]]:
    provider = provider_factory()
    try:
        embeddings = await _with_retry(
            lambda: provider.embed_documents(texts),
            max_retries=max_retries,
            retry_base_delay_seconds=retry_base_delay_seconds,
        )
        _validate_provider_result(provider, embeddings, expected_count=len(texts))
        return embeddings
    finally:
        await provider.aclose()


async def embed_query_text(
    text: str,
    *,
    provider_factory: EmbeddingProviderFactory,
    max_retries: int,
    retry_base_delay_seconds: float,
) -> list[float]:
    provider = provider_factory()
    try:
        embedding = await _with_retry(
            lambda: provider.embed_query(text),
            max_retries=max_retries,
            retry_base_delay_seconds=retry_base_delay_seconds,
        )
        _validate_provider_result(provider, [embedding], expected_count=1)
        return embedding
    finally:
        await provider.aclose()


async def backfill_document_embeddings(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    provider_factory: EmbeddingProviderFactory,
    max_retries: int,
    retry_base_delay_seconds: float,
) -> EmbeddingBackfillResult:
    async with db.begin():
        document_exists = await db.scalar(
            select(Document.id).where(Document.id == document_id)
        )
        if document_exists is None:
            raise EmbeddingDocumentNotFoundError("文档不存在")

        rows = (
            await db.execute(
                select(
                    DocumentChunk.id,
                    DocumentChunk.content,
                    DocumentChunk.embedding,
                )
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index)
            )
        ).all()

    targets = [
        (chunk_id, content)
        for chunk_id, content, embedding in rows
        if embedding is None
    ]
    if not targets:
        return EmbeddingBackfillResult(
            document_id=document_id,
            total_chunks=len(rows),
            embedded_chunks=0,
            skipped_chunks=len(rows),
        )

    embeddings = await embed_texts(
        [content for _, content in targets],
        provider_factory=provider_factory,
        max_retries=max_retries,
        retry_base_delay_seconds=retry_base_delay_seconds,
    )

    embedded_chunks = 0
    async with db.begin():
        for (chunk_id, _), embedding in zip(targets, embeddings, strict=True):
            result = await db.execute(
                update(DocumentChunk)
                .where(
                    DocumentChunk.id == chunk_id,
                    DocumentChunk.embedding.is_(None),
                )
                .values(embedding=embedding)
            )
            embedded_chunks += result.rowcount or 0

    return EmbeddingBackfillResult(
        document_id=document_id,
        total_chunks=len(rows),
        embedded_chunks=embedded_chunks,
        skipped_chunks=len(rows) - embedded_chunks,
    )


async def _with_retry(
    operation: Callable[[], Awaitable[ResultType]],
    *,
    max_retries: int,
    retry_base_delay_seconds: float,
) -> ResultType:
    retry_count = 0
    while True:
        try:
            return await operation()
        except EmbeddingServiceError as exc:
            if retry_count >= max_retries or not _is_retryable(exc):
                raise
            delay = retry_base_delay_seconds * (2**retry_count)
            retry_count += 1
            if delay > 0:
                await asyncio.sleep(delay)


def _is_retryable(exc: EmbeddingServiceError) -> bool:
    if isinstance(exc, EmbeddingAPIError):
        return exc.retryable
    return isinstance(exc, EmbeddingTransportError)


def _validate_provider_result(
    provider: EmbeddingProvider,
    embeddings: list[list[float]],
    *,
    expected_count: int,
) -> None:
    if len(embeddings) != expected_count:
        raise EmbeddingResponseError(
            f"Embedding 返回数量不匹配：期望 {expected_count}，实际 {len(embeddings)}"
        )
    for embedding in embeddings:
        if len(embedding) != provider.dimension:
            raise EmbeddingResponseError(
                f"Embedding 向量维度不匹配：期望 {provider.dimension}，实际 {len(embedding)}"
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in embedding
        ):
            raise EmbeddingResponseError("Embedding 向量包含无效数值")
