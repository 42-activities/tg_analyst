"""excluded users

Revision ID: 0002_excluded_users
Revises: 0001_baseline
Create Date: 2026-10-14 00:26:03.585548
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_excluded_users"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "excluded_users",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("excluded_users")
