from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def dimension(self) -> int:
        """Return the configured vector dimension."""

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Generate embeddings for document chunks while preserving input order."""

    async def embed_query(self, text: str) -> list[float]:
        """Generate one embedding for a search query."""

    async def aclose(self) -> None:
        """Release provider-owned network resources."""
