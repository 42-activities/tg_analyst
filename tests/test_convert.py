from datetime import UTC, datetime
from types import SimpleNamespace

from telethon.tl import types

from tg_analyst.ingest.convert import message_to_record, sender_to_record

GID = -100123


def fake_message(**kw):
    defaults = dict(
        id=10,
        date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        sender_id=1,
        message="hello",
        reply_to=None,
        views=5,
        forwards=0,
        media=None,
        entities=None,
        action=None,
        fwd_from=None,
    )
    return SimpleNamespace(**{**defaults, **kw})


def test_plain_text_message():
    rec = message_to_record(fake_message(), GID)
    assert rec.group_id == GID and rec.message_id == 10 and rec.sender_id == 1
    assert rec.text == "hello"
    assert rec.timestamp == datetime(2026, 3, 1, 12, 0)  # naive UTC
    assert rec.timestamp.tzinfo is None
    assert not rec.is_service and not rec.has_media


def test_reply_header():
    reply = types.MessageReplyHeader(reply_to_msg_id=7, reply_to_top_id=3)
    rec = message_to_record(fake_message(reply_to=reply), GID)
    assert rec.reply_to_msg_id == 7
    assert rec.thread_id == 3


def test_mentions_by_id_and_username_with_utf16_offsets():
    text = "🙂 hi @alice and Bob"
    # The emoji is 2 UTF-16 units, so "@alice" starts at offset 6.
    entities = [
        types.MessageEntityMention(offset=6, length=6),
        types.MessageEntityMentionName(offset=17, length=3, user_id=42),
    ]
    rec = message_to_record(fake_message(message=text, entities=entities), GID)
    assert rec.mention_usernames == ["alice"]
    assert rec.mention_user_ids == [42]


def test_media_types():
    photo = types.MessageMediaPhoto()
    sticker = types.MessageMediaDocument(
        document=types.Document(
            id=1,
            access_hash=1,
            file_reference=b"",
            date=None,
            mime_type="image/webp",
            size=1,
            dc_id=1,
            attributes=[
                types.DocumentAttributeSticker(alt="", stickerset=types.InputStickerSetEmpty())
            ],
        )
    )
    assert message_to_record(fake_message(media=photo), GID).media_type == "photo"
    rec = message_to_record(fake_message(media=sticker, message=""), GID)
    assert rec.media_type == "sticker" and rec.has_media and rec.text is None


def test_webpage_preview_is_not_media():
    rec = message_to_record(
        fake_message(media=types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=1))), GID
    )
    assert rec.media_type == "webpage" and not rec.has_media


def test_join_service_messages():
    added = message_to_record(
        fake_message(action=types.MessageActionChatAddUser(users=[5, 6]), sender_id=1), GID
    )
    assert added.is_service and added.service_action == "join"
    assert added.action_user_ids == [5, 6]

    by_link = message_to_record(
        fake_message(action=types.MessageActionChatJoinedByLink(inviter_id=1), sender_id=9), GID
    )
    assert by_link.service_action == "join" and by_link.action_user_ids == [9]

    left = message_to_record(
        fake_message(action=types.MessageActionChatDeleteUser(user_id=9), sender_id=9), GID
    )
    assert left.service_action == "leave" and left.action_user_ids == [9]


def test_sender_records():
    user = types.User(id=1, first_name="Ann", last_name="Lee", username="ann", bot=False)
    assert sender_to_record(user).display_name == "Ann Lee"
    bot = types.User(id=2, first_name="Bot", bot=True)
    assert sender_to_record(bot).is_bot
    assert sender_to_record(None) is None
