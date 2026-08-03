from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "QualityPilot API"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    database_url: str = (
        "postgresql+asyncpg://qualitypilot:qualitypilot_dev_password"
        "@localhost:5432/qualitypilot"
    )
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    upload_directory: str = "../data/uploads"
    max_upload_size: int = 20 * 1024 * 1024
    document_chunk_size: int = 800
    document_chunk_overlap: int = 100

    dashscope_api_key: SecretStr | None = None
    embedding_base_url: str = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    embedding_model: str = "qwen3.7-text-embedding"
    embedding_dimension: int = Field(default=1024, gt=0)
    embedding_batch_size: int = Field(default=10, ge=1, le=20)
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)
    embedding_max_retries: int = Field(default=2, ge=0, le=5)
    embedding_retry_base_delay_seconds: float = Field(default=0.5, ge=0, le=10)
    hybrid_candidate_multiplier: int = Field(default=5, ge=1, le=20)
    hybrid_rrf_k: int = Field(default=60, ge=1, le=200)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
