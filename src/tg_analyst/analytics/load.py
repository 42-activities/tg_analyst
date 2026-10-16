"""Load analysis-ready DataFrames from the database (the only I/O in analytics)."""

from datetime import datetime

import pandas as pd
from sqlalchemy import Engine, and_, func, select

from tg_analyst.enrich.reactions import emoji_tone
from tg_analyst.storage.models import (
    ContextTopic,
    Enrichment,
    Group,
    Interaction,
    Message,
    Reaction,
    ThreadEmbedding,
    User,
)


def groups(engine: Engine) -> pd.DataFrame:
    return pd.read_sql(select(Group.group_id, Group.title, Group.username), engine)


def messages(engine: Engine, group_id: int, start: datetime, end: datetime) -> pd.DataFrame:
    """Messages in [start, end) joined with enrichment and sender info."""
    stmt = (
        select(
            Message.message_id,
            Message.sender_id,
            Message.timestamp,
            Message.text,
            Message.reply_to_msg_id,
            Message.thread_id,
            Message.reaction_count,
            Message.has_media,
            Message.media_type,
            Message.is_service,
            Message.service_action,
            Message.action_user_ids,
            Enrichment.sentiment_score,
            Enrichment.sentiment_label,
            Enrichment.intent,
            Enrichment.insult,
            Enrichment.obscenity,
            Enrichment.threat,
            User.username,
            User.display_name,
            User.is_bot,
        )
        .outerjoin(
            Enrichment,
            and_(
                Enrichment.group_id == Message.group_id, Enrichment.message_id == Message.message_id
            ),
        )
        .outerjoin(User, User.user_id == Message.sender_id)
        .where(Message.group_id == group_id, Message.timestamp >= start, Message.timestamp < end)
        .order_by(Message.message_id)
    )
    df = pd.read_sql(stmt, engine, parse_dates=["timestamp"])
    df["is_bot"] = df["is_bot"].fillna(False).astype(bool)
    # All-NULL columns (a group not scored yet) would otherwise load as dtype object.
    for column in ("sentiment_score", "insult", "obscenity", "threat", "reaction_count"):
        df[column] = df[column].astype(float)
    df["name"] = df["username"].fillna(df["display_name"]).fillna(df["sender_id"].astype(str))
    return df


def interactions(engine: Engine, group_id: int, start: datetime, end: datetime) -> pd.DataFrame:
    """Interaction edges with their tone.

    Replies and mentions carry the tone and toxicity of the message that created them. A
    reaction edge's ``message_id`` is the message reacted to, written by the *target*, so
    its tone comes from the emoji instead (the mean if one member left several reactions)
    and it has no toxicity.
    """
    stmt = (
        select(
            Interaction.source_user_id,
            Interaction.target_user_id,
            Interaction.type,
            Interaction.timestamp,
            Interaction.message_id,
            Enrichment.sentiment_score,
            Enrichment.insult,
            Enrichment.obscenity,
            Enrichment.threat,
        )
        .outerjoin(
            Enrichment,
            and_(
                Enrichment.group_id == Interaction.group_id,
                Enrichment.message_id == Interaction.message_id,
            ),
        )
        .where(
            Interaction.group_id == group_id,
            Interaction.timestamp >= start,
            Interaction.timestamp < end,
        )
    )
    edges = pd.read_sql(stmt, engine, parse_dates=["timestamp"])
    if not (edges["type"] == "reaction").any():
        return edges
    emojis = pd.read_sql(
        select(Reaction.message_id, Reaction.user_id.label("source_user_id"), Reaction.emoji)
        .join(
            Interaction,
            and_(
                Interaction.group_id == Reaction.group_id,
                Interaction.message_id == Reaction.message_id,
                Interaction.source_user_id == Reaction.user_id,
                Interaction.type == "reaction",
            ),
        )
        .where(
            Reaction.group_id == group_id,
            Interaction.timestamp >= start,
            Interaction.timestamp < end,
        ),
        engine,
    )
    tone = (
        emojis.assign(tone=emojis["emoji"].map(emoji_tone))
        .groupby(["message_id", "source_user_id"])["tone"]
        .mean()
    )
    is_reaction = edges["type"] == "reaction"
    # The reacted-to message's toxicity belongs to its author, not to the reactor.
    edges.loc[is_reaction, ["insult", "obscenity", "threat"]] = float("nan")
    keys = pd.MultiIndex.from_frame(edges.loc[is_reaction, ["message_id", "source_user_id"]])
    edges.loc[is_reaction, "sentiment_score"] = tone.reindex(keys).to_numpy()
    return edges


def reactions(engine: Engine, group_id: int, start: datetime, end: datetime) -> pd.DataFrame:
    """Reactions to messages sent in [start, end): who reacted to whose message, with what."""
    stmt = (
        select(
            Reaction.message_id,
            Reaction.user_id,
            Reaction.emoji,
            Reaction.timestamp,
            Message.sender_id.label("author_id"),
        )
        .join(
            Message,
            and_(Message.group_id == Reaction.group_id, Message.message_id == Reaction.message_id),
        )
        .where(Reaction.group_id == group_id, Message.timestamp >= start, Message.timestamp < end)
    )
    return pd.read_sql(stmt, engine, parse_dates=["timestamp"])


def date_range(engine: Engine, group_id: int) -> tuple[datetime | None, datetime | None]:
    """First and last message timestamp of a group (None, None if it has no messages)."""
    stmt = select(func.min(Message.timestamp), func.max(Message.timestamp)).where(
        Message.group_id == group_id
    )
    with engine.connect() as conn:
        first, last = conn.execute(stmt).one()
    return first, last


def context_topics(engine: Engine, group_id: int) -> pd.DataFrame:
    """Stored context topics of a group (``topics`` job), most messages first."""
    stmt = (
        select(
            ContextTopic.topic_id,
            ContextTopic.label,
            ContextTopic.title,
            ContextTopic.summary,
            ContextTopic.centre_root,
            ContextTopic.edge_roots,
            ContextTopic.thread_roots,
            ContextTopic.created_at,
        )
        .where(ContextTopic.group_id == group_id)
        .order_by(ContextTopic.topic_id)
    )
    return pd.read_sql(stmt, engine)


def thread_membership(engine: Engine, group_id: int) -> pd.DataFrame:
    """``message_id -> root_id`` of the group's embedded threads."""
    stmt = select(ThreadEmbedding.root_message_id, ThreadEmbedding.message_ids).where(
        ThreadEmbedding.group_id == group_id
    )
    rows = pd.read_sql(stmt, engine)
    if rows.empty:
        return pd.DataFrame({"message_id": [], "root_id": []}, dtype="int64")
    out = rows.explode("message_ids").rename(
        columns={"message_ids": "message_id", "root_message_id": "root_id"}
    )
    return out[["message_id", "root_id"]].astype("int64").reset_index(drop=True)
