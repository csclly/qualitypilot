from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import embedding_http_exception
from app.core.config import get_settings
from app.db import get_db
from app.schemas import (
    KnowledgeSearchMode,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from app.services.embedding.errors import EmbeddingServiceError
from app.services.embedding.workflow import (
    EmbeddingProviderFactory,
    embed_query_text,
    get_embedding_provider_factory,
)
from app.services.knowledge_search import (
    fuse_search_hits,
    has_searchable_chunks,
    search_knowledge_chunks,
    search_keyword_chunks,
)


router = APIRouter(prefix="/knowledge", tags=["knowledge"])
settings = get_settings()


@router.post("/search", response_model=list[KnowledgeSearchResult])
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    provider_factory: Annotated[
        EmbeddingProviderFactory,
        Depends(get_embedding_provider_factory),
    ],
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeSearchResult]:
    if payload.mode == KnowledgeSearchMode.KEYWORD:
        hits = await search_keyword_chunks(db, payload.query, top_k=payload.top_k)
        return [
            KnowledgeSearchResult.model_validate(hit, from_attributes=True)
            for hit in hits
        ]

    if not await has_searchable_chunks(db):
        if payload.mode == KnowledgeSearchMode.HYBRID:
            keyword_hits = await search_keyword_chunks(
                db, payload.query, top_k=payload.top_k
            )
            hits = fuse_search_hits([], keyword_hits, top_k=payload.top_k)
            return [
                KnowledgeSearchResult.model_validate(hit, from_attributes=True)
                for hit in hits
            ]
        return []

    try:
        query_embedding = await embed_query_text(
            payload.query,
            provider_factory=provider_factory,
            max_retries=settings.embedding_max_retries,
            retry_base_delay_seconds=settings.embedding_retry_base_delay_seconds,
        )
    except EmbeddingServiceError as exc:
        raise embedding_http_exception(exc) from exc

    candidate_count = min(payload.top_k * settings.hybrid_candidate_multiplier, 250)
    vector_hits = await search_knowledge_chunks(
        db,
        query_embedding,
        top_k=(
            candidate_count
            if payload.mode == KnowledgeSearchMode.HYBRID
            else payload.top_k
        ),
    )
    if payload.mode == KnowledgeSearchMode.VECTOR:
        hits = vector_hits
    else:
        keyword_hits = await search_keyword_chunks(
            db,
            payload.query,
            top_k=candidate_count,
        )
        hits = fuse_search_hits(
            vector_hits,
            keyword_hits,
            top_k=payload.top_k,
            rrf_k=settings.hybrid_rrf_k,
        )
    return [
        KnowledgeSearchResult.model_validate(hit, from_attributes=True)
        for hit in hits
    ]
