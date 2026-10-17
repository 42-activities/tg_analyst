"""Conversation threads: messages grouped into the exchanges they belong to. Pure functions.

A thread is a connected set of human messages:

1. **reply chains**: a reply joins the thread of the message it answers;
2. **same-author bursts**: a message that isn't a reply joins the sender's previous message
   if it came within ``burst_seconds`` (people split one thought into several messages);
3. **hubs** split: a message with at least ``hub_replies`` direct replies is a channel post
   or a forum topic, not a conversation. Replies to it don't join it; each starts its own
   thread, and bursts don't cross from one hub to another.

Threads are identified by their first (lowest) message id, ``root_id``.
"""

import hashlib

import numpy as np
import pandas as pd

from tg_analyst.analytics.conflict import flag
from tg_analyst.analytics.metrics import human_messages
from tg_analyst.enrich.clean import clean_text

MEMBERSHIP_COLUMNS = ["message_id", "root_id"]
THREAD_COLUMNS = [
    "root_id",
    "messages",
    "members",
    "reactions",
    "start",
    "end",
    "tone",
    "insult_share",
]


def _find(parent: dict[int, int], x: int) -> int:
    root = x
    while parent.get(root, root) != root:
        root = parent[root]
    while parent.get(x, x) != root:  # path compression
        parent[x], x = root, parent[x]
    return root


def _union(parent: dict[int, int], a: int, b: int) -> None:
    """Join two components; the smaller id stays the root, so root = first message."""
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[max(ra, rb)] = min(ra, rb)


def hubs(msgs: pd.DataFrame, hub_replies: int = 50) -> set[int]:
    """Messages with at least ``hub_replies`` direct replies (channel posts, forum topics)."""
    counts = msgs["reply_to_msg_id"].dropna().astype(int).value_counts()
    return set(counts[counts >= hub_replies].index)


def build_threads(
    msgs: pd.DataFrame,
    burst_seconds: int = 120,
    min_messages: int = 3,
    hub_replies: int = 50,
) -> pd.DataFrame:
    """``message_id -> root_id`` for every human message in a thread of ``min_messages``+.

    Bots, service messages and hub messages are never part of a thread.
    """
    hub_ids = hubs(msgs, hub_replies)
    human = human_messages(msgs)
    human = human[~human["message_id"].isin(hub_ids)].sort_values(["timestamp", "message_id"])
    if human.empty:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS, dtype="int64")

    ids = human["message_id"].astype(int).to_numpy()
    known = set(ids.tolist())
    reply = human["reply_to_msg_id"].to_numpy()
    top = human["thread_id"].to_numpy() if "thread_id" in human else np.full(len(human), np.nan)
    seconds = (human["timestamp"] - pd.Timestamp(0)).dt.total_seconds().to_numpy()
    senders = human["sender_id"].to_numpy()

    parent: dict[int, int] = {}
    last: dict[int, tuple[int, int, int | None]] = {}  # sender -> (message, second, hub)
    for m, r, t, s, sender in zip(ids.tolist(), reply, top, seconds, senders, strict=True):
        r = None if pd.isna(r) else int(r)
        hub = r if r in hub_ids else (int(t) if not pd.isna(t) and int(t) in hub_ids else None)
        if r is not None and r in known:
            _union(parent, m, r)
        elif r is None or r in hub_ids:
            prev = last.get(sender)
            if prev is not None and s - prev[1] <= burst_seconds and prev[2] == hub:
                _union(parent, m, prev[0])
        last[sender] = (m, int(s), hub)

    roots = np.fromiter((_find(parent, m) for m in ids.tolist()), dtype="int64", count=len(ids))
    membership = pd.DataFrame({"message_id": ids.astype("int64"), "root_id": roots})
    sizes = membership["root_id"].map(membership["root_id"].value_counts())
    return membership[sizes >= min_messages].sort_values("message_id").reset_index(drop=True)


def thread_table(msgs: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    """One row per thread: size, people, reactions, time span, mean tone and insult share.

    Tone is the mean ``sentiment_score`` of scored messages only (unscored are not 0).
    """
    if membership.empty:
        return pd.DataFrame(columns=THREAD_COLUMNS)
    frame = membership.merge(msgs, on="message_id")
    frame["reactions"] = frame["reaction_count"].fillna(0) if "reaction_count" in frame else 0
    frame["insult"] = flag(frame, "insult") | flag(frame, "threat")
    out = frame.groupby("root_id").agg(
        messages=("message_id", "size"),
        members=("sender_id", "nunique"),
        reactions=("reactions", "sum"),
        start=("timestamp", "min"),
        end=("timestamp", "max"),
        tone=("sentiment_score", "mean"),
        insult_share=("insult", "mean"),
    )
    out["reactions"] = out["reactions"].astype(int)
    return out.reset_index()[THREAD_COLUMNS]


def _speaker(index: int) -> str:
    """A, B, ..., Z, AA, AB, ...: per-thread pseudonyms."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(ord("A") + rest) + name
    return name


def thread_text(rows: pd.DataFrame, max_chars: int = 2000) -> str:
    """One thread's messages as ``A: ...`` lines, safe to embed or send to a model.

    Speakers are per-thread letters (never ids or names), @mentions are masked and URLs
    replaced (``clean_text``). Media-only messages are skipped. When the thread is longer
    than ``max_chars``, the first three messages are kept, then the longest of the rest,
    in their original order.
    """
    rows = rows.sort_values("message_id")
    speakers: dict[int, str] = {}
    lines: list[tuple[int, str]] = []
    for position, (sender, text) in enumerate(zip(rows["sender_id"], rows["text"], strict=True)):
        cleaned = clean_text(text) if isinstance(text, str) else ""
        if not cleaned:
            continue
        name = speakers.setdefault(sender, _speaker(len(speakers)))
        lines.append((position, f"{name}: {cleaned}"))

    def total(chosen) -> int:
        return sum(len(line) + 1 for _, line in chosen)

    if total(lines) <= max_chars:
        return "\n".join(line for _, line in lines)
    head = [lines[0]]
    rest = sorted(lines[1:], key=lambda item: -len(item[1]))
    for item in lines[1:3] + rest:
        if item not in head and total([*head, item]) <= max_chars:
            head.append(item)
    if total(head) > max_chars:  # the first message alone is too long
        return head[0][1][:max_chars]
    return "\n".join(line for _, line in sorted(head))


def _by_thread(msgs: pd.DataFrame, membership: pd.DataFrame, roots=None):
    if roots is not None:
        membership = membership[membership["root_id"].isin(set(roots))]
    frame = membership.merge(msgs[["message_id", "sender_id", "text"]], on="message_id")
    return frame.sort_values("message_id").groupby("root_id", sort=True)


def thread_texts(
    msgs: pd.DataFrame, membership: pd.DataFrame, roots=None, max_chars: int = 2000
) -> dict[int, str]:
    """``root_id -> thread_text`` for every thread (or only ``roots``)."""
    return {
        int(root): thread_text(rows, max_chars)
        for root, rows in _by_thread(msgs, membership, roots)
    }


def content_keys(msgs: pd.DataFrame, membership: pd.DataFrame, column: str = "text") -> pd.Series:
    """``root_id -> hash`` of the thread's message ids and texts, to detect changed threads.

    ``column`` may hold the text or a hash of it (``load.message_skeleton``'s ``text_hash``).
    """
    if membership.empty:
        return pd.Series(dtype=object)
    frame = membership.merge(msgs[["message_id", column]], on="message_id")
    frame = frame.sort_values("message_id")
    parts = frame["message_id"].astype(str) + "\x00" + frame[column].fillna("").astype(str)
    joined = parts.groupby(frame["root_id"]).agg("\x01".join)
    return joined.map(lambda value: hashlib.sha1(value.encode()).hexdigest())
