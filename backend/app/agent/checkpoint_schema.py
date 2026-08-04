from sqlalchemy import (
    Column,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB


def register_checkpoint_tables(metadata: MetaData) -> None:
    """Register the schema required by langgraph-checkpoint-postgres 3.1.1."""
    if "checkpoint_migrations" in metadata.tables:
        return

    Table(
        "checkpoint_migrations",
        metadata,
        Column("v", Integer, primary_key=True, autoincrement=False),
    )
    checkpoints = Table(
        "checkpoints",
        metadata,
        Column("thread_id", Text, nullable=False),
        Column("checkpoint_ns", Text, nullable=False, server_default=text("''")),
        Column("checkpoint_id", Text, nullable=False),
        Column("parent_checkpoint_id", Text),
        Column("type", Text),
        Column("checkpoint", JSONB, nullable=False),
        Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
    )
    checkpoint_blobs = Table(
        "checkpoint_blobs",
        metadata,
        Column("thread_id", Text, nullable=False),
        Column("checkpoint_ns", Text, nullable=False, server_default=text("''")),
        Column("channel", Text, nullable=False),
        Column("version", Text, nullable=False),
        Column("type", Text, nullable=False),
        Column("blob", LargeBinary),
        PrimaryKeyConstraint("thread_id", "checkpoint_ns", "channel", "version"),
    )
    checkpoint_writes = Table(
        "checkpoint_writes",
        metadata,
        Column("thread_id", Text, nullable=False),
        Column("checkpoint_ns", Text, nullable=False, server_default=text("''")),
        Column("checkpoint_id", Text, nullable=False),
        Column("task_id", Text, nullable=False),
        Column("idx", Integer, nullable=False),
        Column("channel", Text, nullable=False),
        Column("type", Text),
        Column("blob", LargeBinary, nullable=False),
        Column("task_path", Text, nullable=False, server_default=text("''")),
        PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
        ),
    )
    Index("checkpoints_thread_id_idx", checkpoints.c.thread_id)
    Index("checkpoint_blobs_thread_id_idx", checkpoint_blobs.c.thread_id)
    Index("checkpoint_writes_thread_id_idx", checkpoint_writes.c.thread_id)
