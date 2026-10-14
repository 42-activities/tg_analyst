"""Keeping the database in sync with Telegram: deletions, edits, scheduled jobs."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from telethon.tl import types

from tg_analyst import jobs
from tg_analyst.ingest import service
from tg_analyst.storage import repository as repo
from tg_analyst.storage.db import make_session_factory
from tg_analyst.storage.models import Enrichment, Interaction, Job, Message
from tg_analyst.storage.records import GroupRecord, MessageRecord, UserRecord

GID = -100555
T0 = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)


def rec(mid, sender, text, **kw):
    return MessageRecord(GID, mid, T0 + timedelta(minutes=mid), sender_id=sender, text=text, **kw)


def count(session, model, *where):
    return session.scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def chat(session):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    repo.upsert_users(session, [(UserRecord(1, "alice"), T0), (UserRecord(2, "bob"), T0)])
    repo.upsert_messages(
        session,
        [
            rec(1, 1, "question"),
            rec(2, 2, "answer", reply_to_msg_id=1),
            rec(3, 2, "plain note"),
        ],
    )
    repo.upsert_enrichment(
        session,
        [
            {
                "group_id": GID,
                "message_id": i,
                "sentiment_score": 0.0,
                "sentiment_label": "neutral",
                "toxicity": None,
                "intent": None,
                "topics": None,
                "model_version": "v",
            }
            for i in (1, 2, 3)
        ],
    )
    repo.build_interactions(session, GID)
    session.commit()
    return session


def test_delete_messages_cascades(chat):
    assert repo.delete_messages(chat, GID, [1, 99]) == 1
    chat.commit()
    assert count(chat, Message) == 2
    assert count(chat, Interaction) == 1  # bob->alice reply edge lives on message 2
    assert count(chat, Enrichment) == 2
    assert repo.delete_messages(chat, GID, []) == 0


def test_apply_edits_rescores_and_rebuilds_mentions(chat):
    edited = rec(3, 2, "@alice see this", mention_usernames=["alice"])
    unchanged = rec(1, 1, "question")
    assert repo.apply_edits(chat, GID, [edited, unchanged, rec(99, 1, "not stored")]) == 1
    chat.commit()
    assert chat.get(Message, (GID, 3)).text == "@alice see this"
    assert chat.get(Enrichment, (GID, 3)) is None  # re-scored by the next enrich run
    assert chat.get(Enrichment, (GID, 1)) is not None
    assert count(chat, Interaction, Interaction.message_id == 3, Interaction.type == "mention") == 1


class _FakeTelegram:
    """get_messages(ids=...) like Telethon: aligned list, None for deleted ids."""

    def __init__(self, alive: dict[int, str]):
        self.alive, self.calls = alive, 0

    async def get_messages(self, entity, ids):
        self.calls += 1
        return [
            SimpleNamespace(
                id=i,
                date=T0 + timedelta(minutes=i),
                sender_id=2 if i != 1 else 1,
                message=self.alive[i],
                reply_to=None,
                views=None,
                forwards=None,
                media=None,
                entities=None,
                action=None,
                fwd_from=None,
            )
            if i in self.alive
            else None
            for i in ids
        ]


async def test_reconcile_applies_deletions_and_edits(engine, chat, monkeypatch):
    async def fake_resolve(client, factory, group):
        return "entity", GID

    monkeypatch.setattr(service, "resolve_group", fake_resolve)
    monkeypatch.setattr(service, "RECONCILE_BATCH", 2)
    tg = _FakeTelegram({1: "question", 3: "plain note (edited)"})  # message 2 deleted
    stats = await service.reconcile(tg, make_session_factory(engine), str(GID), days=30)

    assert stats == {"checked": 3, "deleted": 1, "edited": 1, "reactions_updated": 0}
    assert tg.calls == 2  # batches of 2
    chat.expire_all()
    assert chat.get(Message, (GID, 2)) is None
    assert chat.get(Message, (GID, 3)).text == "plain note (edited)"


async def test_reconcile_window_skips_old_messages(engine, chat, monkeypatch):
    async def fake_resolve(client, factory, group):
        return "entity", GID

    monkeypatch.setattr(service, "resolve_group", fake_resolve)
    tg = _FakeTelegram({})
    stats = await service.reconcile(tg, make_session_factory(engine), str(GID), days=1)
    assert stats["checked"] == 0 and tg.calls == 0  # all messages are 2 days old


async def test_empty_message_counts_as_deleted(engine, chat, monkeypatch):
    async def fake_resolve(client, factory, group):
        return "entity", GID

    class _EmptyForDeleted(_FakeTelegram):
        async def get_messages(self, entity, ids):
            found = await super().get_messages(entity, ids)
            return [
                m or types.MessageEmpty(id=i, peer_id=None) for i, m in zip(ids, found, strict=True)
            ]

    monkeypatch.setattr(service, "resolve_group", fake_resolve)
    tg = _EmptyForDeleted({1: "question", 2: "answer"})
    stats = await service.reconcile(tg, make_session_factory(engine), str(GID), days=30)
    assert stats["deleted"] == 1 and stats["edited"] == 0


def test_schedule_due(chat):
    now = datetime(2026, 10, 14, 12, 0)
    created = jobs.schedule_due(
        chat, now, refresh_minutes=60, reconcile_hours=24, reconcile_days=30
    )
    assert sorted(j.kind for j in created) == ["backfill", "sync"]
    backfill = next(j for j in created if j.kind == "backfill")
    assert backfill.params == {"group": str(GID), "enrich_after": True, "scheduled": True}

    # Nothing new within the interval (also covers worker restarts and deploys).
    assert jobs.schedule_due(chat, now + timedelta(minutes=30), 60, 24, 30) == []
    later = jobs.schedule_due(chat, now + timedelta(minutes=61), 60, 24, 30)
    assert [j.kind for j in later] == ["backfill"]
    assert jobs.schedule_due(chat, now + timedelta(days=2), 0, 0, 30) == []
    assert count(chat, Job) == 3
