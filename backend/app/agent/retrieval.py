from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.protocols import EvidenceRetriever
from app.agent.state import AgentEvidence
from app.core.config import get_settings
from app.services.embedding.workflow import (
    EmbeddingProviderFactory,
    embed_query_text,
)
from app.services.knowledge_search import (
    KnowledgeSearchHit,
    fuse_search_hits,
    has_searchable_chunks,
    search_knowledge_chunks,
    search_keyword_chunks,
)


settings = get_settings()


AgentEvidenceRetrieverBuilder = Callable[
    [AsyncSession, EmbeddingProviderFactory],
    EvidenceRetriever,
]


def _to_evidence(hit: KnowledgeSearchHit) -> AgentEvidence:
    return {
        "chunk_id": str(hit.chunk_id),
        "document_id": str(hit.document_id),
        "document_title": hit.document_title,
        "source_uri": hit.source_uri,
        "original_filename": hit.original_filename,
        "chunk_index": hit.chunk_index,
        "content": hit.content,
        "score": hit.score,
        "match_type": hit.match_type,
        "vector_score": hit.vector_score,
        "keyword_score": hit.keyword_score,
    }


def build_agent_evidence_retriever(
    db: AsyncSession,
    provider_factory: EmbeddingProviderFactory,
) -> EvidenceRetriever:
    async def retrieve(
        question: str,
        *,
        top_k: int,
        mode: str,
    ) -> list[AgentEvidence]:
        if mode == "keyword":
            hits = await search_keyword_chunks(db, question, top_k=top_k)
            return [_to_evidence(hit) for hit in hits]

        if not await has_searchable_chunks(db):
            if mode == "hybrid":
                keyword_hits = await search_keyword_chunks(
                    db,
                    question,
                    top_k=top_k,
                )
                hits = fuse_search_hits([], keyword_hits, top_k=top_k)
                return [_to_evidence(hit) for hit in hits]
            return []

        query_embedding = await embed_query_text(
            question,
            provider_factory=provider_factory,
            max_retries=settings.embedding_max_retries,
            retry_base_delay_seconds=(
                settings.embedding_retry_base_delay_seconds
            ),
        )
        candidate_count = min(
            top_k * settings.hybrid_candidate_multiplier,
            250,
        )
        vector_hits = await search_knowledge_chunks(
            db,
            query_embedding,
            top_k=candidate_count if mode == "hybrid" else top_k,
        )
        if mode == "vector":
            hits = vector_hits
        else:
            keyword_hits = await search_keyword_chunks(
                db,
                question,
                top_k=candidate_count,
            )
            hits = fuse_search_hits(
                vector_hits,
                keyword_hits,
                top_k=top_k,
                rrf_k=settings.hybrid_rrf_k,
            )
        return [_to_evidence(hit) for hit in hits]

    return retrieve


def get_agent_evidence_retriever_builder() -> AgentEvidenceRetrieverBuilder:
    return build_agent_evidence_retriever
