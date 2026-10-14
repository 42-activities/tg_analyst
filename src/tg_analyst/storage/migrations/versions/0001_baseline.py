"""baseline: schema before migrations

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-14 00:19:08.790749
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table(
        "groups",
        sa.Column("group_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=True),
        sa.Column("last_message_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("group_id"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)
    op.create_table(
        "messages",
        sa.Column("group_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("message_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("reply_to_msg_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("forwards", sa.Integer(), nullable=True),
        sa.Column("has_media", sa.Boolean(), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=True),
        sa.Column("is_forwarded", sa.Boolean(), nullable=False),
        sa.Column("mention_user_ids", sa.JSON(), nullable=True),
        sa.Column("mention_usernames", sa.JSON(), nullable=True),
        sa.Column("is_service", sa.Boolean(), nullable=False),
        sa.Column("service_action", sa.String(length=32), nullable=True),
        sa.Column("action_user_ids", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("group_id", "message_id"),
    )
    op.create_index(
        "ix_messages_group_reply", "messages", ["group_id", "reply_to_msg_id"], unique=False
    )
    op.create_index(op.f("ix_messages_sender_id"), "messages", ["sender_id"], unique=False)
    op.create_index(op.f("ix_messages_timestamp"), "messages", ["timestamp"], unique=False)
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False),
        sa.Column("is_channel", sa.Boolean(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)
    op.create_table(
        "enrichment",
        sa.Column("group_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("message_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("sentiment_label", sa.String(length=16), nullable=True),
        sa.Column("toxicity", sa.Float(), nullable=True),
        sa.Column("intent", sa.String(length=32), nullable=True),
        sa.Column("topics", sa.JSON(), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id", "message_id"],
            ["messages.group_id", "messages.message_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "message_id"),
    )
    op.create_table(
        "interactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("source_user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id", "message_id"],
            ["messages.group_id", "messages.message_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id",
            "message_id",
            "source_user_id",
            "target_user_id",
            "type",
            name="uq_interaction",
        ),
    )
    op.create_index(
        op.f("ix_interactions_source_user_id"), "interactions", ["source_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_interactions_target_user_id"), "interactions", ["target_user_id"], unique=False
    )
    op.create_index(op.f("ix_interactions_timestamp"), "interactions", ["timestamp"], unique=False)


def downgrade() -> None:

    op.drop_index(op.f("ix_interactions_timestamp"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_target_user_id"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_source_user_id"), table_name="interactions")
    op.drop_table("interactions")
    op.drop_table("enrichment")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_messages_timestamp"), table_name="messages")
    op.drop_index(op.f("ix_messages_sender_id"), table_name="messages")
    op.drop_index("ix_messages_group_reply", table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("groups")
