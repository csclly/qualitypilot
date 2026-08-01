import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.main import app
from app.models import Document


@pytest.mark.integration
async def test_existing_system_and_manual_document_endpoints() -> None:
    created_id: uuid.UUID | None = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            root_response = await client.get("/")
            health_response = await client.get("/api/v1/health")
            ready_response = await client.get("/api/v1/ready")
            create_response = await client.post(
                "/api/v1/knowledge/documents",
                json={
                    "title": "现有接口回归测试",
                    "source_type": "test",
                    "source_uri": "test://manual-document",
                },
            )

            assert root_response.status_code == 200
            assert health_response.status_code == 200
            assert ready_response.status_code == 200
            assert create_response.status_code == 201, create_response.text
            created_id = uuid.UUID(create_response.json()["id"])

            list_response = await client.get("/api/v1/knowledge/documents")
            assert list_response.status_code == 200
            assert any(item["id"] == str(created_id) for item in list_response.json())
    finally:
        if created_id is not None:
            async with SessionLocal() as session:
                await session.execute(delete(Document).where(Document.id == created_id))
                await session.commit()
