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
    negative_below: float = -0.3,
    min_negative: int = 3,
    gap_days: float = 7,
) -> pd.DataFrame:
    """Find bursts of hostile exchanges between the same two members.

    Negative interactions (tone below ``negative_below``) between a pair are chained
    while consecutive ones are at most ``gap_days`` apart; a chain with at least
    ``min_negative`` interactions is an episode. ``mutual`` is True when both members
    were hostile to each other (a feud), False when it was one-sided (e.g. harassment).
    Episodes are sorted by size, largest first.
    """
    if edges.empty:
        return pd.DataFrame(columns=EPISODE_COLUMNS)
    e = _with_pair(edges)
    neg = e[e["sentiment_score"] < negative_below].sort_values("timestamp")
    episodes = []
    gap = timedelta(days=gap_days)
    for (a, b), grp in neg.groupby(["user_a", "user_b"]):
        chain_id = (grp["timestamp"].diff() > gap).cumsum()
        for _, chain in grp.groupby(chain_id):
            if len(chain) < min_negative:
                continue
            episodes.append(
                {
                    "user_a": a,
                    "user_b": b,
                    "start": chain["timestamp"].min(),
                    "end": chain["timestamp"].max(),
                    "negative_interactions": len(chain),
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
