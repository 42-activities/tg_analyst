import pytest
from sqlalchemy import func, select

from tg_analyst import jobs
from tg_analyst.storage.db import make_session_factory
from tg_analyst.storage.models import Enrichment, Job, Message


@pytest.fixture
def factory(engine):
    return make_session_factory(engine)


def test_claim_is_fifo_and_exclusive(session):
    a = jobs.enqueue(session, "seed")
    b = jobs.enqueue(session, "enrich")
    first = jobs.claim_next(session)
    second = jobs.claim_next(session)
    assert (first.id, second.id) == (a.id, b.id)
    assert first.status == jobs.RUNNING and first.started_at is not None
    assert jobs.claim_next(session) is None


def test_unknown_kind_rejected(session):
    with pytest.raises(ValueError):
        jobs.enqueue(session, "rm -rf")


def test_recover_stale_fails_running_jobs(session):
    jobs.enqueue(session, "seed")
    jobs.claim_next(session)
    assert jobs.recover_stale(session) == 1
    job = session.scalar(select(Job))
    assert job.status == jobs.FAILED and "Worker stopped" in job.error


def test_worker_runs_seed_then_enrich(factory):
    with factory() as s:
        jobs.enqueue(s, "seed", {"days": 5})
        jobs.enqueue(s, "enrich", {"backend": "vader"})
    assert jobs.run_one(factory) and jobs.run_one(factory)
    assert not jobs.run_one(factory)

    with factory() as s:
        statuses = [j.status for j in s.scalars(select(Job).order_by(Job.id))]
        assert statuses == [jobs.DONE, jobs.DONE]
        assert s.scalar(select(func.count()).select_from(Message)) > 0
        assert s.scalar(select(func.count()).select_from(Enrichment)) > 0


def test_failed_job_records_error_and_backfill_requires_credentials(factory, monkeypatch, tmp_path):
    from tg_analyst.config import get_settings

    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    monkeypatch.chdir(tmp_path)  # avoid reading a developer's local .env
    get_settings.cache_clear()
    try:
        with factory() as s:
            jobs.enqueue(s, "backfill", {"group": "somegroup", "enrich_after": True})
        jobs.run_one(factory)
        with factory() as s:
            job = s.scalar(select(Job))
            assert job.status == jobs.FAILED
            assert "TG_API_ID" in job.error
            # A failed back-fill does not chain enrichment.
            assert s.scalar(select(func.count()).select_from(Job)) == 1
    finally:
        get_settings.cache_clear()


def test_recent_can_hide_runs_that_found_nothing_new(session):
    results = {
        "backfill": ["Fetched 0 messages from g", "Fetched 10 messages from g"],
        "enrich": ["Scored 0 messages (0 without text) with vader", "Scored 30 messages (2 …"],
        "sync": [
            "Checked 50 messages (last 30 days): 0 deleted, 0 edited, reactions updated on 0",
            "Checked 50 messages (last 30 days): 0 deleted, 0 edited, reactions updated on 05",
        ],
    }
    expected_shown = []
    for kind, (quiet, busy) in results.items():
        for result in (quiet, busy):
            job = jobs.enqueue(session, kind)
            jobs.claim_next(session)
            jobs._finish(session, job, jobs.DONE, result=result)
            if result == busy:
                expected_shown.append(job.id)
    failed = jobs.enqueue(session, "backfill")
    jobs.claim_next(session)
    jobs._finish(session, failed, jobs.FAILED, error="boom")
    queued = jobs.enqueue(session, "enrich")

    assert len(jobs.recent(session)) == 8
    shown = [j.id for j in jobs.recent(session, hide_nothing_new=True)]
    assert shown == sorted([*expected_shown, failed.id, queued.id], reverse=True)
    assert jobs.count_nothing_new(session) == 3
    assert [j.id for j in jobs.recent(session, limit=2, hide_nothing_new=True)] == shown[:2]
