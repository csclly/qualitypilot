from pathlib import Path
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Document, DocumentChunk


@pytest.mark.integration
async def test_upload_and_query_chunks() -> None:
    created_document: dict | None = None
    settings = get_settings()
    content = ("PCB 偏移异常，需要检查对位参数和设备状态。\n\n" * 100).encode()

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
    finally:
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


async def test_rejects_unsupported_upload_and_missing_document() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload_response = await client.post(
            "/api/v1/knowledge/documents/upload",
            files={"file": ("data.xlsx", b"not supported", "application/octet-stream")},
        )
        assert upload_response.status_code == 415

        chunks_response = await client.get(
            f"/api/v1/knowledge/documents/{uuid.uuid4()}/chunks"
        )
        assert chunks_response.status_code == 404
