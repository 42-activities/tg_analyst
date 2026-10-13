"""Command-line entry point: ``tg-analyst <command>``."""

import argparse
import asyncio
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
        async with client:
            await client.start(phone=get_settings().tg_phone)
            for group in args.group:
                await backfill(client, factory, group, since=since, limit=args.limit)

    asyncio.run(run())


def cmd_listen(args) -> None:
    from tg_analyst.ingest.service import listen

    factory = _session_factory()
    client = _client()

    async def run():
        async with client:
            await client.start(phone=get_settings().tg_phone)
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
