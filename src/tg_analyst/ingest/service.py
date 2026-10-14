"""Back-fill and live ingestion of Telegram groups."""

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker
from telethon import TelegramClient, events
from telethon.utils import get_peer_id

from tg_analyst.ingest.convert import group_to_record, message_to_record, sender_to_record
from tg_analyst.ingest.groupref import GroupRefError, candidate_ids, parse_group_ref
from tg_analyst.storage import repository as repo
from tg_analyst.storage.records import MessageRecord, UserRecord

log = logging.getLogger(__name__)

BATCH_SIZE = 500


def make_client(session_path: str, api_id: int, api_hash: str) -> TelegramClient:
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    # Telethon sleeps through FloodWaitError automatically below this threshold (seconds).
    return TelegramClient(session_path, api_id, api_hash, flood_sleep_threshold=24 * 3600)


def _persist(
    session: Session,
    group_id: int,
    batch: list[tuple[MessageRecord, UserRecord | None]],
    advance: bool = True,
    excluded: set[int] = frozenset(),
) -> int:
    """Store a batch, skipping excluded senders; returns how many messages were skipped."""
    highest = max(r.message_id for r, _ in batch)
    kept = [(r, u) for r, u in batch if r.sender_id not in excluded]
    repo.upsert_users(session, ((u, r.timestamp) for r, u in kept if u is not None))
    repo.upsert_messages(session, [r for r, _ in kept])
    if advance:
        # Advance past skipped messages too, so they aren't fetched again next time.
        repo.advance_high_water_mark(session, group_id, highest)
    session.commit()
    return len(batch) - len(kept)


async def _find_entity(client: TelegramClient, group: str):
    ref = parse_group_ref(group)
    ids = candidate_ids(ref)
    if not ids:
        return await client.get_entity(ref)
    # Telethon resolves numeric ids only for chats it has seen; load the dialog list once
    # if the first attempt fails.
    for attempt in range(2):
        for gid in ids:
            try:
                return await client.get_entity(gid)
            except (ValueError, TypeError):
                continue
        if attempt == 0:
            await client.get_dialogs()
    raise GroupRefError(
        f"Group {group!r} not found. Make sure the logged-in Telegram account is a member."
    )


async def resolve_group(client: TelegramClient, session_factory: sessionmaker, group: str):
    entity = await _find_entity(client, group)
    group_id = get_peer_id(entity)
    with session_factory() as session:
        repo.upsert_group(session, group_to_record(entity, group_id))
        session.commit()
    return entity, group_id


async def backfill(
    client: TelegramClient,
    session_factory: sessionmaker,
    group: str,
    since: datetime | None = None,
    limit: int | None = None,
) -> int:
    """Fetch messages oldest-first, resuming after the stored high-water mark."""
    entity, group_id = await resolve_group(client, session_factory, group)
    with session_factory() as session:
        start_id = repo.get_high_water_mark(session, group_id)
    log.info("Back-filling group %s from message id %s", group_id, start_id)

    total = 0
    batch: list[tuple[MessageRecord, UserRecord | None]] = []
    kwargs = {"reverse": True, "min_id": start_id, "limit": limit}
    if since is not None and start_id == 0:
        kwargs["offset_date"] = since

    skipped = 0
    with session_factory() as session:
        excluded = repo.excluded_ids(session)
        async for msg in client.iter_messages(entity, **kwargs):
            batch.append((message_to_record(msg, group_id), sender_to_record(msg.sender)))
            if len(batch) >= BATCH_SIZE:
                skipped += _persist(session, group_id, batch, excluded=excluded)
                total += len(batch)
                log.info("Stored %d messages", total - skipped)
                batch.clear()
        if batch:
            skipped += _persist(session, group_id, batch, excluded=excluded)
            total += len(batch)
        if skipped:
            log.info("Skipped %d messages from excluded members", skipped)
        edges = repo.build_interactions(session, group_id, min_message_id=start_id + 1)
        session.commit()

    log.info("Back-fill done: %d messages, %d new interactions", total, edges)
    return total


async def listen(client: TelegramClient, session_factory: sessionmaker, groups: list[str]) -> None:
    """Store new messages as they arrive until the client disconnects."""
    group_ids: dict[int, object] = {}
    for g in groups:
        entity, gid = await resolve_group(client, session_factory, g)
        group_ids[gid] = entity

    async def _store(msg, gid: int) -> None:
        record = message_to_record(msg, gid)
        sender = await msg.get_sender()
        with session_factory() as session:
            # The listener never advances the high-water mark: a later back-fill must still
            # fetch any history older than what arrived live.
            _persist(
                session,
                gid,
                [(record, sender_to_record(sender))],
                advance=False,
                excluded=repo.excluded_ids(session),
            )
            repo.build_interactions(session, gid, min_message_id=record.message_id)
            session.commit()
        log.debug("Stored message %s in %s", record.message_id, gid)

    chats = list(group_ids.values())

    @client.on(events.NewMessage(chats=chats))
    async def _on_message(event):
        await _store(event.message, event.chat_id)

    # Joins/leaves arrive as service messages, which NewMessage does not deliver.
    @client.on(events.ChatAction(chats=chats))
    async def _on_action(event):
        if event.action_message is not None:
            await _store(event.action_message, event.chat_id)

    log.info("Listening to %d group(s); Ctrl+C to stop", len(group_ids))
    await client.run_until_disconnected()
