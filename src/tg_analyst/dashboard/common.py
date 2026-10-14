"""Shared cached resources and helpers for the Streamlit pages."""

import re
from datetime import datetime

import pandas as pd
import streamlit as st

from tg_analyst.analytics import load
from tg_analyst.config import get_settings
from tg_analyst.storage.db import init_db, make_engine, make_session_factory

TONE_SCALE = ["#c0392b", "#e8e8e8", "#27ae60"]  # negative -> neutral -> positive


@st.cache_resource
def engine():
    eng = make_engine(get_settings().database_url)
    init_db(eng)
    return eng


@st.cache_resource
def session_factory():
    return make_session_factory(engine())


@st.cache_data(ttl=60)
def cached_groups() -> pd.DataFrame:
    return load.groups(engine())


@st.cache_data(ttl=60)
def cached_messages(group_id: int, start: datetime, end: datetime) -> pd.DataFrame:
    return load.messages(engine(), group_id, start, end)


@st.cache_data(ttl=60)
def cached_edges(group_id: int, start: datetime, end: datetime) -> pd.DataFrame:
    return load.interactions(engine(), group_id, start, end)


def fmt_duration(seconds) -> str:
    """Human-friendly duration: 45 s, 12 min, 3.5 h, 2.0 d."""
    if seconds is None or pd.isna(seconds):
        return "–"
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def fmt(value, pattern="{:.2f}", none="–"):
    return none if value is None or pd.isna(value) else pattern.format(value)


def _tone_color(value) -> str:
    if value is None or pd.isna(value):
        return ""
    if value <= -0.1:
        return "color: #c0392b; font-weight: 600"
    if value >= 0.1:
        return "color: #1e8449"
    return ""


def tone_style(df: pd.DataFrame, columns: list[str]):
    """Colour tone values: red for hostile, green for friendly."""
    return df.style.map(_tone_color, subset=columns)


@st.cache_data(ttl=300, show_spinner="Extracting topics…")
def cached_terms(group_id: int, start: datetime, end: datetime) -> pd.DataFrame:
    from tg_analyst.analytics import topics

    return topics.message_terms(cached_messages(group_id, start, end))


@st.cache_data(ttl=300)
def cached_date_range(group_id: int):
    return load.date_range(engine(), group_id)


@st.cache_data(ttl=300, show_spinner="Analysing relationships…")
def cached_relationships(group_id: int, start: datetime, end: datetime) -> dict:
    """Graph metrics, roles, newcomer outcomes and conflicts for a group and period."""
    from tg_analyst.analytics import conflict, newcomers
    from tg_analyst.graph import network, roles

    msgs = cached_messages(group_id, start, end)
    edges = cached_edges(group_id, start, end)
    graph = network.build_graph(edges)
    centrality = network.centrality(graph)
    feats = roles.member_features(msgs, centrality)
    member_roles = (
        roles.classify_roles(feats, msgs["timestamp"].max())
        if not feats.empty
        else pd.Series(dtype=object)
    )
    outcomes = newcomers.newcomer_outcomes(msgs)
    return {
        "graph": graph,
        "centrality": centrality,
        "communities": network.communities(graph),
        "roles": member_roles,
        "newcomers": outcomes,
        "newcomer_summary": newcomers.newcomer_summary(outcomes),
        "conflicts": conflict.detect_conflicts(edges),
        "hostility_threshold": conflict.hostility_threshold(edges["sentiment_score"])
        if "sentiment_score" in edges
        else -0.3,
        "pair_timeline": conflict.pair_tone_timeline(edges),
    }


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<])")


def escape_md(text: str) -> str:
    """Show chat text literally: no Markdown formatting, links or images from user content."""
    return _MD_SPECIAL.sub(r"\\\1", text)


MENTION = re.compile(r"(?<!\w)@\w{2,}")


NO_TEXT = "_(no text: media or sticker)_"


def show_text(text, hide_names: bool = False, limit: int | None = None) -> str:
    """Chat text safe to render: escaped, with @usernames masked when pseudonymizing.

    Media-only messages have no text, which pandas stores as NaN (a truthy float), so
    anything that isn't a non-empty string renders as a placeholder.
    """
    if not isinstance(text, str) or not text:
        return NO_TEXT
    text = text[:limit] if limit else text
    return escape_md(MENTION.sub("@user", text) if hide_names else text)
