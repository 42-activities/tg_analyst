"""Idempotent write operations and edge building.

Upserts use PostgreSQL's native INSERT ... ON CONFLICT.
"""

from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import and_, case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from tg_analyst.storage.models import (
    Enrichment,
    ExcludedUser,
    Group,
    Interaction,
    Message,
    Reaction,
    User,
)
from tg_analyst.storage.records import (
    GroupRecord,
    MessageRecord,
    ReactionSummary,
    UserRecord,
)

# Keeps multi-row statements well under PostgreSQL's 65,535 bound-parameter limit.
_CHUNK_ROWS = 500


def _chunks(rows: list[dict]) -> Iterable[list[dict]]:
    for start in range(0, len(rows), _CHUNK_ROWS):
        yield rows[start : start + _CHUNK_ROWS]


def _insert(session: Session, model):
    return pg_insert(model)


def upsert_group(session: Session, record: GroupRecord) -> None:
    values = asdict(record)
    stmt = _insert(session, Group).values(**values, last_message_id=0)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Group.group_id],
        set_={k: stmt.excluded[k] for k in ("title", "username", "member_count")},
    )
    session.execute(stmt)


def upsert_users(session: Session, sightings: Iterable[tuple[UserRecord, datetime]]) -> None:
    """Insert or refresh users seen at the given times, widening first_seen/last_seen."""
    merged: dict[int, dict] = {}
    for record, seen_at in sightings:
        row = merged.get(record.user_id)
        if row is None:
            merged[record.user_id] = {**asdict(record), "first_seen": seen_at, "last_seen": seen_at}
        else:
            row.update({k: v for k, v in asdict(record).items() if v is not None})
            row["first_seen"] = min(row["first_seen"], seen_at)
            row["last_seen"] = max(row["last_seen"], seen_at)
    rows = list(merged.values())
    for chunk in _chunks(rows):
        _upsert_user_rows(session, chunk)


def _upsert_user_rows(session: Session, rows: list[dict]) -> None:
    stmt = _insert(session, User).values(rows)
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[User.user_id],
        set_={
            "username": func.coalesce(ex.username, User.username),
            "display_name": func.coalesce(ex.display_name, User.display_name),
            "is_bot": ex.is_bot,
            "is_channel": ex.is_channel,
            "first_seen": case(
                (User.first_seen.is_(None), ex.first_seen),
                (ex.first_seen < User.first_seen, ex.first_seen),
                else_=User.first_seen,
            ),
            "last_seen": case(
                (User.last_seen.is_(None), ex.last_seen),
                (ex.last_seen > User.last_seen, ex.last_seen),
                else_=User.last_seen,
            ),
        },
    )
    session.execute(stmt)


_MUTABLE_MESSAGE_FIELDS = (
    "text",
    "views",
    "forwards",
    "has_media",
    "media_type",
    "mention_user_ids",
    "mention_usernames",
)


def upsert_messages(session: Session, records: Sequence[MessageRecord]) -> None:
    """Insert messages; on re-ingest refresh fields that can change (edits, view counts)."""
    for chunk in _chunks([asdict(r) for r in records]):
        stmt = _insert(session, Message).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Message.group_id, Message.message_id],
            set_={k: stmt.excluded[k] for k in _MUTABLE_MESSAGE_FIELDS},
        )
        session.execute(stmt)


def get_high_water_mark(session: Session, group_id: int) -> int:
    value = session.scalar(select(Group.last_message_id).where(Group.group_id == group_id))
    return value or 0


def advance_high_water_mark(session: Session, group_id: int, message_id: int) -> None:
    session.execute(
        update(Group)
        .where(Group.group_id == group_id, Group.last_message_id < message_id)
        .values(last_message_id=message_id)
    )


def build_interactions(session: Session, group_id: int, min_message_id: int = 0) -> int:
    """Derive reply and mention edges from stored messages; returns edges inserted.

    Safe to re-run: existing edges are skipped via the unique constraint. Self-interactions
    and messages without a known sender are ignored.
    """
    inserted = 0
    excluded = excluded_ids(session)

    # Replies: the edge goes from the replier to the author of the replied-to message.
    parent = aliased(Message)
    reply_rows = session.execute(
        select(Message.message_id, Message.sender_id, parent.sender_id, Message.timestamp)
        .join(
            parent,
            and_(parent.group_id == Message.group_id, parent.message_id == Message.reply_to_msg_id),
        )
        .where(
            Message.group_id == group_id,
            Message.message_id >= min_message_id,
            Message.sender_id.is_not(None),
            parent.sender_id.is_not(None),
            Message.sender_id != parent.sender_id,
        )
    ).all()
    edges = [
        {
            "group_id": group_id,
            "message_id": mid,
            "source_user_id": src,
            "target_user_id": dst,
            "type": "reply",
            "timestamp": ts,
        }
        for mid, src, dst, ts in reply_rows
    ]

    # Mentions: explicit user ids plus @usernames resolved against known users.
    mention_rows = session.execute(
        select(
            Message.message_id,
            Message.sender_id,
            Message.timestamp,
            Message.mention_user_ids,
            Message.mention_usernames,
        ).where(
            Message.group_id == group_id,
            Message.message_id >= min_message_id,
            Message.sender_id.is_not(None),
        )
    ).all()
    wanted = {u.lower() for *_, names in mention_rows for u in (names or [])}
    by_username: dict[str, int] = {}
    if wanted:
        for uid, uname in session.execute(
            select(User.user_id, User.username).where(func.lower(User.username).in_(wanted))
        ):
            by_username[uname.lower()] = uid
    for mid, src, ts, ids, names in mention_rows:
        targets = set(ids or []) | {
            by_username[n.lower()] for n in names or [] if n.lower() in by_username
        }
        targets.discard(src)
        targets -= excluded
        edges.extend(
            {
                "group_id": group_id,
                "message_id": mid,
                "source_user_id": src,
                "target_user_id": t,
                "type": "mention",
                "timestamp": ts,
            }
            for t in sorted(targets)
        )

    edges.extend(_reaction_edges(session, group_id, Message.message_id >= min_message_id))
    return inserted + _insert_edges(session, edges)


def _insert_edges(session: Session, edges: list[dict]) -> int:
    inserted = 0
    for chunk in _chunks(edges):
        stmt = (
            _insert(session, Interaction)
            .values(chunk)
            .on_conflict_do_nothing()
            # rowcount is unreliable for multi-row upserts on psycopg; count what came back.
            .returning(Interaction.id)
        )
        inserted += len(session.execute(stmt).all())
    return inserted


def _reaction_edges(session: Session, group_id: int, *where) -> list[dict]:
    """Reactor -> message author edges, one per (message, reactor) whatever the emoji."""
    rows = session.execute(
        select(
            Reaction.message_id,
            Reaction.user_id,
            Message.sender_id,
            func.min(func.coalesce(Reaction.timestamp, Message.timestamp)),
        )
        .join(
            Message,
            and_(Message.group_id == Reaction.group_id, Message.message_id == Reaction.message_id),
        )
        .where(
            Reaction.group_id == group_id,
            Message.sender_id.is_not(None),
            Message.sender_id != Reaction.user_id,
            *where,
        )
        .group_by(Reaction.message_id, Reaction.user_id, Message.sender_id)
    ).all()
    return [
        {
            "group_id": group_id,
            "message_id": mid,
            "source_user_id": reactor,
            "target_user_id": author,
            "type": "reaction",
            "timestamp": ts,
        }
        for mid, reactor, author, ts in rows
    ]


def refresh_statistics(session: Session) -> None:
    """ANALYZE the tables a back-fill grows, so the planner sees the new rows."""
    for table in ("messages", "reactions", "interactions"):
        session.execute(text(f"ANALYZE {table}"))
    session.commit()


def store_reactions(
    session: Session,
    group_id: int,
    summaries: Sequence[ReactionSummary],
    excluded: set[int] = frozenset(),
) -> int:
    """Store the reactions of stored messages and rebuild their reaction edges.

    A complete reactor list replaces what is stored (people take reactions back); a
    partial one only adds. Reactions by excluded members are dropped. Messages that are
    not stored (e.g. from excluded senders) are ignored. Returns reaction rows written.
    """
    stored = set(
        session.scalars(
            select(Message.message_id).where(
                Message.group_id == group_id,
                Message.message_id.in_([s.message_id for s in summaries]),
            )
        )
    )
    summaries = [s for s in summaries if s.message_id in stored]
    if not summaries:
        return 0
    ids = [s.message_id for s in summaries]
    for s in summaries:
        session.execute(
            update(Message)
            .where(Message.group_id == group_id, Message.message_id == s.message_id)
            .values(reaction_count=s.total)
        )
    replaced = [s.message_id for s in summaries if s.complete]
    if replaced:
        session.execute(
            delete(Reaction).where(Reaction.group_id == group_id, Reaction.message_id.in_(replaced))
        )
    rows = [
        {
            "group_id": group_id,
            "message_id": s.message_id,
            "user_id": r.user_id,
            "emoji": r.emoji,
            "timestamp": r.timestamp,
        }
        for s in summaries
        for r in s.reactors
        if r.user_id not in excluded
    ]
    for chunk in _chunks(rows):
        session.execute(_insert(session, Reaction).values(chunk).on_conflict_do_nothing())
    session.execute(
        delete(Interaction).where(
            Interaction.group_id == group_id,
            Interaction.type == "reaction",
            Interaction.message_id.in_(ids),
        )
    )
    # Filter both sides of the join by id: with only Message filtered, a group whose rows
    # the planner statistics haven't seen yet (a fresh back-fill) got a nested loop over
    # all its reactions x messages, ~20 s per batch.
    _insert_edges(
        session,
        _reaction_edges(
            session, group_id, Reaction.message_id.in_(ids), Message.message_id.in_(ids)
        ),
    )
    return len(rows)


def reaction_state(session: Session, group_id: int, message_ids: Iterable[int]) -> dict:
    """{message_id: (reaction_count, stored reaction rows)} for stored messages."""
    ids = list(message_ids)
    if not ids:
        return {}
    counts = dict(
        session.execute(
            select(Message.message_id, Message.reaction_count).where(
                Message.group_id == group_id, Message.message_id.in_(ids)
            )
        ).all()
    )
    rows = dict(
        session.execute(
            select(Reaction.message_id, func.count())
            .where(Reaction.group_id == group_id, Reaction.message_id.in_(ids))
            .group_by(Reaction.message_id)
        ).all()
    )
    return {mid: (count, rows.get(mid, 0)) for mid, count in counts.items()}


_ENRICHMENT_FIELDS = (
    "sentiment_score",
    "sentiment_label",
    "toxicity",
    "insult",
    "obscenity",
    "threat",
    "intent",
    "topics",
    "model_version",
)


def upsert_enrichment(session: Session, rows: list[dict]) -> None:
    for chunk in _chunks(rows):
        stmt = _insert(session, Enrichment).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Enrichment.group_id, Enrichment.message_id],
            set_={k: stmt.excluded[k] for k in _ENRICHMENT_FIELDS},
        )
        session.execute(stmt)


# --- privacy exclusions --------------------------------------------------------------------


def excluded_ids(session: Session) -> set[int]:
    return set(session.scalars(select(ExcludedUser.user_id)))


def resolve_user(session: Session, ref: str) -> int:
    """A numeric user id, or the id of a known @username (case-insensitive)."""
    ref = ref.strip().removeprefix("@")
    if ref.lstrip("-").isdigit():
        return int(ref)
    uid = session.scalar(select(User.user_id).where(func.lower(User.username) == ref.lower()))
    if uid is None:
        raise ValueError(
            f"Unknown username @{ref}: use the numeric Telegram id instead "
            "(members who never wrote in a scraped group aren't in the database)."
        )
    return uid


def exclude_user(session: Session, user_id: int, note: str | None = None) -> dict[str, int]:
    """Opt a member out: record the exclusion and delete everything stored about their messages.

    Removes their messages (enrichment and edges cascade), their reactions, and
    replies/mentions/reactions that point at them. Ingestion skips excluded senders from
    then on. Returns row counts.
    """
    session.execute(
        _insert(session, ExcludedUser)
        .values(user_id=user_id, note=note, created_at=datetime.now(UTC).replace(tzinfo=None))
        .on_conflict_do_update(index_elements=[ExcludedUser.user_id], set_={"note": note})
    )
    edges = session.execute(
        delete(Interaction).where(
            or_(Interaction.target_user_id == user_id, Interaction.source_user_id == user_id)
        )
    ).rowcount
    messages = session.execute(delete(Message).where(Message.sender_id == user_id)).rowcount
    reactions = session.execute(delete(Reaction).where(Reaction.user_id == user_id)).rowcount
    return {"messages": messages, "interactions": edges, "reactions": reactions}


def include_user(session: Session, user_id: int) -> bool:
    """Lift an exclusion. Their old messages only come back with a full re-scrape."""
    return bool(
        session.execute(delete(ExcludedUser).where(ExcludedUser.user_id == user_id)).rowcount
    )


# --- keeping the database in sync with Telegram ---------------------------------------------


def delete_messages(session: Session, group_id: int, message_ids: Iterable[int]) -> int:
    """Remove messages deleted in Telegram (enrichment and edges cascade)."""
    ids = list(message_ids)
    if not ids:
        return 0
    return session.execute(
        delete(Message).where(Message.group_id == group_id, Message.message_id.in_(ids))
    ).rowcount


def apply_edits(session: Session, group_id: int, records: Sequence[MessageRecord]) -> int:
    """Store edited messages whose text changed; returns how many changed.

    Their enrichment is dropped so the next enrichment run re-scores them, and their
    edges are rebuilt (an edit can add or remove @mentions).
    """
    if not records:
        return 0
    stored = dict(
        session.execute(
            select(Message.message_id, Message.text).where(
                Message.group_id == group_id,
                Message.message_id.in_([r.message_id for r in records]),
            )
        ).all()
    )
    changed = [r for r in records if r.message_id in stored and stored[r.message_id] != r.text]
    if not changed:
        return 0
    ids = [r.message_id for r in changed]
    upsert_messages(session, changed)
    session.execute(
        delete(Enrichment).where(Enrichment.group_id == group_id, Enrichment.message_id.in_(ids))
    )
    session.execute(
        delete(Interaction).where(Interaction.group_id == group_id, Interaction.message_id.in_(ids))
    )
    build_interactions(session, group_id, min_message_id=min(ids))
    return len(changed)


def stored_message_ids(session: Session, group_id: int, since: datetime | None = None) -> list[int]:
    stmt = select(Message.message_id).where(Message.group_id == group_id)
    if since is not None:
        stmt = stmt.where(Message.timestamp >= since)
    return list(session.scalars(stmt.order_by(Message.message_id)))
