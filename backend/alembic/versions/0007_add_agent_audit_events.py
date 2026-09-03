"""Add immutable Agent approval audit events."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007_agent_audit_events"
down_revision = "0006_agent_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type = 'approval_decision'",
            name="ck_agent_audit_events_type",
        ),
        sa.CheckConstraint(
            "length(trim(actor_id)) > 0",
            name="ck_agent_audit_events_actor_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "event_type",
            name="uq_agent_audit_events_run_type",
        ),
    )
    op.create_index(
        "ix_agent_audit_events_run_id",
        "agent_audit_events",
        ["run_id"],
    )
    op.create_index(
        "ix_agent_audit_events_occurred_at",
        "agent_audit_events",
        ["occurred_at"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_agent_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'agent audit events are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_audit_events_immutable
        BEFORE UPDATE OR DELETE OR TRUNCATE ON agent_audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION prevent_agent_audit_event_mutation()
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    stored_rows = connection.scalar(
        sa.text("SELECT count(*) FROM agent_audit_events")
    ) or 0
    if stored_rows:
        raise RuntimeError(
            "拒绝降级：Agent 审计事件仍存在，请先完成合规归档"
        )
    op.execute(
        "DROP TRIGGER trg_agent_audit_events_immutable "
        "ON agent_audit_events"
    )
    op.execute("DROP FUNCTION prevent_agent_audit_event_mutation()")
    op.drop_index(
        "ix_agent_audit_events_occurred_at",
        table_name="agent_audit_events",
    )
    op.drop_index(
        "ix_agent_audit_events_run_id",
        table_name="agent_audit_events",
    )
    op.drop_table("agent_audit_events")
