from httpx import ASGITransport, AsyncClient

from app.api.routes import knowledge
from app.main import app


async def test_upload_endpoint_returns_413_for_oversized_file() -> None:
    original_limit = knowledge.settings.max_upload_size
    knowledge.settings.max_upload_size = 10
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/knowledge/documents/upload",
                files={"file": ("large.txt", b"a" * 11, "text/plain")},
            )
        assert response.status_code == 413
    finally:
        knowledge.settings.max_upload_size = original_limit


async def test_upload_endpoint_returns_422_for_empty_file() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/knowledge/documents/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
    assert response.status_code == 422
