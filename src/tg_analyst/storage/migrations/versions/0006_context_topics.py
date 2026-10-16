"""context topics

Revision ID: 0006_context_topics
Revises: 0005_job_progress
Create Date: 2026-10-16 20:30:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_context_topics"
down_revision = "0005_job_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "context_topics",
        sa.Column("group_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_model", sa.String(length=64), nullable=True),
        sa.Column("summary_key", sa.String(length=40), nullable=True),
        sa.Column("centre_root", sa.Integer(), nullable=False),
        sa.Column("edge_roots", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("thread_roots", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.group_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "topic_id"),
    )
    op.create_table(
        "thread_embeddings",
        sa.Column("group_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("root_message_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("message_ids", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("content_key", sa.String(length=40), nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("vector", postgresql.ARRAY(sa.REAL()), nullable=False),
        sa.Column("start", sa.DateTime(), nullable=False),
        sa.Column("end", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id", "root_message_id"],
            ["messages.group_id", "messages.message_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "root_message_id"),
    )


def downgrade() -> None:
    op.drop_table("thread_embeddings")
    op.drop_table("context_topics")
