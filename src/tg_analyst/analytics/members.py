"""Everything about one member. Pure functions over the frames from ``analytics.load``."""

import pandas as pd

from tg_analyst.analytics.conflict import written
from tg_analyst.analytics.metrics import human_messages


def member_summary(msgs: pd.DataFrame, edges: pd.DataFrame, user_id: int) -> dict:
    """Headline numbers for one member (an empty dict if they wrote nothing)."""
    human = human_messages(msgs)
    own = human[human["sender_id"] == user_id]
    if own.empty:
        return {}
    out_edges = edges[edges["source_user_id"] == user_id]
    in_edges = edges[edges["target_user_id"] == user_id]
    talk_out, talk_in = written(out_edges), written(in_edges)
    replies_to_them = human[
        human["reply_to_msg_id"].isin(own["message_id"]) & (human["sender_id"] != user_id)
    ]
    answered = replies_to_them["reply_to_msg_id"].nunique()
    return {
        "messages": len(own),
        "share_of_group": round(len(own) / len(human), 4),
        "first_seen": own["timestamp"].min(),
        "last_seen": own["timestamp"].max(),
        "active_days": int(own["timestamp"].dt.date.nunique()),
        "tone_given": _mean(own["sentiment_score"]),
        "tone_received": _mean(in_edges["sentiment_score"]),
        "negative_share": _share(own["sentiment_label"], "negative"),
        "questions": int((own["intent"] == "question").sum()),
        "thanks": int((own["intent"] == "thanks").sum()),
        "talks_to": int(talk_out["target_user_id"].nunique()),
        "addressed_by": int(talk_in["source_user_id"].nunique()),
        "reactions_given": len(out_edges) - len(talk_out),
        "reactions_received": len(in_edges) - len(talk_in),
        "messages_answered": round(answered / len(own), 3),
    }


def member_activity(msgs: pd.DataFrame, user_id: int, freq: str = "W") -> pd.DataFrame:
    """Messages and mean tone per period (default: week) for one member."""
    own = human_messages(msgs)
    own = own[own["sender_id"] == user_id]
    if own.empty:
        return pd.DataFrame(columns=["period", "messages", "tone"])
    own = own.assign(period=own["timestamp"].dt.to_period(freq).dt.start_time)
    return (
        own.groupby("period")
        .agg(messages=("message_id", "size"), tone=("sentiment_score", "mean"))
        .reset_index()
    )


def member_partners(edges: pd.DataFrame, user_id: int, n: int = 15) -> pd.DataFrame:
    """The people a member interacts with most, with tone in each direction.

    ``to_them`` / ``tone_to_them``: what the member sends; ``from_them`` /
    ``tone_from_them``: what the partner sends back.
    """
    cols = ["partner_id", "to_them", "tone_to_them", "from_them", "tone_from_them", "total"]
    out_e = edges[edges["source_user_id"] == user_id]
    in_e = edges[edges["target_user_id"] == user_id]
    if out_e.empty and in_e.empty:
        return pd.DataFrame(columns=cols)
    sent = out_e.groupby("target_user_id").agg(
        to_them=("message_id", "size"), tone_to_them=("sentiment_score", "mean")
    )
    got = in_e.groupby("source_user_id").agg(
        from_them=("message_id", "size"), tone_from_them=("sentiment_score", "mean")
    )
    both = sent.join(got, how="outer")
    both[["to_them", "from_them"]] = both[["to_them", "from_them"]].fillna(0).astype(int)
    both["total"] = both["to_them"] + both["from_them"]
    both.index.name = "partner_id"
    return both.sort_values("total", ascending=False).head(n).reset_index()[cols]


def _mean(values: pd.Series) -> float | None:
    values = values.dropna()
    return round(float(values.mean()), 3) if len(values) else None


def _share(labels: pd.Series, label: str) -> float | None:
    labels = labels.dropna()
    return round(float((labels == label).mean()), 3) if len(labels) else None
