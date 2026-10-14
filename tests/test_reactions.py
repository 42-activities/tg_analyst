"""Reactions: conversion, storage, edges with emoji tone, sync, and synthetic ground truth."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import func, select
from telethon.tl import types
from telethon.tl.functions.messages import GetMessageReactionsListRequest
from telethon.tl.types.messages import MessageReactionsList

from tg_analyst import synthetic
from tg_analyst.analytics import conflict, load
from tg_analyst.enrich.reactions import HOSTILE, POSITIVE, WARM, emoji_tone
from tg_analyst.ingest import service
from tg_analyst.ingest.convert import reactions_to_summary
from tg_analyst.storage import repository as repo
from tg_analyst.storage.db import make_session_factory
from tg_analyst.storage.models import Interaction, Message, Reaction
from tg_analyst.storage.records import (
    GroupRecord,
    MessageRecord,
    ReactionRecord,
    ReactionSummary,
    UserRecord,
)

GID = -100777
ALICE, BOB, CAROL = 1, 2, 3
T0 = datetime.now(UTC).replace(tzinfo=None, microsecond=0) - timedelta(days=2)
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def count(session, model, *where):
    return session.scalar(select(func.count()).select_from(model).where(*where))


def peer_reaction(user_id: int, emoji: str | None = None, custom: bool = False):
    reaction = types.ReactionCustomEmoji(document_id=5) if custom else types.ReactionEmoji(emoji)
    return types.MessagePeerReaction(peer_id=types.PeerUser(user_id), date=NOW, reaction=reaction)


def test_emoji_tone():
    assert emoji_tone("👍") == emoji_tone("👍🏽") == POSITIVE  # skin tones don't matter
    assert emoji_tone("❤️") == emoji_tone("❤") == emoji_tone("❤️‍🔥") == WARM
    assert emoji_tone("🤡") == HOSTILE and emoji_tone("👎") < 0
    assert emoji_tone("🤔") == emoji_tone("custom") == emoji_tone(None) == 0.0


def test_reactions_to_summary():
    msg = SimpleNamespace(
        id=7,
        reactions=types.MessageReactions(
            results=[
                types.ReactionCount(types.ReactionEmoji("👍"), 3),
                types.ReactionCount(types.ReactionCustomEmoji(document_id=5), 1),
                types.ReactionCount(types.ReactionPaid(), 50),  # paid stars are not counted
            ],
            can_see_list=True,
            recent_reactions=[peer_reaction(BOB, "👍"), peer_reaction(CAROL, custom=True)],
        ),
    )
    s = reactions_to_summary(msg)
    assert s.message_id == 7 and s.total == 4 and s.visible and not s.complete
    assert [(r.user_id, r.emoji) for r in s.reactors] == [(BOB, "👍"), (CAROL, "custom")]
    assert s.reactors[0].timestamp == NOW.replace(tzinfo=None)

    none = reactions_to_summary(SimpleNamespace(id=8, reactions=None))
    assert none.total == 0 and none.complete  # reactions taken back get removed

    hidden = reactions_to_summary(
        SimpleNamespace(
            id=9,
            reactions=types.MessageReactions(
                results=[types.ReactionCount(types.ReactionEmoji("👍"), 2)]
            ),
        )
    )
    assert hidden.total == 2 and not hidden.visible and not hidden.complete


@pytest.fixture
def chat(session):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    repo.upsert_users(session, [(UserRecord(u, f"user{u}"), T0) for u in (ALICE, BOB, CAROL)])
    repo.upsert_messages(
        session,
        [
            MessageRecord(GID, 1, T0, sender_id=ALICE, text="hello"),
            MessageRecord(GID, 2, T0 + timedelta(minutes=1), sender_id=BOB, text="hi"),
        ],
    )
    session.commit()
    return session


def rr(user_id, emoji, minutes=5):
    return ReactionRecord(user_id, emoji, T0 + timedelta(minutes=minutes))


def edges(session):
    return sorted(
        session.execute(
            select(
                Interaction.message_id,
                Interaction.source_user_id,
                Interaction.target_user_id,
            ).where(Interaction.type == "reaction")
        ).all()
    )


def test_store_reactions_builds_edges(chat):
    written = repo.store_reactions(
        chat,
        GID,
        [
            # Bob reacts twice to Alice (one edge), Alice likes her own message (no edge).
            ReactionSummary(1, 3, [rr(BOB, "👍"), rr(BOB, "❤️"), rr(ALICE, "👍")]),
            ReactionSummary(2, 1, [rr(CAROL, "🤡")]),
            ReactionSummary(99, 1, [rr(CAROL, "👍")]),  # message not stored
        ],
    )
    chat.commit()
    assert written == 4
    assert edges(chat) == [(1, BOB, ALICE), (2, CAROL, BOB)]
    assert chat.get(Message, (GID, 1)).reaction_count == 3

    # A complete list replaces (Bob took his ❤️ back); a partial one only adds.
    repo.store_reactions(chat, GID, [ReactionSummary(1, 1, [rr(BOB, "👍")])])
    repo.store_reactions(chat, GID, [ReactionSummary(2, 5, [rr(ALICE, "👍")])])
    chat.commit()
    assert count(chat, Reaction, Reaction.message_id == 1) == 1
    assert count(chat, Reaction, Reaction.message_id == 2) == 2
    assert edges(chat) == [(1, BOB, ALICE), (2, ALICE, BOB), (2, CAROL, BOB)]

    # A full edge rebuild finds the same reaction edges.
    chat.execute(Interaction.__table__.delete())
    repo.build_interactions(chat, GID)
    assert edges(chat) == [(1, BOB, ALICE), (2, ALICE, BOB), (2, CAROL, BOB)]

    # Deleting a message removes its reactions and their edges.
    repo.delete_messages(chat, GID, [2])
    chat.commit()
    assert count(chat, Reaction) == 1 and edges(chat) == [(1, BOB, ALICE)]


def test_excluded_members_reactions(chat):
    repo.store_reactions(
        chat, GID, [ReactionSummary(1, 2, [rr(BOB, "👍"), rr(CAROL, "👍")])], excluded={CAROL}
    )
    assert count(chat, Reaction) == 1
    removed = repo.exclude_user(chat, BOB)
    chat.commit()
    assert removed["reactions"] == 1 and count(chat, Reaction) == 0 and edges(chat) == []


def test_reaction_edges_carry_emoji_tone(engine, chat):
    repo.store_reactions(
        chat,
        GID,
        [
            ReactionSummary(1, 2, [rr(BOB, "👍"), rr(BOB, "❤️")]),
            ReactionSummary(2, 1, [rr(CAROL, "🤡")]),
        ],
    )
    chat.commit()
    e = load.interactions(engine, GID, T0 - timedelta(days=1), T0 + timedelta(days=1))
    tone = e.set_index(["message_id", "source_user_id"])["sentiment_score"]
    assert tone[(1, BOB)] == pytest.approx((POSITIVE + WARM) / 2)
    assert tone[(2, CAROL)] == HOSTILE
    r = load.reactions(engine, GID, T0 - timedelta(days=1), T0 + timedelta(days=1))
    assert sorted(r["emoji"]) == sorted(["👍", "❤️", "🤡"])
    assert set(r.loc[r["emoji"] == "🤡", "author_id"]) == {BOB}


def test_hostility_threshold_ignores_reactions():
    ts = pd.Timestamp("2026-01-01")
    text = [(-0.9 + i * 0.1, "reply") for i in range(19)]  # -0.9 .. +0.9
    likes = [(POSITIVE, "reaction")] * 500
    e = pd.DataFrame(
        [
            {
                "source_user_id": 1,
                "target_user_id": 2,
                "type": kind,
                "timestamp": ts,
                "message_id": i,
                "sentiment_score": tone,
            }
            for i, (tone, kind) in enumerate(text + likes)
        ]
    )
    expected = conflict.hostility_threshold(e.loc[e["type"] == "reply", "sentiment_score"])
    assert conflict.hostility_threshold(conflict.written(e)["sentiment_score"]) == expected
    assert len(conflict.written(e)) == 19


class _FakeTelegram:
    """get_messages(ids=...) with reactions, and the full reaction list call."""

    def __init__(self, reactions: dict[int, types.MessageReactions | None]):
        self.reactions, self.list_calls = reactions, 0

    async def get_messages(self, entity, ids):
        return [
            SimpleNamespace(
                id=i,
                date=T0,
                sender_id=ALICE if i == 1 else BOB,
                message="hello" if i == 1 else "hi",
                reply_to=None,
                views=None,
                forwards=None,
                media=None,
                entities=None,
                action=None,
                fwd_from=None,
                reactions=self.reactions.get(i),
            )
            for i in ids
        ]

    async def __call__(self, request):
        assert isinstance(request, GetMessageReactionsListRequest)
        self.list_calls += 1
        page = [peer_reaction(BOB, "👍"), peer_reaction(CAROL, "🔥")]
        users = [types.User(id=CAROL, username="carol", first_name="Carol")]
        return MessageReactionsList(count=2, reactions=page, chats=[], users=users)


async def test_reconcile_imports_reactions(engine, chat, monkeypatch):
    async def fake_resolve(client, factory, group):
        return "entity", GID

    monkeypatch.setattr(service, "resolve_group", fake_resolve)
    summary_only = types.MessageReactions(
        results=[
            types.ReactionCount(types.ReactionEmoji("👍"), 1),
            types.ReactionCount(types.ReactionEmoji("🔥"), 1),
        ],
        can_see_list=True,
        recent_reactions=[peer_reaction(BOB, "👍")],  # incomplete: 1 of 2 reactors
    )
    tg = _FakeTelegram({1: summary_only})
    factory = make_session_factory(engine)

    stats = await service.reconcile(tg, factory, str(GID), days=30)
    assert stats["reactions_updated"] == 1 and tg.list_calls == 1
    chat.expire_all()
    assert edges(chat) == [(1, BOB, ALICE), (1, CAROL, ALICE)]
    assert chat.get(Message, (GID, 1)).reaction_count == 2

    # Unchanged totals: no work and no API calls on the next run.
    stats = await service.reconcile(tg, factory, str(GID), days=30)
    assert stats["reactions_updated"] == 0 and tg.list_calls == 1

    # All reactions taken back.
    tg.reactions = {}
    stats = await service.reconcile(tg, factory, str(GID), days=30)
    chat.expire_all()
    assert stats["reactions_updated"] == 1 and count(chat, Reaction) == 0 and edges(chat) == []


def test_synthetic_reactions_ground_truth():
    community = synthetic.generate(days=30)
    roles = community.roles
    by_id = {m.message_id: m for m in community.messages}
    received: dict[str, int] = {}
    feud_tones = []
    for s in community.reactions:
        author = by_id[s.message_id].sender_id
        received[roles[author]] = received.get(roles[author], 0) + s.total
        if roles[author] == "feuder":
            feud_tones += [emoji_tone(r.emoji) for r in s.reactors if roles[r.user_id] == "feuder"]
    assert received["helper"] > received.get("regular", 0)
    assert feud_tones and max(feud_tones) < 0
    # Reactions don't change the messages (separate random stream).
    plain = synthetic.generate(days=30)
    assert [m.text for m in plain.messages] == [m.text for m in community.messages]
