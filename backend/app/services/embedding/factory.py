import httpx

from app.core.config import Settings, get_settings
from app.services.embedding.base import EmbeddingProvider
from app.services.embedding.errors import EmbeddingConfigurationError
from app.services.embedding.qwen import QwenEmbeddingProvider


def create_embedding_provider(
    settings: Settings | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> EmbeddingProvider:
    current_settings = settings or get_settings()
    secret = current_settings.dashscope_api_key
    if secret is None or not secret.get_secret_value().strip():
        raise EmbeddingConfigurationError("未配置 DASHSCOPE_API_KEY")

    return QwenEmbeddingProvider(
        api_key=secret.get_secret_value(),
        base_url=current_settings.embedding_base_url,
        model=current_settings.embedding_model,
        dimension=current_settings.embedding_dimension,
        batch_size=current_settings.embedding_batch_size,
        timeout_seconds=current_settings.embedding_timeout_seconds,
        client=client,
    )
