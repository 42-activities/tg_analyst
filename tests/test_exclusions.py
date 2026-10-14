"""Privacy opt-out: excluded members are purged and never ingested again."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from tg_analyst import jobs
from tg_analyst.ingest.service import _persist
from tg_analyst.storage import repository as repo
from tg_analyst.storage.db import make_session_factory
from tg_analyst.storage.models import Enrichment, ExcludedUser, Interaction, Job, Message
from tg_analyst.storage.records import GroupRecord, MessageRecord, UserRecord

GID = -100777
T0 = datetime(2026, 1, 1, 12)
ME, ALICE, BOB = 117, 1, 2


def msg(mid, sender, **kw):
    return MessageRecord(GID, mid, T0 + timedelta(minutes=mid), sender_id=sender, **kw)


def count(session, model, *where):
    return session.scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def chat(session):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    repo.upsert_users(
        session,
        [
            (UserRecord(ME, "Me_Myself"), T0),
            (UserRecord(ALICE, "alice"), T0),
            (UserRecord(BOB, "bob"), T0),
        ],
    )
    repo.upsert_messages(
        session,
        [
            msg(1, ME, text="hi all"),
            msg(2, ALICE, text="hey", reply_to_msg_id=1),  # reply to me
            msg(3, ME, text="thanks", reply_to_msg_id=2),  # my reply to alice
            msg(4, BOB, text="@me_myself look", mention_usernames=["me_myself"]),  # mention of me
            msg(5, BOB, text="@alice ok", mention_usernames=["alice"]),
        ],
    )
    repo.upsert_enrichment(
        session,
        [
            {
                "group_id": GID,
                "message_id": i,
                "sentiment_score": 0.1,
                "sentiment_label": "neutral",
                "toxicity": None,
                "intent": None,
                "topics": None,
                "model_version": "t",
            }
            for i in range(1, 6)
        ],
    )
    repo.build_interactions(session, GID)
    session.commit()
    return session


def test_exclude_purges_messages_and_edges(chat):
    assert count(chat, Interaction) == 4
    removed = repo.exclude_user(chat, ME, "my own account")
    chat.commit()

    assert removed == {"messages": 2, "interactions": 3, "reactions": 0}
    assert count(chat, Message, Message.sender_id == ME) == 0
    assert count(chat, Enrichment) == 3  # cascaded with my two messages
    remaining = chat.execute(select(Interaction.source_user_id, Interaction.target_user_id)).all()
    assert remaining == [(BOB, ALICE)]
    assert chat.get(ExcludedUser, ME).note == "my own account"


def test_exclude_is_idempotent_and_include_lifts_it(chat):
    repo.exclude_user(chat, ME)
    assert repo.exclude_user(chat, ME, "again") == {
        "messages": 0,
        "interactions": 0,
        "reactions": 0,
    }
    assert repo.excluded_ids(chat) == {ME}
    assert repo.include_user(chat, ME) is True
    assert repo.include_user(chat, ME) is False
    assert repo.excluded_ids(chat) == set()


def test_resolve_user(chat):
    assert repo.resolve_user(chat, "@ME_MYSELF") == ME
    assert repo.resolve_user(chat, " 117 ") == ME
    with pytest.raises(ValueError, match="numeric Telegram id"):
        repo.resolve_user(chat, "@nobody")


def test_ingestion_skips_excluded_senders_but_advances_the_resume_point(chat):
    repo.exclude_user(chat, ME)
    chat.commit()
    batch = [
        (msg(10, ALICE, text="new"), UserRecord(ALICE, "alice")),
        (msg(11, ME, text="mine"), UserRecord(ME, "me_myself")),
    ]
    skipped = _persist(chat, GID, batch, excluded=repo.excluded_ids(chat))
    assert skipped == 1
    assert count(chat, Message, Message.message_id == 11) == 0
    assert count(chat, Message, Message.message_id == 10) == 1
    assert repo.get_high_water_mark(chat, GID) == 11


def test_new_mentions_of_excluded_members_create_no_edges(chat):
    repo.exclude_user(chat, ME)
    repo.upsert_messages(
        chat,
        [
            msg(
                20,
                BOB,
                text="@me_myself again",
                mention_usernames=["me_myself"],
                mention_user_ids=[ME],
            )
        ],
    )
    repo.build_interactions(chat, GID)
    chat.commit()
    assert count(chat, Interaction, Interaction.target_user_id == ME) == 0


def test_exclude_and_include_jobs(engine, chat):
    factory = make_session_factory(engine)
    with factory() as s:
        jobs.enqueue(s, "exclude", {"user": "@me_myself", "note": "me"})
        jobs.enqueue(s, "include", {"user": "@nobody"})
    jobs.run_one(factory)
    jobs.run_one(factory)
    with factory() as s:
        done, failed = s.scalars(select(Job).order_by(Job.id)).all()
        assert done.status == jobs.DONE and "removed 2 messages" in done.result
        assert failed.status == jobs.FAILED and "numeric Telegram id" in failed.error
        assert repo.excluded_ids(s) == {ME}
