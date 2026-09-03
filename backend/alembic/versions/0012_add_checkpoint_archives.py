"""Add timestamped, immutable LangGraph checkpoint archives."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_checkpoint_archives"
down_revision = "0011_agent_alert_delivery"
branch_labels = None
depends_on = None


ARCHIVE_PAYLOAD_TABLES = (
    "agent_checkpoint_archive_checkpoints",
    "agent_checkpoint_archive_blobs",
    "agent_checkpoint_archive_writes",
)


def upgrade() -> None:
    op.add_column(
        "checkpoints",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "agent_checkpoint_archives",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'archived'"),
            nullable=False,
        ),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_last_checkpoint_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("checkpoint_count", sa.Integer(), nullable=False),
        sa.Column("blob_count", sa.Integer(), nullable=False),
        sa.Column("write_count", sa.Integer(), nullable=False),
        sa.Column("archived_by", sa.String(length=255), nullable=False),
        sa.Column("actor_authenticated", sa.Boolean(), nullable=False),
        sa.Column("auth_method", sa.String(length=50), nullable=True),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("restored_by", sa.String(length=255), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('archived', 'restored')",
            name="ck_agent_checkpoint_archives_status",
        ),
        sa.CheckConstraint(
            "checkpoint_count > 0 AND blob_count >= 0 AND write_count >= 0",
            name="ck_agent_checkpoint_archives_counts",
        ),
        sa.CheckConstraint(
            "(status = 'archived' AND restored_at IS NULL "
            "AND restored_by IS NULL) OR "
            "(status = 'restored' AND restored_at IS NOT NULL "
            "AND restored_by IS NOT NULL)",
            name="ck_agent_checkpoint_archives_restore_state",
        ),
        sa.CheckConstraint(
            "(actor_authenticated AND auth_method IS NOT NULL) OR "
            "(NOT actor_authenticated AND auth_method IS NULL)",
            name="ck_agent_checkpoint_archives_auth_provenance",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_agent_checkpoint_archives_active_thread",
        "agent_checkpoint_archives",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status = 'archived'"),
    )
    op.create_index(
        "ix_agent_checkpoint_archives_archived_at",
        "agent_checkpoint_archives",
        ["archived_at"],
    )
    op.create_table(
        "agent_checkpoint_archive_checkpoints",
        sa.Column(
            "archive_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_checkpoint_archives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("archive_id", "checkpoint_ns", "checkpoint_id"),
    )
    op.create_table(
        "agent_checkpoint_archive_blobs",
        sa.Column(
            "archive_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_checkpoint_archives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint(
            "archive_id",
            "checkpoint_ns",
            "channel",
            "version",
        ),
    )
    op.create_table(
        "agent_checkpoint_archive_writes",
        sa.Column(
            "archive_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_checkpoint_archives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "archive_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_agent_checkpoint_archive_payload_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'agent checkpoint archive payload is immutable';
        END;
        $$
        """
    )
    for table_name in ARCHIVE_PAYLOAD_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE OR TRUNCATE ON {table_name}
            FOR EACH STATEMENT
            EXECUTE FUNCTION prevent_agent_checkpoint_archive_payload_mutation()
            """
        )
    op.execute(
        """
        CREATE FUNCTION protect_agent_checkpoint_archive_manifest()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
                RAISE EXCEPTION 'agent checkpoint archive manifest is protected';
            END IF;
            IF OLD.status = 'archived' AND NEW.status = 'restored'
               AND NEW.id = OLD.id
               AND NEW.thread_id = OLD.thread_id
               AND NEW.cutoff_at = OLD.cutoff_at
               AND NEW.source_last_checkpoint_at = OLD.source_last_checkpoint_at
               AND NEW.checkpoint_count = OLD.checkpoint_count
               AND NEW.blob_count = OLD.blob_count
               AND NEW.write_count = OLD.write_count
               AND NEW.archived_by = OLD.archived_by
               AND NEW.actor_authenticated = OLD.actor_authenticated
               AND NEW.auth_method IS NOT DISTINCT FROM OLD.auth_method
               AND NEW.archived_at = OLD.archived_at
               AND NEW.restored_by IS NOT NULL
               AND NEW.restored_at IS NOT NULL THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'agent checkpoint archive manifest is immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_checkpoint_archives_protected_rows
        BEFORE UPDATE OR DELETE ON agent_checkpoint_archives
        FOR EACH ROW EXECUTE FUNCTION protect_agent_checkpoint_archive_manifest()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_checkpoint_archives_protected_truncate
        BEFORE TRUNCATE ON agent_checkpoint_archives
        FOR EACH STATEMENT EXECUTE FUNCTION protect_agent_checkpoint_archive_manifest()
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    stored_rows = connection.scalar(
        sa.text("SELECT count(*) FROM agent_checkpoint_archives")
    ) or 0
    if stored_rows:
        raise RuntimeError(
            "拒绝降级：检查点归档仍存在，请先迁移到外部合规存储"
        )
    op.execute(
        "DROP TRIGGER trg_agent_checkpoint_archives_protected_truncate "
        "ON agent_checkpoint_archives"
    )
    op.execute(
        "DROP TRIGGER trg_agent_checkpoint_archives_protected_rows "
        "ON agent_checkpoint_archives"
    )
    op.execute("DROP FUNCTION protect_agent_checkpoint_archive_manifest()")
    for table_name in reversed(ARCHIVE_PAYLOAD_TABLES):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION prevent_agent_checkpoint_archive_payload_mutation()")
    for table_name in reversed(ARCHIVE_PAYLOAD_TABLES):
        op.drop_table(table_name)
    op.drop_index(
        "ix_agent_checkpoint_archives_archived_at",
        table_name="agent_checkpoint_archives",
    )
    op.drop_index(
        "uq_agent_checkpoint_archives_active_thread",
        table_name="agent_checkpoint_archives",
    )
    op.drop_table("agent_checkpoint_archives")
    op.drop_column("checkpoints", "created_at")
