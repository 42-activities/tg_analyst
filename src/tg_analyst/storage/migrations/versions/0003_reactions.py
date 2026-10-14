"""reactions

Revision ID: 0003_reactions
Revises: 0002_excluded_users
Create Date: 2026-10-14 07:56:36.443351
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_reactions"
down_revision = "0002_excluded_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reactions",
        sa.Column("group_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("message_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("emoji", sa.String(length=32), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id", "message_id"],
            ["messages.group_id", "messages.message_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "message_id", "user_id", "emoji"),
    )
    op.create_index("ix_reactions_user", "reactions", ["user_id"], unique=False)
    op.add_column("messages", sa.Column("reaction_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "reaction_count")
    op.drop_index("ix_reactions_user", table_name="reactions")
    op.drop_table("reactions")
