from collections.abc import Sequence
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.main import app
from app.models import Document, DocumentChunk
from app.services.embedding.workflow import get_embedding_provider_factory


class FakeQueryEmbeddingProvider:
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


@pytest.mark.integration
async def test_search_orders_by_cosine_similarity_and_returns_source() -> None:
    document_id = uuid.uuid4()
    provider = FakeQueryEmbeddingProvider()
    vectors = [
        [1.0, 0.0] + [0.0] * 1022,
        [0.8, 0.6] + [0.0] * 1022,
        [0.0, 1.0] + [0.0] * 1022,
    ]

    async with SessionLocal() as session:
        async with session.begin():
            session.add(
                Document(
                    id=document_id,
                    title="PCB 回流焊规范",
                    source_type="upload",
                    source_uri="upload://search-test.txt",
                    original_filename="回流焊规范.txt",
                    status="ready",
                    chunk_count=3,
                )
            )
            session.add_all(
                [
                    DocumentChunk(
                        document_id=document_id,
                        chunk_index=index,
                        content=f"检索测试分块 {index}",
                        char_start=index * 100,
                        char_end=index * 100 + 20,
                        embedding=vector,
                    )
                    for index, vector in enumerate(vectors)
                ]
            )

    app.dependency_overrides[get_embedding_provider_factory] = lambda: lambda: provider
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/knowledge/search",
                json={"query": "回流焊异常", "top_k": 2},
            )

        assert response.status_code == 200, response.text
        hits = response.json()
        assert [hit["chunk_index"] for hit in hits] == [0, 1]
        assert hits[0]["score"] == pytest.approx(1.0)
        assert hits[1]["score"] == pytest.approx(0.8)
        assert hits[0]["document_id"] == str(document_id)
        assert hits[0]["document_title"] == "PCB 回流焊规范"
        assert hits[0]["source_uri"] == "upload://search-test.txt"
        assert hits[0]["original_filename"] == "回流焊规范.txt"
        assert hits[0]["char_start"] == 0
        assert provider.query_calls == 1
        assert provider.closed is True
    finally:
        app.dependency_overrides.pop(get_embedding_provider_factory, None)
        async with SessionLocal() as session:
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.commit()


async def test_empty_knowledge_base_skips_embedding_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def has_no_searchable_chunks(_db: object) -> bool:
        return False

    monkeypatch.setattr(
        "app.api.routes.search.has_searchable_chunks",
        has_no_searchable_chunks,
    )
    provider = FakeQueryEmbeddingProvider()
    app.dependency_overrides[get_embedding_provider_factory] = lambda: lambda: provider

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/knowledge/search",
                json={"query": "没有向量时不应调用模型"},
            )
    finally:
        app.dependency_overrides.pop(get_embedding_provider_factory, None)

    assert response.status_code == 200
    assert response.json() == []
    assert provider.query_calls == 0
    assert provider.closed is False


async def test_search_validates_query_and_top_k() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        blank_query = await client.post(
            "/api/v1/knowledge/search",
            json={"query": "   "},
        )
        invalid_top_k = await client.post(
            "/api/v1/knowledge/search",
            json={"query": "PCB", "top_k": 0},
        )

    assert blank_query.status_code == 422
    assert invalid_top_k.status_code == 422
