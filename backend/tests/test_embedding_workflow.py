from collections.abc import Sequence

import pytest

from app.services.embedding.errors import (
    EmbeddingAPIError,
    EmbeddingResponseError,
    EmbeddingTransportError,
)
from app.services.embedding.workflow import embed_texts


class StubEmbeddingProvider:
    dimension = 3

    def __init__(
        self,
        *,
        failures: list[Exception] | None = None,
        result: list[list[float]] | None = None,
    ) -> None:
        self.failures = list(failures or [])
        self.result = result
        self.calls = 0
        self.closed = False

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        if self.result is not None:
            return self.result
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    async def aclose(self) -> None:
        self.closed = True


async def test_retries_transport_errors_then_succeeds() -> None:
    provider = StubEmbeddingProvider(
        failures=[
            EmbeddingTransportError("network-1"),
            EmbeddingTransportError("network-2"),
        ]
    )

    result = await embed_texts(
        ["a", "b"],
        provider_factory=lambda: provider,
        max_retries=2,
        retry_base_delay_seconds=0,
    )

    assert result == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert provider.calls == 3
    assert provider.closed is True


async def test_does_not_retry_non_retryable_api_error() -> None:
    provider = StubEmbeddingProvider(
        failures=[
            EmbeddingAPIError(
                "bad request",
                status_code=400,
                code="InvalidParameter",
            )
        ]
    )

    with pytest.raises(EmbeddingAPIError):
        await embed_texts(
            ["a"],
            provider_factory=lambda: provider,
            max_retries=2,
            retry_base_delay_seconds=0,
        )

    assert provider.calls == 1
    assert provider.closed is True


async def test_rejects_provider_result_count_mismatch() -> None:
    provider = StubEmbeddingProvider(result=[])

    with pytest.raises(EmbeddingResponseError):
        await embed_texts(
            ["a"],
            provider_factory=lambda: provider,
            max_retries=2,
            retry_base_delay_seconds=0,
        )

    assert provider.calls == 1
    assert provider.closed is True
