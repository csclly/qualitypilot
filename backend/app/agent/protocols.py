from dataclasses import dataclass
from typing import Protocol

from app.agent.business_tools import ReadOnlyBusinessTool
from app.agent.state import (
    AgentBusinessRecord,
    AgentBusinessToolFailure,
    AgentEvidence,
    AgentRecommendation,
)


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
        business_records: list[AgentBusinessRecord],
        business_tool_failures: list[AgentBusinessToolFailure],
    ) -> AgentRecommendation: ...


@dataclass(frozen=True, slots=True)
class AgentRuntimeContext:
    retriever: EvidenceRetriever
    generator: RecommendationGenerator
    business_tools: tuple[ReadOnlyBusinessTool, ...] = ()
    business_tool_limit: int = 10
    business_tool_timeout_seconds: float = 5.0
