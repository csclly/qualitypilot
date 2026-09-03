"""Add lease and retry state to the Agent alert outbox."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011_agent_alert_delivery"
down_revision = "0010_agent_alert_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_alert_outbox",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_alert_outbox",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_alert_outbox",
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_alert_outbox",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_alert_outbox",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_alert_outbox",
        sa.Column("last_error_kind", sa.String(length=100), nullable=True),
    )
    op.create_check_constraint(
        "ck_agent_alert_outbox_attempt_count",
        "agent_alert_outbox",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_agent_alert_outbox_lease_state",
        "agent_alert_outbox",
        "(status = 'delivering' AND lease_token IS NOT NULL "
        "AND lease_expires_at IS NOT NULL) OR "
        "(status <> 'delivering' AND lease_token IS NULL "
        "AND lease_expires_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_agent_alert_outbox_delivered_state",
        "agent_alert_outbox",
        "(status = 'delivered' AND delivered_at IS NOT NULL) OR "
        "(status <> 'delivered' AND delivered_at IS NULL)",
    )
    op.create_index(
        "ix_agent_alert_outbox_delivery_schedule",
        "agent_alert_outbox",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    active_rows = connection.scalar(
        sa.text(
            "SELECT count(*) FROM agent_alert_outbox "
            "WHERE attempt_count > 0 OR status <> 'pending'"
        )
    ) or 0
    if active_rows:
        raise RuntimeError(
            "拒绝降级：已有告警投递历史，请先完成迁移或归档"
        )
    op.drop_index(
        "ix_agent_alert_outbox_delivery_schedule",
        table_name="agent_alert_outbox",
    )
    op.drop_constraint(
        "ck_agent_alert_outbox_delivered_state",
        "agent_alert_outbox",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_alert_outbox_lease_state",
        "agent_alert_outbox",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_alert_outbox_attempt_count",
        "agent_alert_outbox",
        type_="check",
    )
    op.drop_column("agent_alert_outbox", "last_error_kind")
    op.drop_column("agent_alert_outbox", "delivered_at")
    op.drop_column("agent_alert_outbox", "lease_expires_at")
    op.drop_column("agent_alert_outbox", "lease_token")
    op.drop_column("agent_alert_outbox", "next_attempt_at")
    op.drop_column("agent_alert_outbox", "attempt_count")
