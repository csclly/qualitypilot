import json

import httpx
from pydantic import SecretStr
import pytest

from app.core.config import Settings
from app.services.embedding import (
    EmbeddingAPIError,
    EmbeddingConfigurationError,
    EmbeddingResponseError,
    EmbeddingTimeoutError,
    QwenEmbeddingProvider,
    create_embedding_provider,
)


def make_provider(
    client: httpx.AsyncClient,
    *,
    dimension: int = 3,
    batch_size: int = 2,
) -> QwenEmbeddingProvider:
    return QwenEmbeddingProvider(
        api_key="test-key",
        base_url="https://embedding.test/compatible-mode/v1",
        model="qwen3.7-text-embedding",
        dimension=dimension,
        batch_size=batch_size,
        timeout_seconds=1,
        client=client,
    )


async def test_batches_documents_and_preserves_response_order() -> None:
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        inputs = payload["input"]
        batch_sizes.append(len(inputs))
        assert request.headers["Authorization"] == "Bearer test-key"
        assert payload["dimensions"] == 3
        data = [
            {"index": index, "embedding": [index + 0.1, index + 0.2, index + 0.3]}
            for index in reversed(range(len(inputs)))
        ]
        return httpx.Response(200, json={"data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(client)
        result = await provider.embed_documents(["a", "b", "c", "d", "e"])

    assert batch_sizes == [2, 2, 1]
    assert result == [
        [0.1, 0.2, 0.3],
        [1.1, 1.2, 1.3],
        [0.1, 0.2, 0.3],
        [1.1, 1.2, 1.3],
        [0.1, 0.2, 0.3],
    ]


async def test_empty_document_list_does_not_call_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"不应发送请求：{request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(client)
        assert await provider.embed_documents([]) == []


async def test_embed_query_returns_one_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 2, 3]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(client)
        assert await provider.embed_query("PCB 偏移") == [1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    ("status_code", "expected_retryable"),
    [(400, False), (429, True), (503, True)],
)
async def test_maps_api_errors(status_code: int, expected_retryable: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"error": {"code": "TestError", "message": "模型调用失败"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(client)
        with pytest.raises(EmbeddingAPIError) as caught:
            await provider.embed_query("测试")

    assert caught.value.status_code == status_code
    assert caught.value.code == "TestError"
    assert caught.value.retryable is expected_retryable


async def test_maps_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(client)
        with pytest.raises(EmbeddingTimeoutError):
            await provider.embed_query("测试")


@pytest.mark.parametrize(
    ("texts", "response_data"),
    [
        (["a", "b"], []),
        (["a"], [{"index": 0, "embedding": [1, 2]}]),
        (
            ["a", "b"],
            [
                {"index": 0, "embedding": [1, 2, 3]},
                {"index": 0, "embedding": [4, 5, 6]},
            ],
        ),
    ],
)
async def test_rejects_invalid_embedding_response(
    texts: list[str],
    response_data: list[dict],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": response_data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(client)
        with pytest.raises(EmbeddingResponseError):
            await provider.embed_documents(texts)


async def test_rejects_non_finite_vector_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"""{"data":[{"index":0,"embedding":[1,NaN,3]}]}""",
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(client)
        with pytest.raises(EmbeddingResponseError):
            await provider.embed_query("测试")


def test_factory_requires_api_key() -> None:
    settings = Settings(_env_file=None, dashscope_api_key=None)

    with pytest.raises(EmbeddingConfigurationError):
        create_embedding_provider(settings)


async def test_factory_uses_typed_settings() -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key=SecretStr("factory-key"),
        embedding_base_url="https://embedding.test/compatible-mode/v1",
        embedding_dimension=3,
        embedding_batch_size=2,
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200))

    async with httpx.AsyncClient(transport=transport) as client:
        provider = create_embedding_provider(settings, client=client)
        assert isinstance(provider, QwenEmbeddingProvider)
        assert provider.dimension == 3
