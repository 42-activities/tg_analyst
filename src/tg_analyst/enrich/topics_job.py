"""The ``topics`` job: threads -> embeddings -> clusters -> labels -> summaries, per group.

1. Build the group's threads and embed those that are new, changed (``content_key``) or
   embedded by another model; drop rows of threads that no longer exist.
2. Cluster the stored vectors per quarter, merge the quarters into topics, and pick
   three representative threads per topic.
3. Label every topic with its distinctive keywords, and summarize the largest ones with
   Claude when a summarizer is configured. A topic whose representatives are unchanged
   keeps its earlier summary, so a daily run costs nothing when nothing changed.
4. Replace the group's ``context_topics`` rows in one transaction.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tg_analyst.analytics import load
from tg_analyst.analytics.context_topics import (
    cluster_windows,
    merge_windows,
    representatives,
    topic_table,
)
from tg_analyst.analytics.threads import build_threads, content_keys, thread_texts
from tg_analyst.analytics.topics import distinctive_terms, message_terms, without_names
from tg_analyst.enrich.embeddings import EmbeddingBackend
from tg_analyst.enrich.summaries import Summarizer
from tg_analyst.progress import Progress, no_progress
from tg_analyst.storage import repository as repo
from tg_analyst.storage.models import ContextTopic, ThreadEmbedding

log = logging.getLogger(__name__)

EMBED_BATCH = 64
EMBED_SHARE = 0.85  # of the progress bar; clustering and summaries take the rest
TEXT_CHARS = 2000  # per thread, for the embedding (the model truncates at 512 tokens)
SUMMARY_TEXT_CHARS = 1500  # per thread sent to Claude


@dataclass(slots=True)
class TopicStats:
    threads: int = 0
    embedded: int = 0
    removed: int = 0
    topics: int = 0
    summarized: int = 0
    summaries_kept: int = 0


def _all_messages(session: Session, group_id: int) -> pd.DataFrame:
    return load.messages(session.get_bind(), group_id, datetime(1970, 1, 1), datetime(2100, 1, 1))


def embed_threads(
    session: Session,
    group_id: int,
    msgs: pd.DataFrame,
    membership: pd.DataFrame,
    backend: EmbeddingBackend,
    stats: TopicStats,
    progress: Progress = no_progress,
) -> None:
    keys = content_keys(msgs, membership)
    stats.threads = len(keys)
    stored = dict(
        session.execute(
            select(ThreadEmbedding.root_message_id, ThreadEmbedding.content_key).where(
                ThreadEmbedding.group_id == group_id,
                ThreadEmbedding.model_version == backend.version,
            )
        ).all()
    )
    gone = set(
        session.scalars(
            select(ThreadEmbedding.root_message_id).where(ThreadEmbedding.group_id == group_id)
        )
    ) - set(keys.index)
    for chunk in _id_chunks(sorted(gone)):
        stats.removed += session.execute(
            delete(ThreadEmbedding).where(
                ThreadEmbedding.group_id == group_id, ThreadEmbedding.root_message_id.in_(chunk)
            )
        ).rowcount
    session.commit()

    todo = [int(root) for root, key in keys.items() if stored.get(root) != key]
    progress(0.0, f"{len(keys):,} threads, {len(todo):,} to embed")
    spans = membership.merge(msgs[["message_id", "timestamp"]], on="message_id")
    spans = spans.groupby("root_id")["timestamp"].agg(["min", "max"])
    ids = membership.groupby("root_id")["message_id"].apply(list)
    for done in range(0, len(todo), EMBED_BATCH):
        batch = todo[done : done + EMBED_BATCH]
        texts = thread_texts(msgs, membership, roots=batch, max_chars=TEXT_CHARS)
        vectors = backend.embed([texts[root] for root in batch])
        rows = [
            {
                "group_id": group_id,
                "root_message_id": root,
                "message_ids": [int(m) for m in ids[root]],
                "content_key": keys[root],
                "model_version": backend.version,
                "vector": [float(x) for x in vector],
                "start": spans.at[root, "min"].to_pydatetime(),
                "end": spans.at[root, "max"].to_pydatetime(),
            }
            for root, vector in zip(batch, vectors, strict=True)
        ]
        stmt = repo._insert(session, ThreadEmbedding).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ThreadEmbedding.group_id, ThreadEmbedding.root_message_id],
            set_={
                k: stmt.excluded[k]
                for k in ("message_ids", "content_key", "model_version", "vector", "start", "end")
            },
        )
        session.execute(stmt)
        session.commit()
        stats.embedded += len(batch)
        progress(
            EMBED_SHARE * stats.embedded / len(todo),
            f"{stats.embedded:,} of {len(todo):,} threads embedded ({len(keys):,} in total)",
        )


def _id_chunks(ids: list[int], size: int = 5000):
    for start in range(0, len(ids), size):
        yield ids[start : start + size]


def _summary_key(roots: list[int], keys: pd.Series) -> str:
    material = "|".join(f"{root}:{keys.get(root, '')}" for root in roots)
    return hashlib.sha1(material.encode()).hexdigest()


def build_topics(
    session: Session,
    group_id: int,
    msgs: pd.DataFrame,
    membership: pd.DataFrame,
    model_version: str,
    threshold: float,
    stats: TopicStats,
    summarizer: Summarizer | None = None,
    summary_top: int = 25,
    progress: Progress = no_progress,
) -> None:
    rows = session.execute(
        select(ThreadEmbedding.root_message_id, ThreadEmbedding.start, ThreadEmbedding.vector)
        .where(ThreadEmbedding.group_id == group_id, ThreadEmbedding.model_version == model_version)
        .order_by(ThreadEmbedding.start)
    ).all()
    previous = {
        t.summary_key: t
        for t in session.scalars(select(ContextTopic).where(ContextTopic.group_id == group_id))
        if t.summary_key and t.summary
    }
    topics: list[dict] = []
    if rows:
        progress(EMBED_SHARE, f"Clustering {len(rows):,} threads")
        roots = np.array([r.root_message_id for r in rows])
        vectors = np.array([r.vector for r in rows], dtype=np.float32)
        threads = pd.DataFrame({"root_id": roots, "start": [r.start for r in rows]})
        assignment = merge_windows(
            cluster_windows(threads, vectors, threshold), roots, vectors, threshold
        )
        topics = _describe(
            msgs, membership, assignment, roots, vectors, model_version, threshold, group_id
        )

    keys = content_keys(msgs, membership)
    candidates = topics[:summary_top] if summarizer else []
    for done, topic in enumerate(candidates, start=1):
        reps = [topic["centre_root"], *topic["edge_roots"]]
        topic["summary_key"] = key = _summary_key(reps, keys)
        if (old := previous.get(key)) and old.summary_model == summarizer.model:
            topic.update(title=old.title, summary=old.summary, summary_model=old.summary_model)
            stats.summaries_kept += 1
            continue
        texts = thread_texts(msgs, membership, roots=reps, max_chars=SUMMARY_TEXT_CHARS)
        result = summarizer.summarize([texts[r] for r in reps if r in texts])
        if result:
            topic.update(title=result.title, summary=result.summary, summary_model=summarizer.model)
            stats.summarized += 1
        progress(
            EMBED_SHARE + (1 - EMBED_SHARE) * done / len(candidates),
            f"{done} of {len(candidates)} topic summaries",
        )

    session.execute(delete(ContextTopic).where(ContextTopic.group_id == group_id))
    if topics:
        for chunk in repo._chunks(topics):
            session.execute(repo._insert(session, ContextTopic).values(chunk))
    session.commit()
    stats.topics = len(topics)


def _describe(msgs, membership, assignment, roots, vectors, model_version, threshold, group_id):
    """Topic rows (without summaries), ordered by messages, most first."""
    table = topic_table(msgs, membership, assignment)
    position = {int(root): i for i, root in enumerate(roots)}
    message_topic = membership.set_index("message_id")["root_id"].map(assignment).dropna()
    in_topics = msgs[msgs["message_id"].isin(message_topic.index)]
    names = pd.concat([msgs["username"], msgs["display_name"]]).dropna().unique()
    labels = distinctive_terms(without_names(message_terms(in_topics), names), message_topic)
    now = datetime.now(UTC).replace(tzinfo=None)
    members = assignment.groupby(assignment).groups
    topics = []
    for rank, old_id in enumerate(table["topic_id"]):
        thread_roots = sorted(int(r) for r in members[old_id])
        reps = representatives(vectors[[position[r] for r in thread_roots]], thread_roots)
        topics.append(
            {
                "group_id": group_id,
                "topic_id": rank,
                "model_version": model_version,
                "threshold": threshold,
                "label": labels.get(old_id, "")[:255] or f"Topic {rank + 1}",
                "title": None,
                "summary": None,
                "summary_model": None,
                "summary_key": None,
                "centre_root": reps[0],
                "edge_roots": reps[1:],
                "thread_roots": thread_roots,
                "created_at": now,
            }
        )
    return topics


def run_topics(
    session: Session,
    group_id: int,
    backend: EmbeddingBackend,
    threshold: float,
    summarizer: Summarizer | None = None,
    summary_top: int = 25,
    progress: Progress = no_progress,
) -> TopicStats:
    stats = TopicStats()
    progress(None, "Loading messages and building threads")
    msgs = _all_messages(session, group_id)
    membership = build_threads(msgs)
    embed_threads(session, group_id, msgs, membership, backend, stats, progress)
    build_topics(
        session,
        group_id,
        msgs,
        membership,
        backend.version,
        threshold,
        stats,
        summarizer,
        summary_top,
        progress,
    )
    progress(1.0, f"{stats.topics:,} topics from {stats.threads:,} threads")
    return stats
