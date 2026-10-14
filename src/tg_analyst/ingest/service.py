"""Back-fill and live ingestion of Telegram groups."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.orm import Session, sessionmaker
from telethon import TelegramClient, events
from telethon.errors import RPCError
from telethon.tl import types
from telethon.tl.functions.messages import GetMessageReactionsListRequest
from telethon.utils import get_peer_id

from tg_analyst.ingest.convert import (
    group_to_record,
    message_to_record,
    peer_reactions_to_records,
    reactions_to_summary,
    sender_to_record,
)
from tg_analyst.ingest.groupref import GroupRefError, candidate_ids, parse_group_ref
from tg_analyst.storage import repository as repo
from tg_analyst.storage.records import MessageRecord, ReactionSummary, UserRecord

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


REACTION_PAGE = 100


async def _complete_reactions(
    client: TelegramClient, entity, summaries: list[ReactionSummary]
) -> list[tuple[UserRecord, datetime]]:
    """Fetch the full reactor list where a message only carries its latest reactors.

    Fills ``summaries`` in place; returns the reactors' user records (with names) to store.
    One API call per page of 100 reactors; Telethon sleeps through flood waits.
    """
    users: list[tuple[UserRecord, datetime]] = []
    for s in summaries:
        if not s.visible or s.complete:
            continue
        reactors, offset = [], None
        try:
            while True:
                page = await client(
                    GetMessageReactionsListRequest(
                        peer=entity, id=s.message_id, limit=REACTION_PAGE, offset=offset
                    )
                )
                reactors.extend(peer_reactions_to_records(page.reactions))
                seen_at = {r.user_id: r.timestamp for r in reactors}
                users.extend(
                    (rec, seen_at[rec.user_id])
                    for rec in map(sender_to_record, page.users)
                    if rec is not None and seen_at.get(rec.user_id) is not None
                )
                offset = page.next_offset
                if not offset:
                    break
        except RPCError as exc:
            # Keep the partial list from the message itself.
            log.warning("Reaction list for message %s unavailable: %s", s.message_id, exc)
            continue
        s.reactors, s.listed = reactors, True
    return users


def _store_reactions(
    session: Session,
    group_id: int,
    summaries: list[ReactionSummary],
    users: list[tuple[UserRecord, datetime]],
    excluded: set[int],
) -> None:
    repo.upsert_users(session, ((u, ts) for u, ts in users if u.user_id not in excluded))
    repo.store_reactions(session, group_id, summaries, excluded)
    session.commit()


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
    summaries: list[ReactionSummary] = []

    async def flush() -> tuple[int, int]:
        users = await _complete_reactions(
            client, entity, [s for s in summaries if s.total and not s.complete]
        )
        dropped = _persist(session, group_id, batch, excluded=excluded)
        _store_reactions(session, group_id, [s for s in summaries if s.total], users, excluded)
        count = len(batch)
        batch.clear()
        summaries.clear()
        return dropped, count

    with session_factory() as session:
        excluded = repo.excluded_ids(session)
        async for msg in client.iter_messages(entity, **kwargs):
            batch.append((message_to_record(msg, group_id), sender_to_record(msg.sender)))
            summaries.append(reactions_to_summary(msg))
            if len(batch) >= BATCH_SIZE:
                dropped, count = await flush()
                skipped += dropped
                total += count
                log.info("Stored %d messages", total - skipped)
        if batch:
            dropped, count = await flush()
            skipped += dropped
            total += count
        if skipped:
            log.info("Skipped %d messages from excluded members", skipped)
        edges = repo.build_interactions(session, group_id, min_message_id=start_id + 1)
        session.commit()

    log.info("Back-fill done: %d messages, %d new interactions", total, edges)
    return total


RECONCILE_BATCH = 100  # Telegram returns at most 100 messages per get_messages(ids=...)


async def reconcile(
    client: TelegramClient,
    session_factory: sessionmaker,
    group: str,
    days: int | None = 30,
) -> dict[str, int]:
    """Re-check stored messages against Telegram: drop deleted ones, apply edits, and
    refresh reactions.

    ``days=None`` re-checks the whole history (this is also how reactions are imported
    for messages stored before reactions were collected). Telethon returns ``None`` (or an
    empty message) for ids that no longer exist, which is how deletions are detected.
    Reactions are refreshed when a message's reaction total changed since the last check.
    """
    entity, group_id = await resolve_group(client, session_factory, group)
    since = (
        datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days) if days is not None else None
    )
    with session_factory() as session:
        ids = repo.stored_message_ids(session, group_id, since)
        excluded = repo.excluded_ids(session)

    stats = {"checked": 0, "deleted": 0, "edited": 0, "reactions_updated": 0}
    for start in range(0, len(ids), RECONCILE_BATCH):
        chunk = ids[start : start + RECONCILE_BATCH]
        found = await client.get_messages(entity, ids=chunk)
        gone, edited, summaries = [], [], []
        for mid, msg in zip(chunk, found, strict=True):
            if msg is None or isinstance(msg, types.MessageEmpty):
                gone.append(mid)
                continue
            summaries.append(reactions_to_summary(msg))
            if msg.sender_id not in excluded and getattr(msg, "action", None) is None:
                edited.append(message_to_record(msg, group_id))
        with session_factory() as session:
            state = repo.reaction_state(session, group_id, [s.message_id for s in summaries])
        changed = [
            s
            for s in summaries
            # A message never checked before (count None) and without reactions is unchanged.
            if s.message_id in state and (state[s.message_id][0] or 0) != s.total
        ]
        users = await _complete_reactions(client, entity, changed)
        with session_factory() as session:
            stats["deleted"] += repo.delete_messages(session, group_id, gone)
            stats["edited"] += repo.apply_edits(session, group_id, edited)
            session.commit()
            _store_reactions(session, group_id, changed, users, excluded)
        stats["checked"] += len(chunk)
        stats["reactions_updated"] += len(changed)
    log.info("Reconciled group %s: %s", group_id, stats)
    return stats


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

    @client.on(events.MessageEdited(chats=chats))
    async def _on_edit(event):
        gid = event.chat_id
        with session_factory() as session:
            if event.message.sender_id in repo.excluded_ids(session):
                return
            repo.apply_edits(session, gid, [message_to_record(event.message, gid)])
            session.commit()

    @client.on(events.MessageDeleted(chats=chats))
    async def _on_delete(event):
        # Basic groups don't say which chat a deletion belongs to (chat_id is None); the
        # periodic reconcile job catches those.
        if event.chat_id is None:
            return
        with session_factory() as session:
            repo.delete_messages(session, event.chat_id, event.deleted_ids)
            session.commit()

    @client.on(events.Raw(types.UpdateMessageReactions))
    async def _on_reactions(update):
        gid = get_peer_id(update.peer)
        if gid not in group_ids:
            return
        summary = reactions_to_summary(
            SimpleNamespace(id=update.msg_id, reactions=update.reactions)
        )
        users = await _complete_reactions(client, group_ids[gid], [summary])
        with session_factory() as session:
            _store_reactions(session, gid, [summary], users, repo.excluded_ids(session))

    log.info("Listening to %d group(s); Ctrl+C to stop", len(group_ids))
    await client.run_until_disconnected()
