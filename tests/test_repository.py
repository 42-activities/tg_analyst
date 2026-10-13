from datetime import datetime, timedelta

from sqlalchemy import func, select

from tg_analyst.storage import repository as repo
from tg_analyst.storage.models import Interaction, Message, User
from tg_analyst.storage.records import GroupRecord, MessageRecord, UserRecord

GID = -100999
T0 = datetime(2026, 1, 1, 12)


def msg(mid, sender, **kw):
    return MessageRecord(
        group_id=GID, message_id=mid, sender_id=sender, timestamp=T0 + timedelta(minutes=mid), **kw
    )


def seed(session, messages):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    repo.upsert_users(
        session,
        [(UserRecord(1, "alice"), T0), (UserRecord(2, "bob"), T0), (UserRecord(3, "carol"), T0)],
    )
    repo.upsert_messages(session, messages)
    session.commit()


def edges(session):
    return sorted(
        session.execute(
            select(
                Interaction.message_id,
                Interaction.source_user_id,
                Interaction.target_user_id,
                Interaction.type,
            )
        ).all()
    )


def test_upsert_messages_is_idempotent_and_refreshes_mutable_fields(session):
    seed(session, [msg(1, 1, text="v1", views=1)])
    repo.upsert_messages(session, [msg(1, 1, text="edited", views=10)])
    session.commit()
    row = session.get(Message, (GID, 1))
    assert (row.text, row.views) == ("edited", 10)
    assert session.scalar(select(func.count()).select_from(Message)) == 1


def test_upsert_users_widens_seen_window_and_dedupes(session):
    early, late = T0 - timedelta(days=3), T0 + timedelta(days=3)
    repo.upsert_users(session, [(UserRecord(1, "alice"), T0)])
    repo.upsert_users(session, [(UserRecord(1, None, "Alice"), late), (UserRecord(1, None), early)])
    session.commit()
    u = session.get(User, 1)
    assert (u.first_seen, u.last_seen) == (early, late)
    assert u.username == "alice"  # a null username does not erase a known one
    assert u.display_name == "Alice"


def test_high_water_mark_only_moves_forward(session):
    seed(session, [])
    repo.advance_high_water_mark(session, GID, 50)
    repo.advance_high_water_mark(session, GID, 20)
    assert repo.get_high_water_mark(session, GID) == 50


def test_build_interactions_replies_and_mentions(session):
    seed(
        session,
        [
            msg(1, 1, text="question"),
            msg(2, 2, text="answer", reply_to_msg_id=1),  # bob -> alice (reply)
            msg(3, 1, text="self", reply_to_msg_id=1),  # self-reply: ignored
            msg(
                4, 3, text="@Bob look", mention_usernames=["Bob"]
            ),  # carol -> bob (case-insensitive)
            msg(5, 3, text="hey", mention_user_ids=[1, 3]),  # carol -> alice; self-mention dropped
            msg(6, 2, text="orphan", reply_to_msg_id=999),  # parent not stored: ignored
            msg(7, 1, text="@ghost", mention_usernames=["ghost"]),  # unknown username: ignored
        ],
    )
    assert repo.build_interactions(session, GID) == 3
    session.commit()
    assert edges(session) == [(2, 2, 1, "reply"), (4, 3, 2, "mention"), (5, 3, 1, "mention")]

    # Re-running is a no-op.
    assert repo.build_interactions(session, GID) == 0


def test_build_interactions_respects_min_message_id(session):
    seed(session, [msg(1, 1), msg(2, 2, reply_to_msg_id=1), msg(3, 1, reply_to_msg_id=2)])
    repo.build_interactions(session, GID, min_message_id=3)
    session.commit()
    assert edges(session) == [(3, 1, 2, "reply")]
