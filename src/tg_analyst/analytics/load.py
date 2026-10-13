"""Load analysis-ready DataFrames from the database (the only I/O in analytics)."""

from datetime import datetime

import pandas as pd
from sqlalchemy import Engine, and_, func, select

from tg_analyst.storage.models import Enrichment, Group, Interaction, Message, User


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
            Message.has_media,
            Message.media_type,
            Message.is_service,
            Message.service_action,
            Message.action_user_ids,
            Enrichment.sentiment_score,
            Enrichment.sentiment_label,
            Enrichment.intent,
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
    df["name"] = df["username"].fillna(df["display_name"]).fillna(df["sender_id"].astype(str))
    return df


def interactions(engine: Engine, group_id: int, start: datetime, end: datetime) -> pd.DataFrame:
    """Interaction edges with the tone of the message that created them."""
    stmt = (
        select(
            Interaction.source_user_id,
            Interaction.target_user_id,
            Interaction.type,
            Interaction.timestamp,
            Interaction.message_id,
            Enrichment.sentiment_score,
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
    return pd.read_sql(stmt, engine, parse_dates=["timestamp"])


def date_range(engine: Engine, group_id: int) -> tuple[datetime | None, datetime | None]:
    """First and last message timestamp of a group (None, None if it has no messages)."""
    stmt = select(func.min(Message.timestamp), func.max(Message.timestamp)).where(
        Message.group_id == group_id
    )
    with engine.connect() as conn:
        first, last = conn.execute(stmt).one()
    return first, last
