"""Tone between pairs over time, and detection of conflict episodes. Pure functions.

Input is the edge frame from ``analytics.load.interactions``: one row per reply/mention,
carrying the sentiment of the message that created it.
"""

from datetime import timedelta

import pandas as pd

EPISODE_COLUMNS = [
    "user_a",
    "user_b",
    "start",
    "end",
    "negative_interactions",
    "hostile_share",
    "mutual",
    "mean_tone",
]


def _with_pair(edges: pd.DataFrame) -> pd.DataFrame:
    e = edges.copy()
    e["user_a"] = e[["source_user_id", "target_user_id"]].min(axis=1)
    e["user_b"] = e[["source_user_id", "target_user_id"]].max(axis=1)
    return e


def pair_tone_timeline(edges: pd.DataFrame, freq: str = "W") -> pd.DataFrame:
    """Interactions and mean tone per unordered pair per period (default: week)."""
    if edges.empty:
        return pd.DataFrame(columns=["user_a", "user_b", "period", "interactions", "tone"])
    e = _with_pair(edges)
    e["period"] = e["timestamp"].dt.to_period(freq).dt.start_time
    return (
        e.groupby(["user_a", "user_b", "period"])
        .agg(interactions=("message_id", "size"), tone=("sentiment_score", "mean"))
        .reset_index()
    )


def detect_conflicts(
    edges: pd.DataFrame,
    negative_below: float | None = None,
    min_negative: int = 3,
    gap_days: float = 7,
    min_hostile_share: float = 0.5,
) -> pd.DataFrame:
    """Find bursts of hostile exchanges between the same two members.

    Hostile interactions (tone at or below ``negative_below``) between a pair are chained
    while consecutive ones are at most ``gap_days`` apart; a chain with at least
    ``min_negative`` interactions is an episode. ``mutual`` is True when both members
    were hostile to each other (a feud), False when it was one-sided (e.g. harassment).

    By default ``negative_below`` adapts to the group: the tone of its most negative 10%
    of interactions, and never above -0.3. Chats differ hugely in how rough everyday
    talk is; a fixed cut-off flags half of a casual chat while missing feuds in a polite
    one.

    ``hostile_share`` is the fraction of *all* the pair's interactions during the episode
    that were negative. Friends who banter or swear a lot exchange many negative-scored
    messages among many neutral ones; requiring ``min_hostile_share`` keeps only episodes
    where hostility dominated. Episodes are sorted by size, largest first.
    """
    if edges.empty:
        return pd.DataFrame(columns=EPISODE_COLUMNS)
    e = _with_pair(edges)
    if negative_below is None:
        negative_below = hostility_threshold(e["sentiment_score"])
    neg = e[e["sentiment_score"] <= negative_below].sort_values("timestamp")
    episodes = []
    gap = timedelta(days=gap_days)
    pair_all = {k: g["timestamp"] for k, g in e.groupby(["user_a", "user_b"])}
    for (a, b), grp in neg.groupby(["user_a", "user_b"]):
        chain_id = (grp["timestamp"].diff() > gap).cumsum()
        for _, chain in grp.groupby(chain_id):
            if len(chain) < min_negative:
                continue
            start, end = chain["timestamp"].min(), chain["timestamp"].max()
            all_ts = pair_all[(a, b)]
            total = int(((all_ts >= start) & (all_ts <= end)).sum())
            share = len(chain) / total
            if share < min_hostile_share:
                continue
            episodes.append(
                {
                    "user_a": a,
                    "user_b": b,
                    "start": start,
                    "end": end,
                    "negative_interactions": len(chain),
                    "hostile_share": round(share, 3),
                    "mutual": chain["source_user_id"].nunique() == 2,
                    "mean_tone": float(chain["sentiment_score"].mean()),
                }
            )
    if not episodes:
        return pd.DataFrame(columns=EPISODE_COLUMNS)
    return (
        pd.DataFrame(episodes, columns=EPISODE_COLUMNS)
        .sort_values(["negative_interactions", "mean_tone"], ascending=[False, True])
        .reset_index(drop=True)
    )


def hostility_threshold(tones: pd.Series, quantile: float = 0.1, ceiling: float = -0.3) -> float:
    """Group-relative tone below which an interaction counts as hostile."""
    scored = tones.dropna()
    if scored.empty:
        return ceiling
    return float(min(ceiling, scored.quantile(quantile)))


TRANSCRIPT_COLUMNS = [
    "message_id",
    "timestamp",
    "sender_id",
    "text",
    "sentiment_score",
    "reply_to_msg_id",
    "kind",
    "hostile",
]


def episode_messages(
    msgs: pd.DataFrame,
    edges: pd.DataFrame,
    user_a: int,
    user_b: int,
    start,
    end,
    negative_below: float,
    pad_hours: float = 2,
    limit: int = 300,
) -> pd.DataFrame:
    """The message chain behind a conflict episode, oldest first.

    Includes every reply/mention between the two members from ``pad_hours`` before the
    episode to ``pad_hours`` after it (``kind="exchange"``), plus the messages those
    replies answer when they are not already in the chain (``kind="context"``), so the
    reader can see what each hostile reply was reacting to. ``hostile`` marks exchange
    messages at or below ``negative_below``.
    """
    pad = timedelta(hours=pad_hours)
    pair = {user_a, user_b}
    window = edges[
        edges["source_user_id"].isin(pair)
        & edges["target_user_id"].isin(pair)
        & (edges["source_user_id"] != edges["target_user_id"])
        & (edges["timestamp"] >= pd.Timestamp(start) - pad)
        & (edges["timestamp"] <= pd.Timestamp(end) + pad)
    ]
    ids = set(window["message_id"])
    if not ids:
        return pd.DataFrame(columns=TRANSCRIPT_COLUMNS)
    cols = ["message_id", "timestamp", "sender_id", "text", "sentiment_score", "reply_to_msg_id"]
    exchange = msgs[msgs["message_id"].isin(ids)][cols].assign(kind="exchange")
    parent_ids = set(exchange["reply_to_msg_id"].dropna().astype(int)) - ids
    context = msgs[msgs["message_id"].isin(parent_ids)][cols].assign(kind="context")
    out = pd.concat([exchange, context], ignore_index=True).sort_values("timestamp")
    out["hostile"] = (out["kind"] == "exchange") & (out["sentiment_score"] <= negative_below)
    return out.head(limit).reset_index(drop=True)[TRANSCRIPT_COLUMNS]
