"""Rule-based member roles. Pure functions over the frames from ``analytics.load``.

Thresholds are relative to the group (percentiles, medians), so the same rules work for
a 30-person chat and a 3,000-person one. Each member gets one primary role, checked in
this order: newcomer, lurker, critic, helper, connector, starter, regular.
"""

from datetime import timedelta

import pandas as pd

from tg_analyst.analytics.metrics import human_messages
from tg_analyst.analytics.newcomers import join_times

ROLE_ORDER = ["newcomer", "lurker", "critic", "helper", "connector", "starter", "regular"]

ROLE_DESCRIPTIONS = {
    "newcomer": "Joined recently",
    "lurker": "Rarely writes",
    "critic": "Mostly negative tone",
    "helper": "Answers others' questions, friendly tone",
    "connector": "Bridges otherwise separate parts of the community",
    "starter": "Starts conversations that others join",
    "regular": "Active member without a dominant pattern",
}


def member_features(msgs: pd.DataFrame, centrality: pd.DataFrame) -> pd.DataFrame:
    """Per-member behaviour used by :func:`classify_roles`, indexed by user_id."""
    human = human_messages(msgs)
    if human.empty:
        return pd.DataFrame()
    by_sender = human.groupby("sender_id")
    feats = pd.DataFrame(
        {
            "messages": by_sender.size(),
            "tone_given": by_sender["sentiment_score"].mean(),
            "first_seen": by_sender["timestamp"].min(),
        }
    )

    # Answers: replies to somebody else's question.
    questions = human.loc[human["intent"] == "question", ["message_id", "sender_id"]]
    answers = human.merge(
        questions.rename(columns={"message_id": "qid", "sender_id": "asker"}),
        left_on="reply_to_msg_id",
        right_on="qid",
    )
    answers = answers[answers["sender_id"] != answers["asker"]]
    feats["answers"] = answers.groupby("sender_id").size()

    # Conversation starters: non-reply messages that drew replies from 2+ other people.
    replies = human[human["reply_to_msg_id"].notna()]
    repliers = (
        replies.merge(
            human[["message_id", "sender_id"]].rename(
                columns={"message_id": "root_id", "sender_id": "root_sender"}
            ),
            left_on="reply_to_msg_id",
            right_on="root_id",
        )
        .query("sender_id != root_sender")
        .groupby("root_id")["sender_id"]
        .nunique()
    )
    roots = human[human["reply_to_msg_id"].isna()].set_index("message_id")
    started = roots.loc[roots.index.intersection(repliers[repliers >= 2].index)]
    feats["threads_started"] = started.groupby("sender_id").size()

    # Joins recorded by service messages count as the start of membership when earlier.
    feats["joined"] = feats.index.map(join_times(msgs).get)
    feats["member_since"] = feats[["first_seen", "joined"]].min(axis=1)

    if not centrality.empty:
        feats = feats.join(centrality.set_index("user_id")[["betweenness", "in_degree"]])
    for col in ("answers", "threads_started", "betweenness", "in_degree"):
        if col not in feats:
            feats[col] = 0
        feats[col] = feats[col].fillna(0)
    return feats


def classify_roles(
    feats: pd.DataFrame,
    period_end,
    newcomer_days: int = 30,
    lurker_fraction: float = 0.15,
    data_start=None,
    start_grace_days: int = 7,
) -> pd.Series:
    """Assign one role per member (Series indexed by user_id)."""
    if feats.empty:
        return pd.Series(dtype=object)

    active_median = feats["messages"].median()
    lurker_max = max(3, lurker_fraction * active_median)
    engaged = feats[feats["messages"] > lurker_max]
    ref = engaged if len(engaged) >= 3 else feats

    tone_ref = ref["tone_given"].median()
    answers_cut = ref["answers"].quantile(0.8)
    betweenness_cut = max(0.05, ref["betweenness"].quantile(0.9))
    starters_cut = ref["threads_started"].quantile(0.8)
    newcomer_since = pd.Timestamp(period_end) - timedelta(days=newcomer_days)
    # Without a join event, "first seen recently" only means new if it's clearly after the
    # start of the data: people who show up in the first days were probably already members
    # (otherwise everyone is a "newcomer" when the period is short).
    first_data = pd.Timestamp(data_start) if data_start is not None else feats["first_seen"].min()
    seen_late = first_data + timedelta(days=start_grace_days)

    def role(row) -> str:
        joined_recently = pd.notna(row["joined"]) and row["joined"] >= newcomer_since
        first_seen_recently = row["first_seen"] >= max(newcomer_since, seen_late)
        if joined_recently or first_seen_recently:
            return "newcomer"
        if row["messages"] <= lurker_max:
            return "lurker"
        if pd.notna(row["tone_given"]) and row["tone_given"] <= min(-0.05, tone_ref - 0.25):
            return "critic"
        if (
            row["answers"] >= max(1, answers_cut)
            and pd.notna(row["tone_given"])
            and row["tone_given"] >= tone_ref + 0.1
        ):
            return "helper"
        if row["betweenness"] >= betweenness_cut:
            return "connector"
        if row["threads_started"] >= max(1, starters_cut):
            return "starter"
        return "regular"

    return feats.apply(role, axis=1).rename("role")
