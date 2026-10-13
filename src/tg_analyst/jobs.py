"""A small database-backed job queue.

The web UI only enqueues rows; a separate ``tg-analyst worker`` process claims and runs
them. Keeping Telegram I/O out of the web process means one long-lived client session,
and jobs survive web restarts.
"""

import asyncio
import logging
import time
import traceback
from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tg_analyst.storage.models import Job

log = logging.getLogger(__name__)

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
KINDS = ("backfill", "enrich", "build_edges", "seed")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def enqueue(session: Session, kind: str, params: dict | None = None) -> Job:
    if kind not in KINDS:
        raise ValueError(f"Unknown job kind {kind!r}")
    job = Job(kind=kind, params=params or {}, status=QUEUED, created_at=_now())
    session.add(job)
    session.commit()
    return job


def claim_next(session: Session) -> Job | None:
    """Atomically move the oldest queued job to RUNNING and return it.

    The conditional UPDATE makes this safe even if several workers poll the same queue.
    """
    while True:
        job_id = session.scalar(
            select(Job.id).where(Job.status == QUEUED).order_by(Job.id).limit(1)
        )
        if job_id is None:
            return None
        claimed = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == QUEUED)
            .values(status=RUNNING, started_at=_now())
        ).rowcount
        session.commit()
        if claimed:
            return session.get(Job, job_id, populate_existing=True)


def recover_stale(session: Session) -> int:
    """Fail jobs left RUNNING by a worker that died; call once on worker start."""
    n = session.execute(
        update(Job)
        .where(Job.status == RUNNING)
        .values(status=FAILED, finished_at=_now(), error="Worker stopped while job was running")
    ).rowcount
    session.commit()
    return n


def recent(session: Session, limit: int = 50) -> list[Job]:
    return list(session.scalars(select(Job).order_by(Job.id.desc()).limit(limit)))


def _finish(session: Session, job: Job, status: str, result=None, error=None) -> None:
    job.status, job.finished_at, job.result, job.error = status, _now(), result, error
    session.commit()


# --- execution ---------------------------------------------------------------------------


def _run_backfill(factory: sessionmaker, params: dict) -> str:
    from tg_analyst.config import get_settings
    from tg_analyst.ingest.service import backfill, make_client

    settings = get_settings()
    api_id, api_hash = settings.require_telegram()
    since = date.fromisoformat(params["since"]) if params.get("since") else None
    since_dt = datetime(since.year, since.month, since.day, tzinfo=UTC) if since else None

    async def run() -> int:
        client = make_client(settings.tg_session_path, api_id, api_hash)
        # Not `async with client`: that calls client.start(), which prompts for a phone
        # number on stdin and dies with EOFError in a background worker.
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Telegram account is not logged in. Run the one-time login on the server: "
                    "docker compose run --rm app login"
                )
            return await backfill(
                client, factory, params["group"], since=since_dt, limit=params.get("limit")
            )
        finally:
            await client.disconnect()

    n = asyncio.run(run())
    return f"Fetched {n} messages from {params['group']}"


def _run_enrich(factory: sessionmaker, params: dict) -> str:
    from tg_analyst.config import get_settings
    from tg_analyst.enrich.pipeline import enrich
    from tg_analyst.enrich.sentiment import get_backend

    settings = get_settings()
    name = params.get("backend") or settings.sentiment_backend
    kwargs = {"model": settings.hf_sentiment_model} if name == "hf" else {}
    with factory() as session:
        stats = enrich(session, get_backend(name, **kwargs), group_id=params.get("group_id"))
    return f"Scored {stats.scored} messages ({stats.skipped} without text) with {name}"


def _run_build_edges(factory: sessionmaker, params: dict) -> str:
    from tg_analyst.storage import repository as repo
    from tg_analyst.storage.models import Group

    with factory() as session:
        gids = (
            [params["group_id"]]
            if params.get("group_id")
            else list(session.scalars(select(Group.group_id)))
        )
        n = sum(repo.build_interactions(session, gid) for gid in gids)
        session.commit()
    return f"Added {n} interactions"


def _run_seed(factory: sessionmaker, params: dict) -> str:
    from tg_analyst import synthetic

    community = synthetic.generate(days=params.get("days", 90))
    with factory() as session:
        synthetic.load(session, community)
    return f"Loaded {len(community.messages)} synthetic messages"


RUNNERS = {
    "backfill": _run_backfill,
    "enrich": _run_enrich,
    "build_edges": _run_build_edges,
    "seed": _run_seed,
}


def run_one(factory: sessionmaker) -> bool:
    """Claim and execute a single job; returns False when the queue is empty."""
    with factory() as session:
        job = claim_next(session)
        if job is None:
            return False
        log.info("Running job %s (%s) %s", job.id, job.kind, job.params)
        try:
            result = RUNNERS[job.kind](factory, job.params or {})
        except Exception as exc:
            log.exception("Job %s failed", job.id)
            _finish(session, job, FAILED, error=f"{exc}\n\n{traceback.format_exc(limit=5)}")
        else:
            log.info("Job %s done: %s", job.id, result)
            _finish(session, job, DONE, result=result)
            # Chained enrichment after a back-fill, as requested from the UI.
            if job.kind == "backfill" and job.params.get("enrich_after"):
                enqueue(session, "enrich", {})
        return True


def work_forever(factory: sessionmaker, poll_seconds: float = 2.0) -> None:
    with factory() as session:
        if n := recover_stale(session):
            log.warning("Marked %d interrupted job(s) as failed", n)
    log.info("Worker started; polling every %.1fs", poll_seconds)
    while True:
        if not run_one(factory):
            time.sleep(poll_seconds)
