"""A small database-backed job queue.

The web UI only enqueues rows; a separate ``tg-analyst worker`` process claims and runs
them. Keeping Telegram I/O out of the web process means one long-lived client session,
and jobs survive web restarts.
"""

import asyncio
import logging
import time
import traceback
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tg_analyst.progress import Progress
from tg_analyst.storage.models import Job

log = logging.getLogger(__name__)

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
KINDS = ("backfill", "enrich", "build_edges", "seed", "exclude", "include", "sync")


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


def progress_reporter(factory: sessionmaker, job_id: int, every: float = 2.0) -> Progress:
    """A callback that stores a job's progress, at most once per ``every`` seconds.

    It writes in its own short transaction so the Jobs page sees it immediately, and never
    raises: a progress update must not fail the job.
    """
    last = [float("-inf")]

    def report(fraction: float | None, detail: str) -> None:
        now = time.monotonic()
        if now - last[0] < every and fraction != 1.0:
            return
        last[0] = now
        value = {
            "fraction": None if fraction is None else round(fraction, 4),
            "detail": detail,
            "updated": _now().isoformat(timespec="seconds"),
        }
        try:
            with factory() as session:
                session.execute(update(Job).where(Job.id == job_id).values(progress=value))
                session.commit()
        except Exception:
            log.warning("Could not store progress of job %s", job_id, exc_info=True)

    return report


def eta(started_at: datetime | None, fraction: float | None, now: datetime) -> timedelta | None:
    """Time left at the current average speed, once there is enough to extrapolate from."""
    if started_at is None or not fraction or fraction >= 1.0:
        return None
    elapsed = now - started_at
    if fraction < 0.01 or elapsed < timedelta(seconds=10):
        return None
    return elapsed * (1 - fraction) / fraction


# --- execution ---------------------------------------------------------------------------


def _with_telegram(work) -> object:
    """Run ``await work(client)`` with a connected, logged-in Telegram client."""
    from tg_analyst.config import get_settings
    from tg_analyst.ingest.service import make_client

    settings = get_settings()
    api_id, api_hash = settings.require_telegram()

    async def run():
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
            return await work(client)
        finally:
            await client.disconnect()

    return asyncio.run(run())


def _run_backfill(factory: sessionmaker, params: dict, report: Progress) -> str:
    from tg_analyst.ingest.service import backfill

    since = date.fromisoformat(params["since"]) if params.get("since") else None
    since_dt = datetime(since.year, since.month, since.day, tzinfo=UTC) if since else None
    n = _with_telegram(
        lambda client: backfill(
            client,
            factory,
            params["group"],
            since=since_dt,
            limit=params.get("limit"),
            progress=report,
        )
    )
    return f"Fetched {n} messages from {params['group']}"


def _run_sync(factory: sessionmaker, params: dict, report: Progress) -> str:
    from tg_analyst.ingest.service import reconcile

    days = params.get("days", 30)
    stats = _with_telegram(
        lambda client: reconcile(client, factory, params["group"], days=days, progress=report)
    )
    if stats["edited"]:
        with factory() as session:
            enqueue(session, "enrich", {})  # re-score the edited messages
    scope = "whole history" if days is None else f"last {days} days"
    return (
        f"Checked {stats['checked']} messages ({scope}): {stats['deleted']} deleted, "
        f"{stats['edited']} edited, reactions updated on {stats['reactions_updated']}"
    )


def _run_enrich(factory: sessionmaker, params: dict, report: Progress) -> str:
    from tg_analyst.config import get_settings
    from tg_analyst.enrich.pipeline import enrich
    from tg_analyst.enrich.toxicity import backends

    sentiment, toxicity = backends(get_settings(), params.get("backend"))
    with factory() as session:
        stats = enrich(
            session,
            sentiment,
            group_id=params.get("group_id"),
            toxicity=toxicity,
            progress=report,
        )
    models = sentiment.version + (f" + {toxicity.version}" if toxicity else "")
    return f"Scored {stats.scored} messages ({stats.skipped} without text) with {models}"


def _run_build_edges(factory: sessionmaker, params: dict, report: Progress) -> str:
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


def _run_seed(factory: sessionmaker, params: dict, report: Progress) -> str:
    from tg_analyst import synthetic

    community = synthetic.generate(days=params.get("days", 90))
    with factory() as session:
        synthetic.load(session, community)
    return f"Loaded {len(community.messages)} synthetic messages"


def _run_exclude(factory: sessionmaker, params: dict, report: Progress) -> str:
    from tg_analyst.storage import repository as repo

    with factory() as session:
        uid = repo.resolve_user(session, str(params["user"]))
        removed = repo.exclude_user(session, uid, params.get("note"))
        session.commit()
    return (
        f"Excluded {uid}: removed {removed['messages']} messages and "
        f"{removed['interactions']} replies/mentions/reactions and "
        f"{removed['reactions']} of their reactions"
    )


def _run_include(factory: sessionmaker, params: dict, report: Progress) -> str:
    from tg_analyst.storage import repository as repo

    with factory() as session:
        uid = repo.resolve_user(session, str(params["user"]))
        found = repo.include_user(session, uid)
        session.commit()
    if not found:
        return f"{uid} was not excluded"
    return f"{uid} is no longer excluded (old messages return only with a full re-scrape)"


RUNNERS = {
    "backfill": _run_backfill,
    "enrich": _run_enrich,
    "build_edges": _run_build_edges,
    "seed": _run_seed,
    "exclude": _run_exclude,
    "sync": _run_sync,
    "include": _run_include,
}


def run_one(factory: sessionmaker) -> bool:
    """Claim and execute a single job; returns False when the queue is empty."""
    with factory() as session:
        job = claim_next(session)
        if job is None:
            return False
        log.info("Running job %s (%s) %s", job.id, job.kind, job.params)
        try:
            report = progress_reporter(factory, job.id)
            result = RUNNERS[job.kind](factory, job.params or {}, report)
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


def schedule_due(
    session: Session,
    now: datetime,
    refresh_minutes: int,
    reconcile_hours: int,
    reconcile_days: int,
) -> list[Job]:
    """Queue periodic work for every group; returns the jobs created.

    - incremental back-fill + enrichment every ``refresh_minutes``
    - re-check of the last ``reconcile_days`` against Telegram every ``reconcile_hours``

    A job is due when no job of that kind for the group was created within the interval,
    which also means nothing is queued twice. 0 disables a schedule. State lives in the
    jobs table, so restarts and deploys don't reset or duplicate the schedule.
    """
    from tg_analyst.storage.models import Group

    plans = []
    if refresh_minutes > 0:
        plans.append(
            (
                "backfill",
                timedelta(minutes=refresh_minutes),
                lambda g: {"group": str(g), "enrich_after": True, "scheduled": True},
            )
        )
    if reconcile_hours > 0:
        plans.append(
            (
                "sync",
                timedelta(hours=reconcile_hours),
                lambda g: {"group": str(g), "days": reconcile_days, "scheduled": True},
            )
        )
    created = []
    for group_id in session.scalars(select(Group.group_id)):
        for kind, interval, params in plans:
            recent = session.scalar(
                select(Job.id)
                .where(
                    Job.kind == kind,
                    Job.params["group"].as_string() == str(group_id),
                    Job.created_at >= now - interval,
                )
                .limit(1)
            )
            if recent is None:
                job = Job(kind=kind, params=params(group_id), status=QUEUED, created_at=now)
                session.add(job)
                created.append(job)
    session.commit()
    return created


def work_forever(factory: sessionmaker, poll_seconds: float = 2.0) -> None:
    from tg_analyst.config import get_settings

    settings = get_settings()
    with factory() as session:
        if n := recover_stale(session):
            log.warning("Marked %d interrupted job(s) as failed", n)
    log.info(
        "Worker started; polling every %.1fs; refresh every %d min, reconcile every %d h",
        poll_seconds,
        settings.auto_refresh_minutes,
        settings.auto_reconcile_hours,
    )
    next_check = 0.0
    while True:
        if time.monotonic() >= next_check:
            next_check = time.monotonic() + 60
            with factory() as session:
                for job in schedule_due(
                    session,
                    _now(),
                    settings.auto_refresh_minutes,
                    settings.auto_reconcile_hours,
                    settings.reconcile_days,
                ):
                    log.info("Scheduled job %s (%s) %s", job.id, job.kind, job.params)
        if not run_one(factory):
            time.sleep(poll_seconds)
