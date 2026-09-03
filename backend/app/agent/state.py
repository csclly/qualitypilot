from typing import Literal, TypeAlias, TypedDict


BusinessValue: TypeAlias = str | int | float | bool | None


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


class AgentBusinessRecord(TypedDict):
    tool_name: str
    system: Literal["mes", "qms"]
    record_id: str
    record_type: str
    summary: str
    attributes: dict[str, BusinessValue]


class AgentBusinessToolFailure(TypedDict):
    tool_name: str
    system: Literal["mes", "qms"]
    kind: str
    message: str
    retryable: bool


class AgentBusinessReference(TypedDict):
    tool_name: str
    record_id: str


class AgentRecommendation(TypedDict):
    summary: str
    suggested_actions: list[str]
    risk_notes: list[str]
    citations: list[str]
    business_record_references: list[AgentBusinessReference]
    generation_mode: Literal["model", "deterministic_fallback"]


class QualityAgentState(TypedDict, total=False):
    run_id: str
    question: str
    search_mode: str
    top_k: int
    status: str
    evidence: list[AgentEvidence]
    business_records: list[AgentBusinessRecord]
    business_tool_failures: list[AgentBusinessToolFailure]
    draft: AgentRecommendation
    approved: bool
    approval_comment: str | None
    final_response: AgentRecommendation | None
