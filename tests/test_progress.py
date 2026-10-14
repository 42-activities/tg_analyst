"""Job progress: the reporter, ETA, and progress from back-fill, re-check and enrichment."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tg_analyst import jobs
from tg_analyst.enrich.pipeline import enrich
from tg_analyst.enrich.sentiment import VaderBackend
from tg_analyst.ingest import service
from tg_analyst.progress import ratio
from tg_analyst.storage import repository as repo
from tg_analyst.storage.db import make_session_factory
from tg_analyst.storage.models import Job
from tg_analyst.storage.records import GroupRecord, MessageRecord

GID = -100555
T0 = datetime(2026, 5, 1)


@pytest.fixture
def factory(engine):
    return make_session_factory(engine)


def test_ratio():
    assert ratio(5, 10) == 0.5 and ratio(12, 10) == 1.0 and ratio(-1, 10) == 0.0
    assert ratio(3, 0) is None and ratio(3, None) is None


def test_reporter_stores_progress_and_throttles(factory):
    with factory() as s:
        job_id = jobs.enqueue(s, "seed").id
    report = jobs.progress_reporter(factory, job_id, every=3600)
    report(0.25, "a quarter")
    report(0.5, "half")  # within the interval: dropped
    with factory() as s:
        assert s.get(Job, job_id).progress["detail"] == "a quarter"
    report(1.0, "all")  # completion is always stored
    with factory() as s:
        progress = s.get(Job, job_id).progress
    assert progress["fraction"] == 1.0 and progress["detail"] == "all" and progress["updated"]


def test_eta():
    start = datetime(2026, 1, 1)
    assert jobs.eta(start, 0.25, start + timedelta(minutes=10)) == timedelta(minutes=30)
    assert jobs.eta(start, None, start + timedelta(minutes=10)) is None
    assert jobs.eta(start, 0.5, start + timedelta(seconds=5)) is None  # too early to tell
    assert jobs.eta(start, 1.0, start + timedelta(minutes=10)) is None


def test_worker_records_progress_of_enrichment(factory):
    with factory() as s:
        jobs.enqueue(s, "seed", {"days": 5})
        jobs.enqueue(s, "enrich", {"backend": "vader"})
    jobs.run_one(factory)
    jobs.run_one(factory)
    with factory() as s:
        job = s.get(Job, 2)
    assert job.progress["fraction"] == 1.0
    assert "messages scored" in job.progress["detail"]


def test_enrich_progress(session):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    repo.upsert_messages(
        session, [MessageRecord(GID, i, T0, sender_id=1, text=f"hello {i}") for i in range(1, 8)]
    )
    session.commit()
    seen = []
    enrich(session, VaderBackend(), batch_size=3, progress=lambda f, d: seen.append((f, d)))
    assert [f for f, _ in seen] == [0.0, pytest.approx(3 / 7), pytest.approx(6 / 7), 1.0]
    assert seen[-1][1] == "7 of 7 messages scored"
    seen.clear()
    enrich(session, VaderBackend(), progress=lambda f, d: seen.append((f, d)))
    assert seen == [(1.0, "0 messages to score")]


def message(i: int):
    return SimpleNamespace(
        id=i,
        date=T0 + timedelta(minutes=i),
        sender_id=1,
        sender=None,
        message=f"message {i}",
        reply_to=None,
        views=None,
        forwards=None,
        media=None,
        entities=None,
        action=None,
        fwd_from=None,
        reactions=None,
    )


class FakeHistory:
    """A group whose message ids run 1..top with gaps (deleted messages)."""

    def __init__(self, ids: list[int]):
        self.ids = ids

    async def get_messages(self, entity, limit=None, ids=None):
        if ids is not None:
            return [message(i) if i in self.ids else None for i in ids]
        return [message(max(self.ids))]

    async def iter_messages(self, entity, reverse, min_id, limit):
        for i in sorted(self.ids):
            if i > min_id:
                yield message(i)


@pytest.fixture
def fake_group(monkeypatch):
    async def fake_resolve(client, factory, group):
        return "entity", GID

    monkeypatch.setattr(service, "resolve_group", fake_resolve)
    monkeypatch.setattr(service, "BATCH_SIZE", 100)


async def test_backfill_progress_follows_message_ids(engine, session, fake_group):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    session.commit()
    # Ids 1..1000, then a gap; the newest message is 2000.
    tg = FakeHistory(list(range(1, 1001)) + list(range(1500, 2001)))
    seen = []
    n = await service.backfill(
        tg, make_session_factory(engine), str(GID), progress=lambda f, d: seen.append((f, d))
    )
    assert n == 1501
    fractions = [f for f, _ in seen if f is not None]
    assert fractions == sorted(fractions) and fractions[0] == 0.0
    assert fractions[1] == pytest.approx(99 / 1999)  # first batch ends at id 100
    assert fractions[-1] > 0.9
    assert "messages stored, up to 2026-05-0" in seen[-2][1]
    assert seen[-1] == (None, "1,501 messages stored; linking replies and mentions")

    # Resuming: nothing new, no progress beyond the start.
    seen.clear()
    assert (
        await service.backfill(
            tg, make_session_factory(engine), str(GID), progress=lambda f, d: seen.append((f, d))
        )
        == 0
    )


async def test_reconcile_progress(engine, session, fake_group):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    now = datetime.now().replace(microsecond=0)
    repo.upsert_messages(
        session, [MessageRecord(GID, i, now, sender_id=1, text="x") for i in range(1, 251)]
    )
    session.commit()
    tg = FakeHistory([i for i in range(1, 251) if i != 7])
    seen = []
    stats = await service.reconcile(
        tg, make_session_factory(engine), str(GID), progress=lambda f, d: seen.append((f, d))
    )
    assert stats["deleted"] == 1
    assert [f for f, _ in seen] == [None, 0.4, 0.8, 1.0]
    assert seen[-1][1].startswith("250 of 250 messages checked: 1 deleted")
