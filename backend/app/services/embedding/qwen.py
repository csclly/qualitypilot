from collections.abc import Sequence
import math
from typing import Any

import httpx

from app.services.embedding.errors import (
    EmbeddingAPIError,
    EmbeddingConfigurationError,
    EmbeddingResponseError,
    EmbeddingTimeoutError,
    EmbeddingTransportError,
)


class QwenEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
        batch_size: int,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise EmbeddingConfigurationError("未配置百炼 API Key")
        if not model.strip():
            raise EmbeddingConfigurationError("Embedding 模型名称不能为空")
        if dimension <= 0:
            raise EmbeddingConfigurationError("Embedding 维度必须大于 0")
        if batch_size <= 0:
            raise EmbeddingConfigurationError("Embedding 批量大小必须大于 0")
        if timeout_seconds <= 0:
            raise EmbeddingConfigurationError("Embedding 超时时间必须大于 0")

        normalized_base_url = base_url.rstrip("/")
        parsed_url = httpx.URL(normalized_base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise EmbeddingConfigurationError("Embedding Base URL 无效")

        self._api_key = api_key
        self._endpoint = f"{normalized_base_url}/embeddings"
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            http2=False,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in items):
            raise ValueError("Embedding 输入文本不能为空")

        embeddings: list[list[float]] = []
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            embeddings.extend(await self._embed_batch(batch))
        return embeddings

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._client.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "input": texts,
                    "dimensions": self._dimension,
                    "encoding_format": "float",
                },
            )
        except httpx.TimeoutException as exc:
            raise EmbeddingTimeoutError("Embedding API 调用超时") from exc
        except httpx.RequestError as exc:
            raise EmbeddingTransportError("Embedding API 网络请求失败") from exc

        if response.is_error:
            code, message = _extract_api_error(response)
            raise EmbeddingAPIError(
                message,
                status_code=response.status_code,
                code=code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingResponseError("Embedding API 返回的不是有效 JSON") from exc
        return _parse_embeddings(payload, expected_count=len(texts), dimension=self._dimension)


def _extract_api_error(response: httpx.Response) -> tuple[str | None, str]:
    default_message = f"Embedding API 返回 HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return None, default_message

    if not isinstance(payload, dict):
        return None, default_message
    error = payload.get("error")
    if not isinstance(error, dict):
        return None, default_message

    code = error.get("code")
    message = error.get("message")
    return (
        code if isinstance(code, str) else None,
        message if isinstance(message, str) and message else default_message,
    )


def _parse_embeddings(
    payload: Any,
    *,
    expected_count: int,
    dimension: int,
) -> list[list[float]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise EmbeddingResponseError("Embedding API 响应缺少 data 列表")

    data = payload["data"]
    if len(data) != expected_count:
        raise EmbeddingResponseError(
            f"Embedding 返回数量不匹配：期望 {expected_count}，实际 {len(data)}"
        )

    ordered: list[list[float] | None] = [None] * expected_count
    for item in data:
        if not isinstance(item, dict):
            raise EmbeddingResponseError("Embedding data 项格式无效")
        index = item.get("index")
        vector = item.get("embedding")
        if not isinstance(index, int) or not 0 <= index < expected_count:
            raise EmbeddingResponseError("Embedding data 索引无效")
        if ordered[index] is not None:
            raise EmbeddingResponseError("Embedding data 索引重复")
        if not isinstance(vector, list) or len(vector) != dimension:
            actual_dimension = len(vector) if isinstance(vector, list) else 0
            raise EmbeddingResponseError(
                f"Embedding 向量维度不匹配：期望 {dimension}，实际 {actual_dimension}"
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            raise EmbeddingResponseError("Embedding 向量包含无效数值")
        ordered[index] = [float(value) for value in vector]

    if any(vector is None for vector in ordered):
        raise EmbeddingResponseError("Embedding data 索引不完整")
    return [vector for vector in ordered if vector is not None]
