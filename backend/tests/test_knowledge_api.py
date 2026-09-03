from pathlib import Path
from collections.abc import Sequence
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Document, DocumentChunk
from app.services.embedding.errors import EmbeddingTransportError
from app.services.embedding.workflow import get_embedding_provider_factory


class FakeEmbeddingProvider:
    dimension = 1024

    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [[0.01] * self.dimension for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.01] * self.dimension

    async def aclose(self) -> None:
        self.closed = True


class FailingEmbeddingProvider(FakeEmbeddingProvider):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        raise EmbeddingTransportError("测试网络故障")


@pytest.mark.integration
async def test_upload_and_query_chunks() -> None:
    created_document: dict | None = None
    settings = get_settings()
    content = ("PCB 偏移异常，需要检查对位参数和设备状态。\n\n" * 100).encode()
    provider = FakeEmbeddingProvider()
    app.dependency_overrides[get_embedding_provider_factory] = lambda: lambda: provider

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/knowledge/documents/upload",
                files={"file": ("偏移分析.txt", content, "text/plain")},
                data={"title": "上传接口集成测试"},
            )
            assert response.status_code == 201, response.text
            created_document = response.json()
            assert created_document["status"] == "ready"
            assert created_document["source_type"] == "upload"
            assert created_document["original_filename"] == "偏移分析.txt"
            assert created_document["file_size"] == len(content)
            assert created_document["chunk_count"] > 1

            chunks_response = await client.get(
                f"/api/v1/knowledge/documents/{created_document['id']}/chunks"
            )
            assert chunks_response.status_code == 200
            chunks = chunks_response.json()
            assert len(chunks) == created_document["chunk_count"]
            assert [chunk["chunk_index"] for chunk in chunks] == list(range(len(chunks)))
            assert all(chunk["char_start"] < chunk["char_end"] for chunk in chunks)
            assert all(chunk["has_embedding"] is True for chunk in chunks)
            assert all(chunk["embedding_dimension"] == 1024 for chunk in chunks)
            assert provider.calls == 1
            assert provider.closed is True
    finally:
        app.dependency_overrides.pop(get_embedding_provider_factory, None)
        if created_document is not None:
            async with SessionLocal() as session:
                await session.execute(
                    delete(Document).where(Document.id == uuid.UUID(created_document["id"]))
                )
                await session.commit()
            stored_name = created_document.get("storage_path")
            if stored_name:
                (Path(settings.upload_directory).resolve() / stored_name).unlink(missing_ok=True)


@pytest.mark.integration
async def test_backfill_is_idempotent() -> None:
    document_id = uuid.uuid4()
    provider = FakeEmbeddingProvider()

    async with SessionLocal() as session:
        async with session.begin():
            session.add(
                Document(
                    id=document_id,
                    title="历史向量回填测试",
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
                        content="回流焊温度曲线异常。",
                    ),
                    DocumentChunk(
                        document_id=document_id,
                        chunk_index=1,
                        content="检查锡膏印刷厚度。",
                    ),
                ]
            )

    app.dependency_overrides[get_embedding_provider_factory] = lambda: lambda: provider
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first_response = await client.post(
                f"/api/v1/knowledge/documents/{document_id}/embeddings"
            )
            second_response = await client.post(
                f"/api/v1/knowledge/documents/{document_id}/embeddings"
            )
            chunks_response = await client.get(
                f"/api/v1/knowledge/documents/{document_id}/chunks"
            )

        assert first_response.status_code == 200, first_response.text
        assert first_response.json() == {
            "document_id": str(document_id),
            "total_chunks": 2,
            "embedded_chunks": 2,
            "skipped_chunks": 0,
        }
        assert second_response.status_code == 200
        assert second_response.json()["embedded_chunks"] == 0
        assert second_response.json()["skipped_chunks"] == 2
        assert provider.calls == 1
        assert all(chunk["has_embedding"] is True for chunk in chunks_response.json())
    finally:
        app.dependency_overrides.pop(get_embedding_provider_factory, None)
        async with SessionLocal() as session:
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.commit()


@pytest.mark.integration
async def test_embedding_failure_does_not_persist_upload() -> None:
    settings = get_settings()
    original_delay = settings.embedding_retry_base_delay_seconds
    provider = FailingEmbeddingProvider()
    app.dependency_overrides[get_embedding_provider_factory] = lambda: lambda: provider
    settings.embedding_retry_base_delay_seconds = 0

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/knowledge/documents/upload",
                files={"file": ("向量失败.txt", "需要向量化的正文".encode(), "text/plain")},
                data={"title": "向量失败不落库测试"},
            )

        assert response.status_code == 503
        assert provider.calls == settings.embedding_max_retries + 1
        assert provider.closed is True
        async with SessionLocal() as session:
            stored_document = await session.scalar(
                select(Document.id).where(Document.title == "向量失败不落库测试")
            )
        assert stored_document is None
    finally:
        settings.embedding_retry_base_delay_seconds = original_delay
        app.dependency_overrides.pop(get_embedding_provider_factory, None)


@pytest.mark.integration
async def test_document_and_chunks_roll_back_together() -> None:
    document_id = uuid.uuid4()

    async with SessionLocal() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                session.add(
                    Document(
                        id=document_id,
                        title="事务回滚测试",
                        source_type="test",
                        status="ready",
                    )
                )
                session.add_all(
                    [
                        DocumentChunk(
                            document_id=document_id,
                            chunk_index=0,
                            content="分块一",
                        ),
                        DocumentChunk(
                            document_id=document_id,
                            chunk_index=0,
                            content="重复分块",
                        ),
                    ]
                )

    async with SessionLocal() as verification_session:
        stored_document = await verification_session.scalar(
            select(Document.id).where(Document.id == document_id)
        )
        assert stored_document is None


@pytest.mark.integration
async def test_rejects_unsupported_upload_and_missing_document() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload_response = await client.post(
            "/api/v1/knowledge/documents/upload",
            files={"file": ("data.xlsx", b"not supported", "application/octet-stream")},
        )
        chunks_response = await client.get(
            f"/api/v1/knowledge/documents/{uuid.uuid4()}/chunks"
        )
        backfill_response = await client.post(
            f"/api/v1/knowledge/documents/{uuid.uuid4()}/embeddings"
        )

    assert upload_response.status_code == 415
    assert chunks_response.status_code == 404
    assert backfill_response.status_code == 404


@pytest.mark.integration
async def test_document_and_chunk_lists_support_optional_pagination() -> None:
    document_ids = [uuid.uuid4() for _ in range(3)]
    paged_document_id = document_ids[0]
    async with SessionLocal() as session:
        async with session.begin():
            session.add_all(
                [
                    Document(
                        id=document_id,
                        title=f"分页测试-{index}",
                        source_type="test",
                        status="ready",
                        chunk_count=3 if index == 0 else 0,
                    )
                    for index, document_id in enumerate(document_ids)
                ]
            )
            session.add_all(
                [
                    DocumentChunk(
                        document_id=paged_document_id,
                        chunk_index=index,
                        content=f"分页分块-{index}",
                    )
                    for index in range(3)
                ]
            )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            documents_response = await client.get(
                "/api/v1/knowledge/documents",
                params={"limit": 2, "offset": 1},
            )
            chunks_response = await client.get(
                f"/api/v1/knowledge/documents/{paged_document_id}/chunks",
                params={"limit": 2, "offset": 1},
            )
            unpaged_chunks_response = await client.get(
                f"/api/v1/knowledge/documents/{paged_document_id}/chunks"
            )
            invalid_response = await client.get(
                "/api/v1/knowledge/documents",
                params={"limit": 0},
            )

        assert documents_response.status_code == 200
        assert len(documents_response.json()) == 2
        assert int(documents_response.headers["X-Total-Count"]) >= 3
        assert documents_response.headers["X-Limit"] == "2"
        assert documents_response.headers["X-Offset"] == "1"
        assert chunks_response.status_code == 200
        assert [item["chunk_index"] for item in chunks_response.json()] == [1, 2]
        assert chunks_response.headers["X-Total-Count"] == "3"
        assert len(unpaged_chunks_response.json()) == 3
        assert unpaged_chunks_response.headers["X-Limit"] == "all"
        assert invalid_response.status_code == 422
    finally:
        async with SessionLocal() as session:
            await session.execute(
                delete(Document).where(Document.id.in_(document_ids))
            )
            await session.commit()
