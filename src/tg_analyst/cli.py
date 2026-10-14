"""Command-line entry point: ``tg-analyst <command>``."""

import argparse
import asyncio
import contextlib
import logging
from datetime import datetime

from tg_analyst.config import get_settings
from tg_analyst.storage.db import init_db, make_engine, make_session_factory

log = logging.getLogger("tg_analyst")


def _session_factory():
    engine = make_engine(get_settings().database_url)
    init_db(engine)
    return make_session_factory(engine)


def _client():
    from tg_analyst.ingest.service import make_client

    settings = get_settings()
    api_id, api_hash = settings.require_telegram()
    return make_client(settings.tg_session_path, api_id, api_hash)


@contextlib.asynccontextmanager
async def _started(client):
    """Log in (prompting only if needed) and always disconnect.

    Not `async with client`: that runs client.start() with no phone and prompts for it.
    """
    await client.start(phone=get_settings().tg_phone)
    try:
        yield client
    finally:
        await client.disconnect()


def cmd_init_db(_args) -> None:
    _session_factory()
    log.info("Database ready")


def cmd_seed(args) -> None:
    from tg_analyst import synthetic

    community = synthetic.generate(days=args.days, seed=args.seed)
    with _session_factory()() as session:
        synthetic.load(session, community)
    log.info(
        "Seeded %d users and %d messages into group %s",
        len(community.users),
        len(community.messages),
        community.group.group_id,
    )


def cmd_backfill(args) -> None:
    from tg_analyst.ingest.service import backfill

    since = datetime.fromisoformat(args.since) if args.since else None
    factory = _session_factory()
    client = _client()

    async def run():
        async with _started(client):
            for group in args.group:
                await backfill(client, factory, group, since=since, limit=args.limit)

    asyncio.run(run())


def cmd_listen(args) -> None:
    from tg_analyst.ingest.service import listen

    factory = _session_factory()
    client = _client()

    async def run():
        async with _started(client):
            await listen(client, factory, args.group)

    asyncio.run(run())


def cmd_build_edges(args) -> None:
    from sqlalchemy import select

    from tg_analyst.storage import repository as repo
    from tg_analyst.storage.models import Group

    with _session_factory()() as session:
        group_ids = args.group_id or session.scalars(select(Group.group_id)).all()
        for gid in group_ids:
            n = repo.build_interactions(session, gid)
            log.info("Group %s: %d new interactions", gid, n)
        session.commit()


def cmd_enrich(args) -> None:
    from tg_analyst.enrich.pipeline import enrich
    from tg_analyst.enrich.toxicity import backends

    sentiment, toxicity = backends(get_settings(), args.backend)
    with _session_factory()() as session:
        stats = enrich(
            session,
            sentiment,
            group_id=args.group_id,
            batch_size=args.batch_size,
            force=args.force,
            toxicity=toxicity,
        )
    log.info("Enrichment done: %d scored, %d without text", stats.scored, stats.skipped)


def cmd_login(_args) -> None:
    client = _client()

    async def run():
        async with _started(client):
            me = await client.get_me()
            log.info("Logged in as %s (id %s); session saved", me.username or me.first_name, me.id)

    asyncio.run(run())


def cmd_worker(args) -> None:
    from tg_analyst.jobs import work_forever

    work_forever(_session_factory(), poll_seconds=args.poll)


def cmd_web(args) -> None:
    import subprocess
    import sys
    from pathlib import Path

    import tg_analyst.dashboard

    app = Path(tg_analyst.dashboard.__file__).with_name("app.py")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--server.address",
        args.host,
        "--server.port",
        str(args.port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--client.toolbarMode",
        "minimal",
    ]
    raise SystemExit(subprocess.call(cmd))


def cmd_exclude(args) -> None:
    from tg_analyst.storage import repository as repo

    with _session_factory()() as session:
        uid = repo.resolve_user(session, args.user)
        removed = repo.exclude_user(session, uid, args.note)
        session.commit()
    log.info(
        "Excluded %s: removed %d messages, %d replies/mentions/reactions and %d reactions",
        uid,
        removed["messages"],
        removed["interactions"],
        removed["reactions"],
    )


def cmd_include(args) -> None:
    from tg_analyst.storage import repository as repo

    with _session_factory()() as session:
        uid = repo.resolve_user(session, args.user)
        found = repo.include_user(session, uid)
        session.commit()
    log.info("%s %s", uid, "is no longer excluded" if found else "was not excluded")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tg-analyst", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create database tables").set_defaults(func=cmd_init_db)

    p = sub.add_parser("seed", help="load a synthetic community for development")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("backfill", help="fetch group history (resumable)")
    p.add_argument("--group", action="append", required=True, help="username or id; repeatable")
    p.add_argument("--since", help="ISO date; only used on the first back-fill of a group")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("listen", help="stream new messages")
    p.add_argument("--group", action="append", required=True, help="username or id; repeatable")
    p.set_defaults(func=cmd_listen)

    p = sub.add_parser("build-edges", help="(re)derive reply and mention interactions")
    p.add_argument("--group-id", type=int, action="append")
    p.set_defaults(func=cmd_build_edges)

    sub.add_parser("login", help="log in to Telegram once (interactive)").set_defaults(
        func=cmd_login
    )

    p = sub.add_parser("worker", help="run queued jobs from the web UI")
    p.add_argument("--poll", type=float, default=2.0, help="seconds between queue checks")
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("web", help="start the web UI (Streamlit)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8501)
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("exclude", help="opt a member out: delete and skip their messages")
    p.add_argument("--user", required=True, help="@username or numeric Telegram id")
    p.add_argument("--note")
    p.set_defaults(func=cmd_exclude)

    p = sub.add_parser("include", help="lift a member's exclusion")
    p.add_argument("--user", required=True, help="@username or numeric Telegram id")
    p.set_defaults(func=cmd_include)

    p = sub.add_parser("enrich", help="score sentiment and intent for new/stale messages")
    p.add_argument("--backend", choices=["vader", "hf"], help="default: SENTIMENT_BACKEND")
    p.add_argument("--group-id", type=int)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--force", action="store_true", help="re-score everything")
    p.set_defaults(func=cmd_enrich)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args.func(args)


if __name__ == "__main__":
    main()
