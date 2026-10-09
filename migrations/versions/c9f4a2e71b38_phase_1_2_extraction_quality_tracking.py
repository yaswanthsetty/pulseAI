"""phase 1.2 clean extraction tracking (quality, extractor, version)

Revision ID: c9f4a2e71b38
Revises: a7c41d92e5f3
Create Date: 2026-10-09 13:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f4a2e71b38"
down_revision: str | Sequence[str] | None = "a7c41d92e5f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "articles",
        sa.Column(
            "extraction_quality",
            sa.String(16),
            nullable=False,
            server_default="ok",
        ),
    )
    op.add_column("articles", sa.Column("extractor", sa.String(16), nullable=True))
    op.add_column("articles", sa.Column("extractor_version", sa.String(16), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("articles", "extractor_version")
    op.drop_column("articles", "extractor")
    op.drop_column("articles", "extraction_quality")
