class GenerationServiceError(RuntimeError):
    """Base error for model configuration, transport, API, and response failures."""


class GenerationConfigurationError(GenerationServiceError):
    """Raised when the text-generation configuration is unusable."""


class GenerationTransportError(GenerationServiceError):
    """Raised when the text-generation API cannot be reached."""


class GenerationTimeoutError(GenerationTransportError):
    """Raised when a generation request exceeds its timeout."""


class GenerationAPIError(GenerationServiceError):
    """Raised when the remote model returns a non-success status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code == 429 or status_code >= 500


class GenerationResponseError(GenerationServiceError):
    """Raised when the model response violates the structured contract."""
