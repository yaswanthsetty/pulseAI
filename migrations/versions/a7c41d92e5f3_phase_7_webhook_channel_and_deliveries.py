"""Add webhook channel and notification_deliveries table.

- ``notification_rules.channel`` now also allows 'webhook' (email delivery
  lands in the Phase 7 delivery path; 'webhook' posts the event payload to a
  user-configured URL).
- ``notification_deliveries`` records every delivery attempt so the in-app
  inbox and ops debugging have a durable trail (spec §20 notification_rules).

Revision ID: a7c41d92e5f3
Revises: 8f937dbe0ff7
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c41d92e5f3"
down_revision: str | Sequence[str] | None = "8f937dbe0ff7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Widen the channel check to allow 'webhook'.
    # NOTE: the project's MetaData naming convention prefixes check names with
    # "ck_<table>_". Names passed here go through that convention too, so the
    # pre-convention token ("channel_valid") is used and op.f() marks the
    # already-conformed name in create_check_constraint.
    op.drop_constraint(
        op.f("ck_notification_rules_channel_valid"),
        "notification_rules",
        type_="check",
    )
    op.create_check_constraint(
        op.f("channel_valid"),
        "notification_rules",
        "channel IN ('email', 'in_app', 'webhook')",
    )

    op.create_table(
        "notification_deliveries",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "rule_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.UUID(),
            nullable=True,
        ),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["notification_rules.id"],
            name=op.f("fk_notification_deliveries_rule_id_notification_rules"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_deliveries_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_notification_deliveries_event_id_events"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.CheckConstraint(
            "channel IN ('email', 'in_app', 'webhook')",
            name=op.f("channel_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('sent', 'failed', 'pending')",
            name=op.f("status_valid"),
        ),
    )
    op.create_index(
        "ix_notification_deliveries_user",
        "notification_deliveries",
        ["user_id", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_user", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_constraint(
        op.f("ck_notification_rules_channel_valid"),
        "notification_rules",
        type_="check",
    )
    op.create_check_constraint(
        op.f("channel_valid"),
        "notification_rules",
        "channel IN ('email', 'in_app')",
    )
