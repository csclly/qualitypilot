from functools import lru_cache
import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
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
    generation_provider: Literal["dashscope", "openai_compatible"] = "dashscope"
    generation_api_key: SecretStr | None = None
    generation_base_url: str = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    generation_model: str = "qwen3.7-max-2026-05-20"
    generation_timeout_seconds: float = Field(default=60.0, gt=0)
    generation_max_retries: int = Field(default=2, ge=0, le=5)
    generation_retry_base_delay_seconds: float = Field(default=0.5, ge=0, le=10)
    generation_max_completion_tokens: int = Field(default=1200, ge=200, le=8000)
    hybrid_candidate_multiplier: int = Field(default=5, ge=1, le=20)
    hybrid_rrf_k: int = Field(default=60, ge=1, le=200)
    agent_checkpoint_pool_min_size: int = Field(default=1, ge=1, le=20)
    agent_checkpoint_pool_max_size: int = Field(default=5, ge=1, le=50)
    agent_business_tool_limit: int = Field(default=10, ge=1, le=50)
    agent_business_tool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    agent_approval_auth_required: bool = False
    agent_approval_api_key_sha256: SecretStr | None = None
    agent_approval_actor_id: str | None = Field(default=None, max_length=255)
    agent_approval_api_key_roles: str = (
        "quality_approver,alert_operator,alert_viewer,"
        "retention_operator,retention_reader"
    )
    agent_oidc_enabled: bool = False
    agent_oidc_issuer: str | None = Field(default=None, max_length=2048)
    agent_oidc_audience: str | None = Field(default=None, max_length=255)
    agent_oidc_jwks_url: str | None = Field(default=None, max_length=2048)
    agent_oidc_roles_claim: str = Field(default="roles", min_length=1, max_length=128)
    agent_oidc_leeway_seconds: int = Field(default=30, ge=0, le=300)
    agent_oidc_jwks_cache_seconds: int = Field(default=300, ge=30, le=3600)
    agent_oidc_http_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    observability_metrics_enabled: bool = True
    observability_request_logs_enabled: bool = True
    agent_error_alert_threshold: int = Field(default=5, ge=1, le=10000)
    agent_alert_webhook_url: SecretStr | None = None
    agent_alert_webhook_bearer_token: SecretStr | None = None
    agent_alert_delivery_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    agent_alert_lease_seconds: int = Field(default=60, ge=5, le=3600)
    agent_alert_max_attempts: int = Field(default=5, ge=1, le=20)
    agent_alert_retry_base_delay_seconds: float = Field(default=5.0, ge=0, le=3600)
    agent_alert_retry_max_delay_seconds: float = Field(default=300.0, ge=1, le=86400)
    agent_alert_scheduler_enabled: bool = False
    agent_alert_scheduler_interval_seconds: float = Field(
        default=60.0,
        ge=5,
        le=3600,
    )
    agent_alert_scheduler_window_hours: int = Field(default=24, ge=1, le=720)
    agent_alert_scheduler_batch_size: int = Field(default=10, ge=1, le=10)
    agent_checkpoint_retention_days: int = Field(default=30, ge=1, le=3650)
    agent_checkpoint_archive_preview_limit: int = Field(
        default=100,
        ge=1,
        le=500,
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def agent_approval_api_key_role_set(self) -> frozenset[str]:
        return frozenset(
            role.strip()
            for role in self.agent_approval_api_key_roles.split(",")
            if role.strip()
        )

    @field_validator(
        "generation_api_key",
        "agent_approval_api_key_sha256",
        "agent_approval_actor_id",
        "agent_oidc_issuer",
        "agent_oidc_audience",
        "agent_oidc_jwks_url",
        "agent_alert_webhook_url",
        "agent_alert_webhook_bearer_token",
        mode="before",
    )
    @classmethod
    def empty_optional_secret_value_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def model_post_init(self, __context: object) -> None:
        if (
            self.agent_checkpoint_pool_max_size
            < self.agent_checkpoint_pool_min_size
        ):
            raise ValueError(
                "AGENT_CHECKPOINT_POOL_MAX_SIZE 不能小于 MIN_SIZE"
            )
        digest = (
            self.agent_approval_api_key_sha256.get_secret_value().strip()
            if self.agent_approval_api_key_sha256 is not None
            else ""
        )
        if digest and re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            raise ValueError("AGENT_APPROVAL_API_KEY_SHA256 必须是 64 位十六进制")
        if self.agent_approval_actor_id is not None and (
            not self.agent_approval_actor_id.strip()
            or self.agent_approval_actor_id != self.agent_approval_actor_id.strip()
        ):
            raise ValueError("AGENT_APPROVAL_ACTOR_ID 不能是空白或带首尾空格")
        if bool(digest) != (self.agent_approval_actor_id is not None):
            raise ValueError("审批 API Key 摘要和审批人标识必须同时配置")
        if not self.agent_approval_api_key_role_set:
            raise ValueError("AGENT_APPROVAL_API_KEY_ROLES 至少需要一个角色")
        if any(
            re.fullmatch(r"[a-z][a-z0-9_]{0,63}", role) is None
            for role in self.agent_approval_api_key_role_set
        ):
            raise ValueError("AGENT_APPROVAL_API_KEY_ROLES 包含无效角色")
        if self.agent_oidc_enabled:
            if not self.agent_approval_auth_required:
                raise ValueError("启用 OIDC 时必须强制 Agent 认证")
            if not all(
                (self.agent_oidc_issuer, self.agent_oidc_audience, self.agent_oidc_jwks_url)
            ):
                raise ValueError("启用 OIDC 时必须配置 issuer、audience 和 JWKS URL")
            jwks_url = urlparse(self.agent_oidc_jwks_url or "")
            is_local_http = (
                jwks_url.scheme == "http"
                and jwks_url.hostname in {"127.0.0.1", "localhost", "::1"}
                and self.environment.lower() in {"development", "test", "testing"}
            )
            if jwks_url.scheme != "https" and not is_local_http:
                raise ValueError("OIDC JWKS URL 必须使用 HTTPS（本地测试除外）")
        if self.agent_approval_auth_required and not (
            (digest and self.agent_approval_actor_id is not None)
            or self.agent_oidc_enabled
        ):
            raise ValueError(
                "强制 Agent 认证时必须配置 API Key 或启用 OIDC"
            )
        if (
            self.agent_alert_retry_max_delay_seconds
            < self.agent_alert_retry_base_delay_seconds
        ):
            raise ValueError(
                "AGENT_ALERT_RETRY_MAX_DELAY_SECONDS 不能小于 BASE_DELAY_SECONDS"
            )
        if (
            self.agent_alert_lease_seconds
            <= self.agent_alert_delivery_timeout_seconds
        ):
            raise ValueError(
                "AGENT_ALERT_LEASE_SECONDS 必须大于投递超时时间"
            )
        if self.agent_alert_scheduler_enabled and (
            self.agent_alert_webhook_url is None
        ):
            raise ValueError(
                "启用告警调度器时必须配置 AGENT_ALERT_WEBHOOK_URL"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
