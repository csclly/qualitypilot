"""Add PostgreSQL persistence tables for LangGraph Agent checkpoints."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_agent_checkpoints"
down_revision = "0005_trigram_search"
branch_labels = None
depends_on = None


CHECKPOINT_INDEXES = (
    ("checkpoints_thread_id_idx", "checkpoints"),
    ("checkpoint_blobs_thread_id_idx", "checkpoint_blobs"),
    ("checkpoint_writes_thread_id_idx", "checkpoint_writes"),
)


def upgrade() -> None:
    op.create_table(
        "checkpoint_migrations",
        sa.Column(
            "v",
            sa.Integer(),
            nullable=False,
            autoincrement=False,
        ),
        sa.PrimaryKeyConstraint("v"),
    )
    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_ns",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text()),
        sa.Column("type", sa.Text()),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
    )
    op.create_table(
        "checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_ns",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary()),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "channel",
            "version",
        ),
    )
    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_ns",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text()),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column(
            "task_path",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
        ),
    )
    for index_name, table_name in CHECKPOINT_INDEXES:
        op.create_index(index_name, table_name, ["thread_id"])

    # Mark every schema step bundled with langgraph-checkpoint-postgres 3.1.1.
    op.bulk_insert(
        sa.table("checkpoint_migrations", sa.column("v", sa.Integer())),
        [{"v": version} for version in range(10)],
    )


def downgrade() -> None:
    connection = op.get_bind()
    stored_rows = sum(
        connection.scalar(sa.text(f"SELECT count(*) FROM {table_name}")) or 0
        for table_name in (
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
        )
    )
    if stored_rows:
        raise RuntimeError(
            "拒绝降级：LangGraph 检查点表中仍有运行状态，请先迁移或归档"
        )

    for index_name, table_name in reversed(CHECKPOINT_INDEXES):
        op.drop_index(index_name, table_name=table_name)
    op.drop_table("checkpoint_writes")
    op.drop_table("checkpoint_blobs")
    op.drop_table("checkpoints")
    op.drop_table("checkpoint_migrations")
