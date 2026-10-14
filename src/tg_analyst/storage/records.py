"""Plain records passed from ingestion (or the synthetic generator) into storage."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class GroupRecord:
    group_id: int
    title: str | None = None
    username: str | None = None
    member_count: int | None = None


@dataclass(slots=True)
class UserRecord:
    user_id: int
    username: str | None = None
    display_name: str | None = None
    is_bot: bool = False
    is_channel: bool = False


@dataclass(slots=True)
class MessageRecord:
    group_id: int
    message_id: int
    timestamp: datetime  # naive UTC
    sender_id: int | None = None
    text: str | None = None
    reply_to_msg_id: int | None = None
    thread_id: int | None = None
    views: int | None = None
    forwards: int | None = None
    has_media: bool = False
    media_type: str | None = None
    is_forwarded: bool = False
    mention_user_ids: list[int] = field(default_factory=list)
    mention_usernames: list[str] = field(default_factory=list)
    is_service: bool = False
    service_action: str | None = None
    action_user_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ReactionRecord:
    user_id: int
    emoji: str  # the emoji, or "custom"
    timestamp: datetime | None = None  # naive UTC


@dataclass(slots=True)
class ReactionSummary:
    """The reactions of one message as Telegram reports them.

    ``reactors`` may be partial: a message only carries its few most recent reactors.
    ``complete`` says whether they cover ``total``; ``visible`` is False when the group
    hides who reacted, in which case only the total is known.
    """

    message_id: int
    total: int
    reactors: list[ReactionRecord] = field(default_factory=list)
    visible: bool = True
    listed: bool = False  # reactors come from the full reaction list, not the summary

    @property
    def complete(self) -> bool:
        return self.visible and (self.listed or len(self.reactors) >= self.total)
