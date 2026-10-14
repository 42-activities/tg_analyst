"""toxicity labels

Revision ID: 0004_toxicity_labels
Revises: 0003_reactions
Create Date: 2026-10-14 08:33:20.199642
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_toxicity_labels"
down_revision = "0003_reactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("enrichment", sa.Column("insult", sa.Float(), nullable=True))
    op.add_column("enrichment", sa.Column("obscenity", sa.Float(), nullable=True))
    op.add_column("enrichment", sa.Column("threat", sa.Float(), nullable=True))
    # Sentiment + toxicity model names no longer fit in 64 characters.
    op.alter_column(
        "enrichment",
        "model_version",
        type_=sa.String(length=255),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "enrichment",
        "model_version",
        type_=sa.String(length=64),
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
    op.drop_column("enrichment", "threat")
    op.drop_column("enrichment", "obscenity")
    op.drop_column("enrichment", "insult")
