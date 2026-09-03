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
    secret = current.generation_api_key
    if current.generation_provider == "dashscope" and secret is None:
        secret = current.dashscope_api_key
    if secret is None or not secret.get_secret_value().strip():
        required_key = (
            "GENERATION_API_KEY（或 DASHSCOPE_API_KEY）"
            if current.generation_provider == "dashscope"
            else "GENERATION_API_KEY"
        )
        raise GenerationConfigurationError(f"未配置 {required_key}")
    return QwenStructuredRecommendationGenerator(
        provider=current.generation_provider,
        api_key=secret.get_secret_value(),
        base_url=current.generation_base_url,
        model=current.generation_model,
        timeout_seconds=current.generation_timeout_seconds,
        max_completion_tokens=current.generation_max_completion_tokens,
        client=client,
    )
