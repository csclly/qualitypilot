"""Add a durable, deduplicated Agent alert outbox."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010_agent_alert_outbox"
down_revision = "0009_agent_run_errors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_alert_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column("error_events", sa.Integer(), nullable=False),
        sa.Column("alert_threshold", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivering', 'delivered', 'failed')",
            name="ck_agent_alert_outbox_status",
        ),
        sa.CheckConstraint(
            "error_events >= alert_threshold AND alert_threshold > 0",
            name="ck_agent_alert_outbox_threshold",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fingerprint",
            name="uq_agent_alert_outbox_fingerprint",
        ),
    )
    op.create_index(
        "ix_agent_alert_outbox_status",
        "agent_alert_outbox",
        ["status"],
    )
    op.create_index(
        "ix_agent_alert_outbox_created_at",
        "agent_alert_outbox",
        ["created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    stored_rows = connection.scalar(
        sa.text("SELECT count(*) FROM agent_alert_outbox")
    ) or 0
    if stored_rows:
        raise RuntimeError(
            "拒绝降级：Agent 告警 Outbox 仍有记录，请先完成迁移或归档"
        )
    op.drop_index(
        "ix_agent_alert_outbox_created_at",
        table_name="agent_alert_outbox",
    )
    op.drop_index(
        "ix_agent_alert_outbox_status",
        table_name="agent_alert_outbox",
    )
    op.drop_table("agent_alert_outbox")
