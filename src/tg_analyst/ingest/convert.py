"""Convert Telethon objects into plain storage records.

Kept free of network calls so it can be unit-tested with lightweight fakes.
"""

from datetime import UTC, datetime

from telethon.tl import types

from tg_analyst.storage.records import GroupRecord, MessageRecord, UserRecord

_JOIN_ACTIONS = (
    types.MessageActionChatAddUser,
    types.MessageActionChatJoinedByLink,
    types.MessageActionChatJoinedByRequest,
)


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def group_to_record(entity, group_id: int) -> GroupRecord:
    return GroupRecord(
        group_id=group_id,
        title=getattr(entity, "title", None),
        username=getattr(entity, "username", None),
        member_count=getattr(entity, "participants_count", None),
    )


def sender_to_record(sender) -> UserRecord | None:
    if isinstance(sender, types.User):
        name = " ".join(p for p in (sender.first_name, sender.last_name) if p) or None
        return UserRecord(
            user_id=sender.id,
            username=sender.username,
            display_name=name,
            is_bot=bool(sender.bot),
        )
    if isinstance(sender, types.Channel):
        # Anonymous admin or linked channel posting into the group.
        from telethon.utils import get_peer_id

        return UserRecord(
            user_id=get_peer_id(sender),
            username=sender.username,
            display_name=sender.title,
            is_channel=True,
        )
    return None


def _media_type(media) -> str | None:
    if media is None:
        return None
    if isinstance(media, types.MessageMediaPhoto):
        return "photo"
    if isinstance(media, types.MessageMediaDocument):
        doc = media.document
        for attr in getattr(doc, "attributes", None) or []:
            if isinstance(attr, types.DocumentAttributeSticker):
                return "sticker"
            if isinstance(attr, types.DocumentAttributeAnimated):
                return "gif"
            if isinstance(attr, types.DocumentAttributeAudio):
                return "voice" if attr.voice else "audio"
            if isinstance(attr, types.DocumentAttributeVideo):
                return "video_note" if attr.round_message else "video"
        return "document"
    if isinstance(media, types.MessageMediaPoll):
        return "poll"
    if isinstance(media, types.MessageMediaWebPage):
        return "webpage"
    if isinstance(
        media, types.MessageMediaGeo | types.MessageMediaGeoLive | types.MessageMediaVenue
    ):
        return "location"
    if isinstance(media, types.MessageMediaContact):
        return "contact"
    return "other"


def _mentions(text: str | None, entities) -> tuple[list[int], list[str]]:
    ids: list[int] = []
    names: list[str] = []
    for ent in entities or []:
        if isinstance(ent, types.MessageEntityMentionName):
            ids.append(ent.user_id)
        elif isinstance(ent, types.InputMessageEntityMentionName):
            uid = getattr(ent.user_id, "user_id", None)
            if uid is not None:
                ids.append(uid)
        elif isinstance(ent, types.MessageEntityMention) and text:
            # Telegram offsets are in UTF-16 code units.
            raw = text.encode("utf-16-le")[ent.offset * 2 : (ent.offset + ent.length) * 2]
            names.append(raw.decode("utf-16-le").lstrip("@"))
    return sorted(set(ids)), sorted(set(names))


def _service_action(action) -> tuple[str, list[int]]:
    if isinstance(action, types.MessageActionChatAddUser):
        return "join", list(action.users)
    if isinstance(action, _JOIN_ACTIONS):
        return "join", []
    if isinstance(action, types.MessageActionChatDeleteUser):
        return "leave", [action.user_id]
    if isinstance(action, types.MessageActionPinMessage):
        return "pin", []
    return type(action).__name__.removeprefix("MessageAction").lower(), []


def message_to_record(msg, group_id: int) -> MessageRecord:
    reply = getattr(msg, "reply_to", None)
    reply_to_msg_id = getattr(reply, "reply_to_msg_id", None)
    thread_id = getattr(reply, "reply_to_top_id", None) or (
        reply_to_msg_id if getattr(reply, "forum_topic", False) else None
    )

    record = MessageRecord(
        group_id=group_id,
        message_id=msg.id,
        timestamp=to_naive_utc(msg.date),
        sender_id=msg.sender_id,
        reply_to_msg_id=reply_to_msg_id,
        thread_id=thread_id,
        views=getattr(msg, "views", None),
        forwards=getattr(msg, "forwards", None),
        is_forwarded=getattr(msg, "fwd_from", None) is not None,
    )

    action = getattr(msg, "action", None)
    if action is not None:
        record.is_service = True
        record.service_action, record.action_user_ids = _service_action(action)
        # A self-join is sent by the joiner; make sure they count as the joining user.
        if record.service_action == "join" and not record.action_user_ids and msg.sender_id:
            record.action_user_ids = [msg.sender_id]
        return record

    text = getattr(msg, "message", None) or None
    record.text = text
    record.media_type = _media_type(getattr(msg, "media", None))
    record.has_media = record.media_type not in (None, "webpage")
    record.mention_user_ids, record.mention_usernames = _mentions(
        text, getattr(msg, "entities", None)
    )
    return record
