class EmbeddingServiceError(RuntimeError):
    """Base error for embedding configuration, transport, API, and response failures."""


class EmbeddingConfigurationError(EmbeddingServiceError):
    """Raised when the embedding provider configuration is unusable."""


class EmbeddingTransportError(EmbeddingServiceError):
    """Raised when the embedding API cannot be reached."""


class EmbeddingTimeoutError(EmbeddingTransportError):
    """Raised when an embedding request exceeds the configured timeout."""


class EmbeddingAPIError(EmbeddingServiceError):
    """Raised when the remote embedding API returns a non-success status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = status_code == 429 or status_code >= 500


class EmbeddingResponseError(EmbeddingServiceError):
    """Raised when an embedding API response violates the expected contract."""
