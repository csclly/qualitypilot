from fastapi import HTTPException, status

from app.services.embedding.errors import (
    EmbeddingAPIError,
    EmbeddingConfigurationError,
    EmbeddingResponseError,
    EmbeddingServiceError,
)


def embedding_http_exception(exc: EmbeddingServiceError) -> HTTPException:
    if isinstance(exc, EmbeddingConfigurationError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Embedding 服务未配置",
        )
    if isinstance(exc, EmbeddingAPIError):
        return HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.retryable
                else status.HTTP_502_BAD_GATEWAY
            ),
            detail="Embedding 服务返回异常",
        )
    if isinstance(exc, EmbeddingResponseError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Embedding 服务返回异常",
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Embedding 服务暂时不可用",
    )
