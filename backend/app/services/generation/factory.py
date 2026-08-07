import httpx

from app.core.config import Settings, get_settings
from app.services.generation.errors import GenerationConfigurationError
from app.services.generation.qwen import QwenStructuredRecommendationGenerator


def create_generation_provider(
    settings: Settings | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> QwenStructuredRecommendationGenerator:
    current = settings or get_settings()
    secret = current.dashscope_api_key
    if secret is None or not secret.get_secret_value().strip():
        raise GenerationConfigurationError("未配置 DASHSCOPE_API_KEY")
    return QwenStructuredRecommendationGenerator(
        api_key=secret.get_secret_value(),
        base_url=current.generation_base_url,
        model=current.generation_model,
        timeout_seconds=current.generation_timeout_seconds,
        max_completion_tokens=current.generation_max_completion_tokens,
        client=client,
    )
