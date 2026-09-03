from enum import Enum
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    source_type: str = Field(default="manual", max_length=50)
    source_uri: str | None = Field(default=None, max_length=1000)


class DocumentResponse(DocumentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    original_filename: str | None = None
    content_type: str | None = None
    file_size: int | None = None
    checksum_sha256: str | None = None
    storage_path: str | None = None
    chunk_count: int = 0
    processed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DocumentChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    char_start: int | None
    char_end: int | None
    has_embedding: bool
    embedding_dimension: int | None
    created_at: datetime


class DocumentEmbeddingBackfillResponse(BaseModel):
    document_id: uuid.UUID
    total_chunks: int
    embedded_chunks: int
    skipped_chunks: int


class KnowledgeSearchMode(str, Enum):
    VECTOR = "vector"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


class KnowledgeSearchRequest(BaseModel):
    query: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    ]
    top_k: int = Field(default=5, ge=1, le=50)
    mode: KnowledgeSearchMode = KnowledgeSearchMode.VECTOR


class KnowledgeSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    source_type: str
    source_uri: str | None
    original_filename: str | None
    chunk_index: int
    content: str
    char_start: int | None
    char_end: int | None
    score: float
    match_type: KnowledgeSearchMode
    vector_score: float | None = None
    keyword_score: float | None = None


class AgentRunStatus(str, Enum):
    CREATED = "created"
    RETRIEVING = "retrieving"
    QUERYING_BUSINESS_CONTEXT = "querying_business_context"
    DRAFTING = "drafting"
    PENDING_APPROVAL = "pending_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"


class AgentGenerationMode(str, Enum):
    MODEL = "model"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class AgentRunCreate(BaseModel):
    use_model: bool = Field(
        default=True,
        description="是否调用文本生成模型；false 时直接生成规则草稿。",
    )
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    ]
    top_k: int = Field(default=5, ge=1, le=20)
    search_mode: KnowledgeSearchMode = KnowledgeSearchMode.HYBRID


class AgentApprovalRequest(BaseModel):
    approved: bool
    actor_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    ] = "unverified"
    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    comment: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, max_length=2000),
    ] = None


class AgentEvidenceResponse(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    source_uri: str | None
    original_filename: str | None
    chunk_index: int
    content: str
    score: float
    match_type: KnowledgeSearchMode
    vector_score: float | None = None
    keyword_score: float | None = None


class AgentBusinessReferenceResponse(BaseModel):
    tool_name: str
    record_id: str


class AgentRecommendationResponse(BaseModel):
    summary: str
    suggested_actions: list[str]
    risk_notes: list[str]
    citations: list[uuid.UUID] = Field(default_factory=list)
    business_record_references: list[AgentBusinessReferenceResponse] = Field(
        default_factory=list
    )
    generation_mode: AgentGenerationMode = AgentGenerationMode.DETERMINISTIC_FALLBACK


class AgentBusinessRecordResponse(BaseModel):
    tool_name: str
    system: Literal["mes", "qms"]
    record_id: str
    record_type: str
    summary: str
    attributes: dict[str, str | int | float | bool | None]


class AgentBusinessToolFailureResponse(BaseModel):
    tool_name: str
    system: Literal["mes", "qms"]
    kind: Literal[
        "timeout",
        "authentication",
        "permission",
        "unavailable",
        "invalid_response",
        "unexpected",
    ]
    message: str
    retryable: bool


class AgentApprovalAuditResponse(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    event_type: Literal["approval_decision"] = "approval_decision"
    actor_id: str
    actor_authenticated: bool = False
    auth_method: Literal["api_key_sha256", "oidc_jwt_rs256"] | None = None
    approved: bool
    comment: str | None = None
    occurred_at: datetime


class AgentRunErrorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    stage: Literal["retrieval", "business_context", "drafting", "workflow"]
    error_kind: str
    message: str
    retryable: bool
    occurred_at: datetime


class AgentMetricsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    generated_at: datetime
    window_started_at: datetime
    window_hours: int
    approval_decisions: int
    authenticated_approvals: int
    approved_decisions: int
    rejected_decisions: int
    error_events: int
    affected_runs: int
    retryable_errors: int
    errors_by_stage: dict[str, int]
    errors_by_kind: dict[str, int]
    alert_status: Literal["ok", "warning"]
    alert_threshold: int


class AgentAlertOutboxResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fingerprint: str
    window_started_at: datetime
    window_ended_at: datetime
    window_hours: int
    error_events: int
    alert_threshold: int
    status: Literal["pending", "delivering", "delivered", "failed"]
    attempt_count: int
    next_attempt_at: datetime
    lease_expires_at: datetime | None = None
    delivered_at: datetime | None = None
    last_error_kind: str | None = None
    created_at: datetime


class AgentAlertEvaluationResponse(BaseModel):
    triggered: bool
    queued: bool
    metrics: AgentMetricsResponse
    alert: AgentAlertOutboxResponse | None = None


class AgentAlertDeliveryResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    outcome: Literal["idle", "delivered", "retry_scheduled", "failed"]
    alert: AgentAlertOutboxResponse | None = None


class AgentAlertProcessingResponse(BaseModel):
    processed: int
    delivered: int
    retry_scheduled: int
    failed: int
    results: list[AgentAlertDeliveryResultResponse]


class AgentAlertSchedulerStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    running: bool
    cycles_completed: int
    cycles_failed: int
    alerts_queued: int
    alerts_processed: int
    alerts_delivered: int
    alerts_retry_scheduled: int
    alerts_failed: int
    last_cycle_started_at: datetime | None = None
    last_cycle_completed_at: datetime | None = None
    last_error_kind: str | None = None


class AgentCheckpointRetentionCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    thread_id: uuid.UUID
    last_checkpoint_at: datetime
    checkpoint_count: int
    blob_count: int
    write_count: int


class AgentRetentionPreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cutoff_at: datetime
    checkpoint_candidates: list[AgentCheckpointRetentionCandidateResponse]
    approval_events_before_cutoff: int
    run_error_events_before_cutoff: int
    terminal_alerts_before_cutoff: int


class AgentCheckpointArchiveRequest(BaseModel):
    confirm_thread_id: uuid.UUID
    older_than_days: int | None = Field(default=None, ge=1, le=3650)


class AgentCheckpointRestoreRequest(BaseModel):
    confirm_archive_id: uuid.UUID


class AgentCheckpointArchiveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    status: Literal["archived", "restored"]
    cutoff_at: datetime
    source_last_checkpoint_at: datetime
    checkpoint_count: int
    blob_count: int
    write_count: int
    archived_by: str
    actor_authenticated: bool
    auth_method: Literal["api_key_sha256", "oidc_jwt_rs256"] | None = None
    archived_at: datetime
    restored_by: str | None = None
    restored_at: datetime | None = None


class AgentCheckpointArchiveActionResponse(BaseModel):
    changed: bool
    archive: AgentCheckpointArchiveResponse


class AgentCheckpointArchiveListResponse(BaseModel):
    items: list[AgentCheckpointArchiveResponse]


class AgentRunResponse(BaseModel):
    run_id: uuid.UUID
    question: str
    search_mode: KnowledgeSearchMode
    top_k: int
    status: AgentRunStatus
    evidence: list[AgentEvidenceResponse]
    business_records: list[AgentBusinessRecordResponse] = Field(default_factory=list)
    business_tool_failures: list[AgentBusinessToolFailureResponse] = Field(
        default_factory=list
    )
    draft: AgentRecommendationResponse | None = None
    approval_required: bool
    approved: bool | None = None
    approval_comment: str | None = None
    approval_event: AgentApprovalAuditResponse | None = None
    final_response: AgentRecommendationResponse | None = None
