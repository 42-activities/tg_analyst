"""Keyword topics: what the group talks about, the tone of each topic, and who drives it.

A topic is a stemmed keyword (see ``enrich.keywords``). Everything works on the
``message_terms`` frame: one row per (message, keyword). Pure functions, no I/O.
"""

import pandas as pd

from tg_analyst.analytics.metrics import human_messages
from tg_analyst.enrich.keywords import WORD, keywords, stem

TERM_COLUMNS = ["message_id", "sender_id", "timestamp", "sentiment_score", "sentiment_label"]


def message_terms(msgs: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct keyword of each human message: TERM_COLUMNS + stem, word."""
    human = human_messages(msgs)
    human = human[human["text"].map(lambda t: isinstance(t, str) and bool(t)).astype(bool)]
    rows = [
        (*meta, key, word)
        for meta, text in zip(
            human[TERM_COLUMNS].itertuples(index=False, name=None), human["text"], strict=True
        )
        for key, word in keywords(text).items()
    ]
    return pd.DataFrame(rows, columns=[*TERM_COLUMNS, "stem", "word"])


def without_names(terms: pd.DataFrame, names) -> pd.DataFrame:
    """Drop keywords that are (parts of) member names, for pseudonymized views."""
    stems = {stem(w) for name in names if isinstance(name, str) for w in WORD.findall(name.lower())}
    return terms[~terms["stem"].isin(stems)]


def topic_labels(terms: pd.DataFrame) -> pd.Series:
    """The most common surface form of each stem, e.g. "виз" -> "визу" or "виза"."""
    if terms.empty:
        return pd.Series(dtype=object)
    counts = terms.groupby(["stem", "word"]).size().reset_index(name="n")
    best = counts.sort_values(["n", "word"], ascending=[False, True]).drop_duplicates("stem")
    return best.set_index("stem")["word"]


def top_topics(terms: pd.DataFrame, n: int = 40, min_members: int = 3) -> pd.DataFrame:
    """The most discussed topics with their tone.

    ``share`` is the fraction of messages (with any keyword) that mention the topic;
    ``tone_vs_group`` is the topic's mean tone minus the group's, so tense topics stand
    out even in a chat whose baseline tone is rough. Topics raised by fewer than
    ``min_members`` people are dropped (one person's catchphrase is not a topic).
    """
    cols = [
        "stem",
        "topic",
        "messages",
        "members",
        "share",
        "tone",
        "tone_vs_group",
        "negative_share",
        "last_seen",
    ]
    if terms.empty:
        return pd.DataFrame(columns=cols)
    documents = terms["message_id"].nunique()
    baseline = terms.drop_duplicates("message_id")["sentiment_score"].mean()
    out = (
        terms.groupby("stem")
        .agg(
            messages=("message_id", "nunique"),
            members=("sender_id", "nunique"),
            tone=("sentiment_score", "mean"),
            negative_share=("sentiment_label", lambda s: (s == "negative").mean()),
            last_seen=("timestamp", "max"),
        )
        .reset_index()
    )
    out = out[out["members"] >= min_members]
    out["share"] = out["messages"] / documents
    out["tone_vs_group"] = out["tone"] - baseline
    out["topic"] = out["stem"].map(topic_labels(terms))
    return out.sort_values(["messages", "stem"], ascending=[False, True]).head(n)[cols]


def trending_topics(
    terms: pd.DataFrame,
    recent_days: int = 30,
    min_recent: int = 5,
    min_lift: float = 1.5,
    n: int = 15,
) -> pd.DataFrame:
    """Topics discussed more in the last ``recent_days`` than before.

    ``lift`` compares the topic's share of messages in the recent window with its share
    in the earlier period (smoothed, so a brand-new topic gets a large but finite lift).
    Ranked by ``extra``: recent messages beyond what the earlier share predicts, so a busy
    topic that doubled beats a word that appeared five times for the first time.
    """
    cols = ["stem", "topic", "recent", "before", "lift", "extra"]
    if terms.empty:
        return pd.DataFrame(columns=cols)
    cutoff = terms["timestamp"].max() - pd.Timedelta(days=recent_days)
    is_recent = terms["timestamp"] > cutoff
    recent_docs = terms.loc[is_recent, "message_id"].nunique()
    before_docs = terms.loc[~is_recent, "message_id"].nunique()
    if not recent_docs or not before_docs:
        return pd.DataFrame(columns=cols)
    counts = (
        terms.assign(window=is_recent.map({True: "recent", False: "before"}))
        .groupby(["stem", "window"])["message_id"]
        .nunique()
        .unstack(fill_value=0)
        .reindex(columns=["recent", "before"], fill_value=0)
        .reset_index()
    )
    counts = counts[counts["recent"] >= min_recent]
    counts["lift"] = ((counts["recent"] + 1) / (recent_docs + 1)) / (
        (counts["before"] + 1) / (before_docs + 1)
    )
    counts["extra"] = counts["recent"] - counts["before"] * recent_docs / before_docs
    counts = counts[(counts["lift"] >= min_lift) & (counts["extra"] > 0)]
    counts["topic"] = counts["stem"].map(topic_labels(terms))
    return counts.sort_values(["extra", "stem"], ascending=[False, True]).head(n)[cols]


def topic_messages(terms: pd.DataFrame, stem: str) -> pd.Index:
    """Message ids that mention a topic."""
    return pd.Index(terms.loc[terms["stem"] == stem, "message_id"].unique())


def topic_timeline(terms: pd.DataFrame, stem: str, freq: str = "W") -> pd.DataFrame:
    """Messages about a topic and their mean tone per period."""
    hits = terms[terms["stem"] == stem]
    if hits.empty:
        return pd.DataFrame(columns=["period", "messages", "tone"])
    hits = hits.assign(period=hits["timestamp"].dt.to_period(freq).dt.start_time)
    return (
        hits.groupby("period")
        .agg(messages=("message_id", "nunique"), tone=("sentiment_score", "mean"))
        .reset_index()
    )


def topic_members(terms: pd.DataFrame, stem: str, n: int = 10) -> pd.DataFrame:
    """Who talks about a topic most, and in what tone."""
    hits = terms[terms["stem"] == stem]
    if hits.empty:
        return pd.DataFrame(columns=["sender_id", "messages", "tone"])
    return (
        hits.groupby("sender_id")
        .agg(messages=("message_id", "nunique"), tone=("sentiment_score", "mean"))
        .reset_index()
        .sort_values(["messages", "sender_id"], ascending=[False, True])
        .head(n)
    )


def member_topics(
    terms: pd.DataFrame, user_id: int, n: int = 10, min_messages: int = 3
) -> pd.DataFrame:
    """Topics a member raises more often than the group does (``lift`` > 1)."""
    cols = ["stem", "topic", "messages", "lift", "tone"]
    own = terms[terms["sender_id"] == user_id]
    if own.empty:
        return pd.DataFrame(columns=cols)
    own_docs = own["message_id"].nunique()
    all_docs = terms["message_id"].nunique()
    mine = own.groupby("stem").agg(
        messages=("message_id", "nunique"), tone=("sentiment_score", "mean")
    )
    mine = mine[mine["messages"] >= min_messages]
    group = terms[terms["stem"].isin(mine.index)].groupby("stem")["message_id"].nunique()
    mine["lift"] = (mine["messages"] / own_docs) / (group / all_docs)
    mine = mine[mine["lift"] > 1].reset_index()
    mine["topic"] = mine["stem"].map(topic_labels(terms))
    return mine.sort_values(["messages", "stem"], ascending=[False, True]).head(n)[cols]
