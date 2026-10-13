"""How newcomers are received, and whether they stay. Pure functions.

A newcomer's story: they join, write their first message, someone answers (or nobody
does), and weeks later they are either still around or gone. These functions measure
each step so community managers can see whether a slow or cold welcome predicts churn.
"""

from datetime import timedelta

import pandas as pd

from tg_analyst.analytics.metrics import human_messages

OUTCOME_COLUMNS = [
    "user_id",
    "joined",
    "first_message",
    "first_reply",
    "first_reply_seconds",
    "first_reply_tone",
    "welcomed",
    "messages",
    "last_seen",
    "retained",
]


def join_times(msgs: pd.DataFrame) -> dict[int, pd.Timestamp]:
    """Earliest join service message per user (joiner = action users, else the sender)."""
    joins = msgs[msgs["service_action"] == "join"]
    out: dict[int, pd.Timestamp] = {}
    for ts, ids, sender in zip(
        joins["timestamp"], joins["action_user_ids"], joins["sender_id"], strict=False
    ):
        for uid in ids or ([sender] if pd.notna(sender) else []):
            out[int(uid)] = min(ts, out.get(int(uid), ts))
    return out


def newcomer_outcomes(
    msgs: pd.DataFrame,
    welcome_hours: float = 24,
    retention_days: int = 14,
) -> pd.DataFrame:
    """One row per member who joined inside the data window.

    - ``first_reply``: earliest reply by someone else to any of their messages
    - ``welcomed``: that reply came within ``welcome_hours`` of their first message
    - ``retained``: they wrote again ``retention_days`` or more after joining;
      NaN when the window ends too early to tell
    """
    joined = join_times(msgs)
    human = human_messages(msgs)
    if not joined or human.empty:
        return pd.DataFrame(columns=OUTCOME_COLUMNS)

    data_end = msgs["timestamp"].max()
    authors = human[["message_id", "sender_id"]].rename(
        columns={"message_id": "parent_id", "sender_id": "parent_author"}
    )
    replies = human[human["reply_to_msg_id"].notna()].merge(
        authors, left_on="reply_to_msg_id", right_on="parent_id"
    )
    replies = replies[replies["sender_id"] != replies["parent_author"]]

    rows = []
    for uid, joined_at in joined.items():
        own = human[(human["sender_id"] == uid) & (human["timestamp"] >= joined_at)]
        first_msg = own["timestamp"].min() if not own.empty else pd.NaT
        to_them = replies[replies["parent_author"] == uid].sort_values("timestamp")
        first = to_them.iloc[0] if not to_them.empty else None
        first_reply = first["timestamp"] if first is not None else pd.NaT
        wait = (first_reply - first_msg).total_seconds() if first is not None else None
        retained = None
        if joined_at + timedelta(days=retention_days) <= data_end:
            retained = bool((own["timestamp"] >= joined_at + timedelta(days=retention_days)).any())
        rows.append(
            {
                "user_id": uid,
                "joined": joined_at,
                "first_message": first_msg,
                "first_reply": first_reply,
                "first_reply_seconds": wait,
                "first_reply_tone": first["sentiment_score"] if first is not None else None,
                "welcomed": wait is not None and wait <= welcome_hours * 3600,
                "messages": len(own),
                "last_seen": own["timestamp"].max() if not own.empty else pd.NaT,
                "retained": retained,
            }
        )
    return pd.DataFrame(rows, columns=OUTCOME_COLUMNS).sort_values("joined").reset_index(drop=True)


def newcomer_summary(outcomes: pd.DataFrame) -> dict[str, float | int | None]:
    """Headline numbers, including retention split by whether the newcomer was welcomed."""
    if outcomes.empty:
        return {
            "joined": 0,
            "wrote": 0,
            "welcome_rate": None,
            "median_first_reply_seconds": None,
            "retention": None,
            "retention_welcomed": None,
            "retention_not_welcomed": None,
        }
    wrote = outcomes[outcomes["first_message"].notna()]
    judged = wrote[wrote["retained"].notna()]

    def rate(frame):
        return round(float(frame["retained"].astype(bool).mean()), 3) if len(frame) else None

    waits = wrote["first_reply_seconds"].dropna()
    return {
        "joined": len(outcomes),
        "wrote": len(wrote),
        "welcome_rate": round(float(wrote["welcomed"].mean()), 3) if len(wrote) else None,
        "median_first_reply_seconds": float(waits.median()) if len(waits) else None,
        "retention": rate(judged),
        "retention_welcomed": rate(judged[judged["welcomed"]]),
        "retention_not_welcomed": rate(judged[~judged["welcomed"]]),
    }
