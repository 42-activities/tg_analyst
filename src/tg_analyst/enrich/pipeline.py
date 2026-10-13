"""Batch enrichment: score messages that are new or were scored by an older model."""

import logging
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from tg_analyst.enrich.clean import CLEAN_VERSION, clean_text, is_analyzable
from tg_analyst.enrich.intent import INTENT_VERSION, detect_intent
from tg_analyst.enrich.sentiment import SentimentBackend
from tg_analyst.storage import repository as repo
from tg_analyst.storage.models import Enrichment, Message

log = logging.getLogger(__name__)


def model_version(backend: SentimentBackend) -> str:
    return f"{backend.version}|intent-{INTENT_VERSION}|clean-{CLEAN_VERSION}"


@dataclass(slots=True)
class EnrichStats:
    scored: int = 0
    skipped: int = 0  # no analyzable text (stickers, media-only, links only)


def _pending(session: Session, version: str, group_id: int | None, force: bool, limit: int):
    stmt = (
        select(Message.group_id, Message.message_id, Message.text)
        .outerjoin(
            Enrichment,
            and_(
                Enrichment.group_id == Message.group_id, Enrichment.message_id == Message.message_id
            ),
        )
        .where(Message.is_service.is_(False))
        .order_by(Message.group_id, Message.message_id)
        .limit(limit)
    )
    if not force:
        stmt = stmt.where(or_(Enrichment.message_id.is_(None), Enrichment.model_version != version))
    if group_id is not None:
        stmt = stmt.where(Message.group_id == group_id)
    return stmt


def enrich(
    session: Session,
    backend: SentimentBackend,
    group_id: int | None = None,
    batch_size: int = 256,
    force: bool = False,
) -> EnrichStats:
    """Score pending messages in batches, committing after each batch.

    With ``force`` every message is re-scored once (keyset pagination avoids re-reading rows
    already written in this run).
    """
    version = model_version(backend)
    stats = EnrichStats()
    after: tuple[int, int] | None = None

    while True:
        stmt = _pending(session, version, group_id, force, batch_size)
        if after is not None:
            gid, mid = after
            stmt = stmt.where(
                or_(Message.group_id > gid, and_(Message.group_id == gid, Message.message_id > mid))
            )
        rows = session.execute(stmt).all()
        if not rows:
            break
        after = (rows[-1].group_id, rows[-1].message_id)

        cleaned = [clean_text(r.text) for r in rows]
        todo = [i for i, c in enumerate(cleaned) if is_analyzable(c)]
        scores = backend.score([cleaned[i] for i in todo]) if todo else []
        results = dict(zip(todo, scores, strict=True))

        out = []
        for i, row in enumerate(rows):
            res = results.get(i)
            out.append(
                {
                    "group_id": row.group_id,
                    "message_id": row.message_id,
                    "sentiment_score": res.score if res else None,
                    "sentiment_label": res.label if res else None,
                    "intent": detect_intent(cleaned[i], res.label if res else None)
                    if res
                    else None,
                    "toxicity": None,
                    "topics": None,
                    "model_version": version,
                }
            )
        repo.upsert_enrichment(session, out)
        session.commit()

        stats.scored += len(todo)
        stats.skipped += len(rows) - len(todo)
        log.info("Enriched %d messages (%d without text)", stats.scored, stats.skipped)

    return stats
