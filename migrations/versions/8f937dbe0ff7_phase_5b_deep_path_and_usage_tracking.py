"""phase 5b deep path and usage tracking

Revision ID: 8f937dbe0ff7
Revises: 533617f49985
Create Date: 2026-08-19 22:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f937dbe0ff7"
down_revision: str | Sequence[str] | None = "533617f49985"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # FR-22: evidence agreement score on conversation messages
    op.add_column(
        "conversation_messages",
        sa.Column("evidence_agreement", sa.Float(), nullable=True),
    )

    # Cost / token tracking table
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_usage_user",
        "llm_usage",
        ["user_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_llm_usage_operation",
        "llm_usage",
        ["operation", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_llm_usage_operation", table_name="llm_usage")
    op.drop_index("ix_llm_usage_user", table_name="llm_usage")
    op.drop_table("llm_usage")
    op.drop_column("conversation_messages", "evidence_agreement")
