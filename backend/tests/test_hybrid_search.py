from collections.abc import Sequence
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.main import app
from app.models import Document, DocumentChunk
from app.services.embedding.workflow import get_embedding_provider_factory
from app.services.knowledge_search import KnowledgeSearchHit, fuse_search_hits


class FakeHybridEmbeddingProvider:
    dimension = 1024

    def __init__(self) -> None:
        self.query_calls = 0
        self.closed = False

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 1023 for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [1.0] + [0.0] * 1023

    async def aclose(self) -> None:
        self.closed = True


def make_hit(chunk_id: uuid.UUID, score: float, *, match_type: str) -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        chunk_id=chunk_id,
        document_id=uuid.uuid4(),
        document_title=f"文档-{chunk_id}",
        source_type="test",
        source_uri=None,
        original_filename=None,
        chunk_index=0,
        content="测试内容",
        char_start=0,
        char_end=4,
        score=score,
        match_type=match_type,
    )


def test_rrf_fusion_prioritizes_hits_present_in_both_channels() -> None:
    first_id = uuid.UUID(int=1)
    shared_id = uuid.UUID(int=2)
    keyword_only_id = uuid.UUID(int=3)

    fused = fuse_search_hits(
        [
            make_hit(first_id, 0.95, match_type="vector"),
            make_hit(shared_id, 0.80, match_type="vector"),
        ],
        [
            make_hit(shared_id, 0.90, match_type="keyword"),
            make_hit(keyword_only_id, 0.70, match_type="keyword"),
        ],
        top_k=3,
    )

    assert [hit.chunk_id for hit in fused] == [shared_id, first_id, keyword_only_id]
    assert fused[0].match_type == "hybrid"
    assert fused[0].vector_score == pytest.approx(0.80)
    assert fused[0].keyword_score == pytest.approx(0.90)
    assert 0 < fused[0].score <= 1


def test_rrf_fusion_validates_parameters_and_handles_one_channel() -> None:
    hit = make_hit(uuid.UUID(int=1), 0.8, match_type="keyword")

    fused = fuse_search_hits([], [hit], top_k=1)

    assert fused[0].score == pytest.approx(1.0)
    assert fused[0].match_type == "hybrid"
    assert fused[0].vector_score is None
    assert fused[0].keyword_score == pytest.approx(0.8)
    with pytest.raises(ValueError, match="top_k"):
        fuse_search_hits([], [], top_k=0)
    with pytest.raises(ValueError, match="rrf_k"):
        fuse_search_hits([], [hit], top_k=1, rrf_k=0)


@pytest.mark.integration
async def test_keyword_and_hybrid_search_rank_chinese_terms() -> None:
    document_id = uuid.uuid4()
    provider = FakeHybridEmbeddingProvider()

    async with SessionLocal() as session:
        async with session.begin():
            session.add(
                Document(
                    id=document_id,
                    title="AOI 报警代码手册",
                    source_type="test",
                    status="ready",
                    chunk_count=2,
                )
            )
            session.add_all(
                [
                    DocumentChunk(
                        document_id=document_id,
                        chunk_index=0,
                        content="AOI设备报警代码E42表示镜头污染，应清洁镜头并重新标定。",
                        embedding=[1.0] + [0.0] * 1023,
                    ),
                    DocumentChunk(
                        document_id=document_id,
                        chunk_index=1,
                        content="回流焊温度曲线应检查预热区、恒温区和峰值温度。",
                        embedding=[0.0, 1.0] + [0.0] * 1022,
                    ),
                ]
            )

    app.dependency_overrides[get_embedding_provider_factory] = lambda: lambda: provider
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            keyword_response = await client.post(
                "/api/v1/knowledge/search",
                json={"query": "报警代码E42", "top_k": 2, "mode": "keyword"},
            )
            hybrid_response = await client.post(
                "/api/v1/knowledge/search",
                json={"query": "报警代码E42", "top_k": 2, "mode": "hybrid"},
            )

        assert keyword_response.status_code == 200, keyword_response.text
        keyword_hits = keyword_response.json()
        assert len(keyword_hits) == 1
        assert keyword_hits[0]["chunk_index"] == 0
        assert keyword_hits[0]["match_type"] == "keyword"
        assert keyword_hits[0]["keyword_score"] > 0
        assert keyword_hits[0]["vector_score"] is None

        assert hybrid_response.status_code == 200, hybrid_response.text
        hybrid_hits = hybrid_response.json()
        assert hybrid_hits[0]["chunk_index"] == 0
        assert hybrid_hits[0]["match_type"] == "hybrid"
        assert hybrid_hits[0]["vector_score"] == pytest.approx(1.0)
        assert hybrid_hits[0]["keyword_score"] > 0
        assert provider.query_calls == 1
        assert provider.closed is True
    finally:
        app.dependency_overrides.pop(get_embedding_provider_factory, None)
        async with SessionLocal() as session:
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.commit()


async def test_search_rejects_unknown_mode() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/knowledge/search",
            json={"query": "PCB", "mode": "unknown"},
        )

    assert response.status_code == 422
