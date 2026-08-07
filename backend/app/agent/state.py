from typing import Literal, TypedDict


class AgentEvidence(TypedDict):
    chunk_id: str
    document_id: str
    document_title: str
    source_uri: str | None
    original_filename: str | None
    chunk_index: int
    content: str
    score: float
    match_type: str
    vector_score: float | None
    keyword_score: float | None


class AgentRecommendation(TypedDict):
    summary: str
    suggested_actions: list[str]
    risk_notes: list[str]
    citations: list[str]
    generation_mode: Literal["model", "deterministic_fallback"]


class QualityAgentState(TypedDict, total=False):
    run_id: str
    question: str
    search_mode: str
    top_k: int
    status: str
    evidence: list[AgentEvidence]
    draft: AgentRecommendation
    approved: bool
    approval_comment: str | None
    final_response: AgentRecommendation | None
