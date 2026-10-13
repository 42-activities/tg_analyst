"""Idempotent write operations and edge building.

Upserts use PostgreSQL's native INSERT ... ON CONFLICT.
"""

from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import datetime

from sqlalchemy import and_, case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from tg_analyst.storage.models import Enrichment, Group, Interaction, Message, User
from tg_analyst.storage.records import GroupRecord, MessageRecord, UserRecord

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


_ENRICHMENT_FIELDS = (
    "sentiment_score",
    "sentiment_label",
    "toxicity",
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
