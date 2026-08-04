from dataclasses import dataclass
from typing import Protocol

from app.agent.state import AgentEvidence, AgentRecommendation


class EvidenceRetriever(Protocol):
    async def __call__(
        self,
        question: str,
        *,
        top_k: int,
        mode: str,
    ) -> list[AgentEvidence]: ...


class RecommendationGenerator(Protocol):
    async def generate(
        self,
        question: str,
        evidence: list[AgentEvidence],
    ) -> AgentRecommendation: ...


@dataclass(frozen=True, slots=True)
class AgentRuntimeContext:
    retriever: EvidenceRetriever
    generator: RecommendationGenerator
