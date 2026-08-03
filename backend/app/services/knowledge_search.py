from dataclasses import dataclass, replace
import uuid

from sqlalchemy import Float, exists, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentChunk


@dataclass(frozen=True, slots=True)
class KnowledgeSearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    source_type: str
    source_uri: str | None
    original_filename: str | None
    chunk_index: int
    content: str
    char_start: int | None
    char_end: int | None
    score: float
    match_type: str = "vector"
    vector_score: float | None = None
    keyword_score: float | None = None


async def has_searchable_chunks(db: AsyncSession) -> bool:
    async with db.begin():
        return bool(
            await db.scalar(
                select(
                    exists().where(DocumentChunk.embedding.is_not(None))
                )
            )
        )


async def search_knowledge_chunks(
    db: AsyncSession,
    query_embedding: list[float],
    *,
    top_k: int,
) -> list[KnowledgeSearchHit]:
    distance = DocumentChunk.embedding.cosine_distance(query_embedding).label(
        "cosine_distance"
    )
    statement = (
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            Document.title,
            Document.source_type,
            Document.source_uri,
            Document.original_filename,
            DocumentChunk.chunk_index,
            DocumentChunk.content,
            DocumentChunk.char_start,
            DocumentChunk.char_end,
            distance,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(top_k)
    )

    async with db.begin():
        rows = (await db.execute(statement)).all()

    return [
        KnowledgeSearchHit(
            chunk_id=chunk_id,
            document_id=document_id,
            document_title=document_title,
            source_type=source_type,
            source_uri=source_uri,
            original_filename=original_filename,
            chunk_index=chunk_index,
            content=content,
            char_start=char_start,
            char_end=char_end,
            score=1.0 - float(cosine_distance),
            match_type="vector",
            vector_score=1.0 - float(cosine_distance),
        )
        for (
            chunk_id,
            document_id,
            document_title,
            source_type,
            source_uri,
            original_filename,
            chunk_index,
            content,
            char_start,
            char_end,
            cosine_distance,
        ) in rows
    ]


async def search_keyword_chunks(
    db: AsyncSession,
    query_text: str,
    *,
    top_k: int,
) -> list[KnowledgeSearchHit]:
    keyword_distance = type_coerce(
        DocumentChunk.content.op("<->>")(query_text),
        Float,
    ).label("keyword_distance")
    statement = (
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            Document.title,
            Document.source_type,
            Document.source_uri,
            Document.original_filename,
            DocumentChunk.chunk_index,
            DocumentChunk.content,
            DocumentChunk.char_start,
            DocumentChunk.char_end,
            keyword_distance,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.content != "")
        .where(keyword_distance < 1.0)
        .order_by(keyword_distance)
        .limit(top_k)
    )

    async with db.begin():
        rows = (await db.execute(statement)).all()

    return [
        KnowledgeSearchHit(
            chunk_id=chunk_id,
            document_id=document_id,
            document_title=document_title,
            source_type=source_type,
            source_uri=source_uri,
            original_filename=original_filename,
            chunk_index=chunk_index,
            content=content,
            char_start=char_start,
            char_end=char_end,
            score=1.0 - float(keyword_distance_value),
            match_type="keyword",
            keyword_score=1.0 - float(keyword_distance_value),
        )
        for (
            chunk_id,
            document_id,
            document_title,
            source_type,
            source_uri,
            original_filename,
            chunk_index,
            content,
            char_start,
            char_end,
            keyword_distance_value,
        ) in rows
    ]


def fuse_search_hits(
    vector_hits: list[KnowledgeSearchHit],
    keyword_hits: list[KnowledgeSearchHit],
    *,
    top_k: int,
    rrf_k: int = 60,
) -> list[KnowledgeSearchHit]:
    if top_k < 1:
        raise ValueError("top_k 必须大于等于 1")
    if rrf_k < 1:
        raise ValueError("rrf_k 必须大于等于 1")

    channels = [hits for hits in (vector_hits, keyword_hits) if hits]
    if not channels:
        return []

    hit_by_id: dict[uuid.UUID, KnowledgeSearchHit] = {}
    fused_score_by_id: dict[uuid.UUID, float] = {}
    vector_score_by_id = {hit.chunk_id: hit.score for hit in vector_hits}
    keyword_score_by_id = {hit.chunk_id: hit.score for hit in keyword_hits}

    for hits in channels:
        seen_ids: set[uuid.UUID] = set()
        for rank, hit in enumerate(hits, start=1):
            if hit.chunk_id in seen_ids:
                continue
            seen_ids.add(hit.chunk_id)
            hit_by_id.setdefault(hit.chunk_id, hit)
            fused_score_by_id[hit.chunk_id] = (
                fused_score_by_id.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            )

    maximum_rrf_score = len(channels) / (rrf_k + 1)
    fused_hits = [
        replace(
            hit_by_id[chunk_id],
            score=rrf_score / maximum_rrf_score,
            match_type="hybrid",
            vector_score=vector_score_by_id.get(chunk_id),
            keyword_score=keyword_score_by_id.get(chunk_id),
        )
        for chunk_id, rrf_score in fused_score_by_id.items()
    ]
    return sorted(
        fused_hits,
        key=lambda hit: (-hit.score, str(hit.chunk_id)),
    )[:top_k]
