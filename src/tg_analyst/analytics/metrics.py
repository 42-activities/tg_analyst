"""Pure metric functions: DataFrames in, DataFrames/dicts out. No I/O.

Inputs are the frames produced by ``analytics.load``. Bots and service messages are
excluded from member metrics; service joins are used for newcomer counts.
"""

import pandas as pd

from tg_analyst.enrich.toxicity import TOXIC_AT


def human_messages(msgs: pd.DataFrame) -> pd.DataFrame:
    return msgs[~msgs["is_service"] & ~msgs["is_bot"] & msgs["sender_id"].notna()]


def kpis(msgs: pd.DataFrame) -> dict[str, float | int | None]:
    human = human_messages(msgs)
    if human.empty:
        return {
            "messages": 0,
            "active_members": 0,
            "avg_dau": 0.0,
            "avg_tone": None,
            "negative_share": None,
            "reply_share": None,
            "joins": 0,
            "avg_wau": 0.0,
            "media_share": None,
            "median_first_reply_seconds": None,
            "insult_share": None,
            "swearing_share": None,
        }
    daily_active = human.groupby(human["timestamp"].dt.date)["sender_id"].nunique()
    weekly_active = human.groupby(human["timestamp"].dt.to_period("W"))["sender_id"].nunique()
    scored = human["sentiment_score"].dropna()
    labels = human["sentiment_label"].dropna()
    return {
        "messages": len(human),
        "active_members": int(human["sender_id"].nunique()),
        "avg_dau": round(float(daily_active.mean()), 1),
        "avg_tone": round(float(scored.mean()), 3) if not scored.empty else None,
        "negative_share": round(float((labels == "negative").mean()), 3)
        if not labels.empty
        else None,
        "reply_share": round(float(human["reply_to_msg_id"].notna().mean()), 3),
        "joins": int((msgs["service_action"] == "join").sum()),
        "avg_wau": round(float(weekly_active.mean()), 1),
        "media_share": round(float(human["has_media"].astype(bool).mean()), 3),
        "median_first_reply_seconds": median_first_reply_seconds(human),
        "insult_share": _toxic_share(human, "insult"),
        "swearing_share": _toxic_share(human, "obscenity"),
    }


def _toxic_share(human: pd.DataFrame, column: str) -> float | None:
    """Share of toxicity-scored messages flagged for ``column`` (None if none scored)."""
    if column not in human:
        return None
    scored = human[column].dropna()
    return round(float((scored >= TOXIC_AT).mean()), 4) if not scored.empty else None


def median_first_reply_seconds(human: pd.DataFrame) -> float | None:
    """Median wait between a message and the first reply to it from someone else.

    Only messages that got a reply count; unanswered ones are excluded, not infinite.
    """
    replies = human[human["reply_to_msg_id"].notna()][["reply_to_msg_id", "sender_id", "timestamp"]]
    parents = human[["message_id", "sender_id", "timestamp"]]
    joined = replies.merge(
        parents, left_on="reply_to_msg_id", right_on="message_id", suffixes=("", "_parent")
    )
    joined = joined[joined["sender_id"] != joined["sender_id_parent"]]
    if joined.empty:
        return None
    first = joined.groupby("message_id")["timestamp"].min()
    asked = joined.groupby("message_id")["timestamp_parent"].first()
    return float((first - asked).dt.total_seconds().median())


def daily_activity(msgs: pd.DataFrame, rolling_days: int = 7) -> pd.DataFrame:
    """Per-day message count, active members and mean tone (plus a rolling tone)."""
    human = human_messages(msgs)
    if human.empty:
        return pd.DataFrame(columns=["date", "messages", "active_members", "tone", "tone_rolling"])
    g = human.groupby(human["timestamp"].dt.floor("D"))
    out = pd.DataFrame(
        {
            "messages": g.size(),
            "active_members": g["sender_id"].nunique(),
            "tone": g["sentiment_score"].mean(),
        }
    )
    out.index.name = "date"
    # Fill silent days with zero activity but leave their tone undefined.
    out = out.asfreq("D")
    out[["messages", "active_members"]] = out[["messages", "active_members"]].fillna(0).astype(int)
    out["tone_rolling"] = out["tone"].rolling(rolling_days, min_periods=1).mean()
    return out.reset_index()


def activity_heatmap(msgs: pd.DataFrame) -> pd.DataFrame:
    """Weekday (rows, Mon..Sun) by hour (columns 0..23) message counts, in UTC."""
    human = human_messages(msgs)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    grid = pd.crosstab(human["timestamp"].dt.dayofweek, human["timestamp"].dt.hour)
    grid = grid.reindex(index=range(7), columns=range(24), fill_value=0)
    grid.index = days
    return grid


def member_stats(msgs: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """Per member: activity, tone given (own messages) and tone received (edges in)."""
    human = human_messages(msgs)
    if human.empty:
        return pd.DataFrame()
    if "insult" in human:
        human = human.assign(is_insult=human["insult"].fillna(0) >= TOXIC_AT)
    else:
        human = human.assign(is_insult=False)
    stats = human.groupby("sender_id").agg(
        name=("name", "first"),
        insults_sent=("is_insult", "sum"),
        messages=("message_id", "size"),
        tone_given=("sentiment_score", "mean"),
        questions=("intent", lambda s: int((s == "question").sum())),
        first_seen=("timestamp", "min"),
        last_seen=("timestamp", "max"),
    )
    if not edges.empty:
        e = edges.assign(
            is_insult=edges["insult"].fillna(0) >= TOXIC_AT if "insult" in edges else False
        )
        incoming = e.groupby("target_user_id").agg(
            interactions_received=("message_id", "size"),
            tone_received=("sentiment_score", "mean"),
            insults_received=("is_insult", "sum"),
        )
        outgoing = edges.groupby("source_user_id").agg(
            people_reached=("target_user_id", "nunique"),
        )
        stats = stats.join(incoming).join(outgoing)
    for col in ("interactions_received", "people_reached", "insults_sent", "insults_received"):
        if col not in stats:
            stats[col] = 0
        stats[col] = stats[col].fillna(0).astype(int)
    if "tone_received" not in stats:
        stats["tone_received"] = float("nan")
    return stats.sort_values("messages", ascending=False).reset_index(names="user_id")


def top_relationships(edges: pd.DataFrame, names: dict[int, str], n: int = 15) -> pd.DataFrame:
    """Strongest member pairs (undirected) with volume and average tone each way."""
    if edges.empty:
        return pd.DataFrame(
            columns=["member_a", "member_b", "interactions", "tone_a_to_b", "tone_b_to_a", "tone"]
        )
    e = edges.copy()
    e["a"] = e[["source_user_id", "target_user_id"]].min(axis=1)
    e["b"] = e[["source_user_id", "target_user_id"]].max(axis=1)
    e["a_to_b"] = e["source_user_id"] == e["a"]
    pairs = e.groupby(["a", "b"]).agg(
        interactions=("message_id", "size"), tone=("sentiment_score", "mean")
    )
    directed = e.groupby(["a", "b", "a_to_b"])["sentiment_score"].mean().unstack()
    pairs["tone_a_to_b"] = directed.get(True)
    pairs["tone_b_to_a"] = directed.get(False)
    pairs = pairs.sort_values("interactions", ascending=False).head(n).reset_index()
    pairs["member_a"] = pairs["a"].map(lambda u: names.get(u, str(u)))
    pairs["member_b"] = pairs["b"].map(lambda u: names.get(u, str(u)))
    return pairs[["member_a", "member_b", "interactions", "tone_a_to_b", "tone_b_to_a", "tone"]]


def pseudonym_map(user_ids) -> dict[int, str]:
    """Stable 'Member N' labels, numbered by user id so they match across tables."""
    return {uid: f"Member {i + 1}" for i, uid in enumerate(sorted(set(user_ids)))}
