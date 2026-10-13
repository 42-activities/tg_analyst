"""Shared cached resources and helpers for the Streamlit pages."""

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
