"""ORM models. All timestamps are stored as naive UTC datetimes."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Group(Base):
    __tablename__ = "groups"

    # Marked peer id as returned by telethon.utils.get_peer_id (e.g. -100123...).
    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64))
    member_count: Mapped[int | None] = mapped_column(Integer)
    # Highest message id ingested so far; back-fills resume from here.
    last_message_id: Mapped[int] = mapped_column(Integer, default=0)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    # Messages sent "as a channel" (anonymous admins, linked channels).
    is_channel: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)


class Message(Base):
    __tablename__ = "messages"

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    text: Mapped[str | None] = mapped_column(Text)
    reply_to_msg_id: Mapped[int | None] = mapped_column(Integer)
    thread_id: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    forwards: Mapped[int | None] = mapped_column(Integer)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    media_type: Mapped[str | None] = mapped_column(String(32))
    is_forwarded: Mapped[bool] = mapped_column(Boolean, default=False)
    # Raw mention targets; resolved into `interactions` by build_interactions().
    mention_user_ids: Mapped[list[int] | None] = mapped_column(JSON)
    mention_usernames: Mapped[list[str] | None] = mapped_column(JSON)
    # Service messages (joins, leaves, pins...) carry no text but matter for retention.
    is_service: Mapped[bool] = mapped_column(Boolean, default=False)
    service_action: Mapped[str | None] = mapped_column(String(32))
    action_user_ids: Mapped[list[int] | None] = mapped_column(JSON)

    __table_args__ = (Index("ix_messages_group_reply", "group_id", "reply_to_msg_id"),)


class Interaction(Base):
    """A directed edge between two members, derived from a message."""

    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer)
    source_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    type: Mapped[str] = mapped_column(String(16))  # reply | mention | reaction
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)

    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "message_id",
            "source_user_id",
            "target_user_id",
            "type",
            name="uq_interaction",
        ),
        ForeignKeyConstraint(
            ["group_id", "message_id"],
            ["messages.group_id", "messages.message_id"],
            ondelete="CASCADE",
        ),
    )


class Enrichment(Base):
    __tablename__ = "enrichment"

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sentiment_score: Mapped[float | None] = mapped_column(Float)  # -1.0 .. +1.0
    sentiment_label: Mapped[str | None] = mapped_column(String(16))
    toxicity: Mapped[float | None] = mapped_column(Float)
    intent: Mapped[str | None] = mapped_column(String(32))
    topics: Mapped[list[str] | None] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        ForeignKeyConstraint(
            ["group_id", "message_id"],
            ["messages.group_id", "messages.message_id"],
            ondelete="CASCADE",
        ),
    )
