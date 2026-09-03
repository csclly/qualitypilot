"""Add immutable Agent node error history."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_agent_run_errors"
down_revision = "0008_audit_actor_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_run_error_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("error_kind", sa.String(length=100), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage IN ('retrieval', 'business_context', 'drafting', 'workflow')",
            name="ck_agent_run_error_events_stage",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_run_error_events_run_id",
        "agent_run_error_events",
        ["run_id"],
    )
    op.create_index(
        "ix_agent_run_error_events_occurred_at",
        "agent_run_error_events",
        ["occurred_at"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_agent_run_error_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'agent run error events are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_run_error_events_immutable
        BEFORE UPDATE OR DELETE OR TRUNCATE ON agent_run_error_events
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_agent_run_error_event_mutation()
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    stored_rows = connection.scalar(
        sa.text("SELECT count(*) FROM agent_run_error_events")
    ) or 0
    if stored_rows:
        raise RuntimeError(
            "拒绝降级：Agent 运行错误历史仍存在，请先完成合规归档"
        )
    op.execute(
        "DROP TRIGGER trg_agent_run_error_events_immutable "
        "ON agent_run_error_events"
    )
    op.execute("DROP FUNCTION prevent_agent_run_error_event_mutation()")
    op.drop_index(
        "ix_agent_run_error_events_occurred_at",
        table_name="agent_run_error_events",
    )
    op.drop_index(
        "ix_agent_run_error_events_run_id",
        table_name="agent_run_error_events",
    )
    op.drop_table("agent_run_error_events")
