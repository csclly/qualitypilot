"""Record whether an Agent approval actor was authenticated."""

from alembic import op
import sqlalchemy as sa


revision = "0008_audit_actor_auth"
down_revision = "0007_agent_audit_events"
branch_labels = None
depends_on = None


AUTH_CONSTRAINT = "ck_agent_audit_events_auth_provenance"


def upgrade() -> None:
    op.add_column(
        "agent_audit_events",
        sa.Column(
            "actor_authenticated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_audit_events",
        sa.Column("auth_method", sa.String(length=50), nullable=True),
    )
    op.create_check_constraint(
        AUTH_CONSTRAINT,
        "agent_audit_events",
        "(actor_authenticated AND auth_method IS NOT NULL "
        "AND length(trim(auth_method)) > 0) "
        "OR (NOT actor_authenticated AND auth_method IS NULL)",
    )


def downgrade() -> None:
    connection = op.get_bind()
    stored_rows = connection.scalar(
        sa.text("SELECT count(*) FROM agent_audit_events")
    ) or 0
    if stored_rows:
        raise RuntimeError(
            "拒绝降级：审批身份来源已写入审计事件，请先完成合规归档"
        )
    op.drop_constraint(
        AUTH_CONSTRAINT,
        "agent_audit_events",
        type_="check",
    )
    op.drop_column("agent_audit_events", "auth_method")
    op.drop_column("agent_audit_events", "actor_authenticated")
