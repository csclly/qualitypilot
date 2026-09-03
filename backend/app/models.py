import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Document(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    source_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="created")
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "knowledge_document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
        Index(
            "ix_knowledge_document_chunks_embedding_hnsw_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("embedding IS NOT NULL"),
        ),
        Index(
            "ix_knowledge_document_chunks_content_gist_trgm",
            "content",
            postgresql_using="gist",
            postgresql_ops={"content": "gist_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Nullable embeddings support staged vectorization and retrying failed jobs.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)

    @property
    def has_embedding(self) -> bool:
        return self.embedding is not None

    @property
    def embedding_dimension(self) -> int | None:
        return len(self.embedding) if self.embedding is not None else None

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class AgentAuditEvent(Base):
    __tablename__ = "agent_audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type = 'approval_decision'",
            name="ck_agent_audit_events_type",
        ),
        CheckConstraint(
            "length(trim(actor_id)) > 0",
            name="ck_agent_audit_events_actor_id",
        ),
        CheckConstraint(
            "(actor_authenticated AND auth_method IS NOT NULL "
            "AND length(trim(auth_method)) > 0) "
            "OR (NOT actor_authenticated AND auth_method IS NULL)",
            name="ck_agent_audit_events_auth_provenance",
        ),
        UniqueConstraint(
            "run_id",
            "event_type",
            name="uq_agent_audit_events_run_type",
        ),
        Index("ix_agent_audit_events_run_id", "run_id"),
        Index("ix_agent_audit_events_occurred_at", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_authenticated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    auth_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AgentRunErrorEvent(Base):
    __tablename__ = "agent_run_error_events"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('retrieval', 'business_context', 'drafting', 'workflow')",
            name="ck_agent_run_error_events_stage",
        ),
        Index("ix_agent_run_error_events_run_id", "run_id"),
        Index("ix_agent_run_error_events_occurred_at", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    error_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AgentAlertOutbox(Base):
    __tablename__ = "agent_alert_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'delivering', 'delivered', 'failed')",
            name="ck_agent_alert_outbox_status",
        ),
        CheckConstraint(
            "error_events >= alert_threshold AND alert_threshold > 0",
            name="ck_agent_alert_outbox_threshold",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_agent_alert_outbox_attempt_count",
        ),
        CheckConstraint(
            "(status = 'delivering' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'delivering' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_agent_alert_outbox_lease_state",
        ),
        CheckConstraint(
            "(status = 'delivered' AND delivered_at IS NOT NULL) OR "
            "(status <> 'delivered' AND delivered_at IS NULL)",
            name="ck_agent_alert_outbox_delivered_state",
        ),
        UniqueConstraint("fingerprint", name="uq_agent_alert_outbox_fingerprint"),
        Index("ix_agent_alert_outbox_status", "status"),
        Index("ix_agent_alert_outbox_created_at", "created_at"),
        Index(
            "ix_agent_alert_outbox_delivery_schedule",
            "status",
            "next_attempt_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    window_ended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    window_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    error_events: Mapped[int] = mapped_column(Integer, nullable=False)
    alert_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_kind: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AgentCheckpointArchive(Base):
    __tablename__ = "agent_checkpoint_archives"
    __table_args__ = (
        CheckConstraint(
            "status IN ('archived', 'restored')",
            name="ck_agent_checkpoint_archives_status",
        ),
        CheckConstraint(
            "checkpoint_count > 0 AND blob_count >= 0 AND write_count >= 0",
            name="ck_agent_checkpoint_archives_counts",
        ),
        CheckConstraint(
            "(status = 'archived' AND restored_at IS NULL "
            "AND restored_by IS NULL) OR "
            "(status = 'restored' AND restored_at IS NOT NULL "
            "AND restored_by IS NOT NULL)",
            name="ck_agent_checkpoint_archives_restore_state",
        ),
        CheckConstraint(
            "(actor_authenticated AND auth_method IS NOT NULL) OR "
            "(NOT actor_authenticated AND auth_method IS NULL)",
            name="ck_agent_checkpoint_archives_auth_provenance",
        ),
        Index(
            "uq_agent_checkpoint_archives_active_thread",
            "thread_id",
            unique=True,
            postgresql_where=text("status = 'archived'"),
        ),
        Index(
            "ix_agent_checkpoint_archives_archived_at",
            "archived_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'archived'"),
    )
    cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    source_last_checkpoint_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    checkpoint_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_count: Mapped[int] = mapped_column(Integer, nullable=False)
    write_count: Mapped[int] = mapped_column(Integer, nullable=False)
    archived_by: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_authenticated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    auth_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    restored_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AgentCheckpointArchiveCheckpoint(Base):
    __tablename__ = "agent_checkpoint_archive_checkpoints"

    archive_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_checkpoint_archives.id", ondelete="CASCADE"),
        primary_key=True,
    )
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class AgentCheckpointArchiveBlob(Base):
    __tablename__ = "agent_checkpoint_archive_blobs"

    archive_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_checkpoint_archives.id", ondelete="CASCADE"),
        primary_key=True,
    )
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    blob: Mapped[bytes | None] = mapped_column(nullable=True)


class AgentCheckpointArchiveWrite(Base):
    __tablename__ = "agent_checkpoint_archive_writes"

    archive_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_checkpoint_archives.id", ondelete="CASCADE"),
        primary_key=True,
    )
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    blob: Mapped[bytes] = mapped_column(nullable=False)
    task_path: Mapped[str] = mapped_column(Text, nullable=False)
