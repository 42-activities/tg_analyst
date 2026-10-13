"""Dashboard page: community activity, mood, members and relationships."""

from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from tg_analyst.analytics import metrics
from tg_analyst.dashboard.common import (
    TONE_SCALE,
    cached_edges,
    cached_groups,
    cached_messages,
    fmt,
    tone_style,
)


def dashboard_page() -> None:
    groups = cached_groups()
    if groups.empty:
        st.title("Community dashboard")
        st.info("No data yet. Queue a back-fill or load demo data on the **Jobs** page.")
        return

    with st.sidebar:
        labels = {r.group_id: r.title or r.username or str(r.group_id) for r in groups.itertuples()}
        group_id = st.selectbox("Group", list(labels), format_func=labels.get)
        today = date.today()
        period = st.date_input("Period", value=(today - timedelta(days=365), today))
        hide_names = st.toggle("Pseudonymize members", help="For screenshots and sharing")
        if st.button("Refresh data"):
            st.cache_data.clear()

    if not isinstance(period, tuple) or len(period) != 2:
        st.stop()
    start = datetime.combine(period[0], datetime.min.time())
    end = datetime.combine(period[1] + timedelta(days=1), datetime.min.time())
    msgs = cached_messages(group_id, start, end)
    edges = cached_edges(group_id, start, end)

    st.title(labels[group_id])
    if msgs.empty:
        st.info("No messages in this period.")
        return

    names = dict(zip(msgs["sender_id"], msgs["name"], strict=False))
    if hide_names:
        names = metrics.pseudonym_map(names)

    _kpi_row(msgs)
    if msgs["sentiment_score"].isna().all():
        st.warning("Messages are not scored yet. Run sentiment analysis on the **Jobs** page.")

    tab_activity, tab_members, tab_rel, tab_msgs = st.tabs(
        ["Activity & mood", "Members", "Relationships", "Messages"]
    )
    with tab_activity:
        _activity_tab(msgs)
    with tab_members:
        _members_tab(msgs, edges, names)
    with tab_rel:
        _relationships_tab(edges, names)
    with tab_msgs:
        _messages_tab(msgs, names)


def _kpi_row(msgs: pd.DataFrame) -> None:
    k = metrics.kpis(msgs)
    cols = st.columns(6)
    cols[0].metric("Messages", f"{k['messages']:,}")
    cols[1].metric("Active members", k["active_members"])
    cols[2].metric("Avg daily active", fmt(k["avg_dau"], "{:.1f}"))
    cols[3].metric("Avg tone", fmt(k["avg_tone"], "{:+.2f}"), help="-1 negative … +1 positive")
    cols[4].metric("Negative messages", fmt(k["negative_share"], "{:.0%}"))
    cols[5].metric("New members", k["joins"])


def _activity_tab(msgs: pd.DataFrame) -> None:
    daily = metrics.daily_activity(msgs)
    fig = go.Figure()
    fig.add_bar(x=daily["date"], y=daily["messages"], name="Messages", marker_color="#9db4d3")
    fig.add_scatter(
        x=daily["date"],
        y=daily["tone_rolling"],
        name="Tone (7-day avg)",
        yaxis="y2",
        line=dict(color="#c0392b", width=2),
    )
    fig.update_layout(
        title="Activity and tone over time",
        height=380,
        hovermode="x unified",
        yaxis=dict(title="Messages per day"),
        yaxis2=dict(
            title="Tone", overlaying="y", side="right", range=[-1, 1], dtick=0.5, zeroline=True
        ),
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=60, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns([3, 2])
    heat = metrics.activity_heatmap(msgs)
    c1.plotly_chart(
        px.imshow(
            heat,
            aspect="auto",
            color_continuous_scale="Blues",
            labels=dict(x="Hour (UTC)", y="", color="Messages"),
            title="When is the community active?",
        ).update_layout(height=320),
        use_container_width=True,
    )
    human = metrics.human_messages(msgs)
    by_intent = (
        human.assign(intent=human["intent"].fillna("other"))
        .groupby("intent")["sentiment_score"]
        .agg(["size", "mean"])
        .reset_index()
    )
    c2.plotly_chart(
        px.bar(
            by_intent,
            x="intent",
            y="size",
            color="mean",
            range_color=[-1, 1],
            color_continuous_scale=TONE_SCALE,
            title="Message types and their tone",
            labels={"size": "Messages", "mean": "Tone", "intent": ""},
        ).update_layout(height=320),
        use_container_width=True,
    )


def _members_tab(msgs: pd.DataFrame, edges: pd.DataFrame, names: dict) -> None:
    stats = metrics.member_stats(msgs, edges)
    if stats.empty:
        st.info("No member activity.")
        return
    stats["name"] = stats["user_id"].map(names)
    st.caption(
        "**Tone given**: average tone of a member's own messages. "
        "**Tone received**: average tone of replies and mentions directed at them."
    )
    st.dataframe(
        tone_style(
            stats[
                [
                    "name",
                    "messages",
                    "people_reached",
                    "interactions_received",
                    "tone_given",
                    "tone_received",
                    "questions",
                    "first_seen",
                    "last_seen",
                ]
            ],
            ["tone_given", "tone_received"],
        ),
        hide_index=True,
        use_container_width=True,
        column_config={
            "name": "Member",
            "messages": "Messages",
            "people_reached": "Talks to (people)",
            "interactions_received": "Replies/mentions received",
            "tone_given": st.column_config.NumberColumn("Tone given", format="%+.2f"),
            "tone_received": st.column_config.NumberColumn("Tone received", format="%+.2f"),
            "questions": "Questions asked",
            "first_seen": "First message",
            "last_seen": "Last message",
        },
    )
    top = stats.head(30).dropna(subset=["tone_given", "tone_received"])
    if not top.empty:
        fig = px.scatter(
            top,
            x="tone_given",
            y="tone_received",
            size="messages",
            text="name",
            title="How members speak vs. how others speak to them (top 30 by activity)",
            labels={"tone_given": "Tone given", "tone_received": "Tone received"},
        )
        fig.add_hline(y=0, line_dash="dot").add_vline(x=0, line_dash="dot")
        fig.update_traces(textposition="top center").update_layout(height=480)
        st.plotly_chart(fig, use_container_width=True)


def _relationships_tab(edges: pd.DataFrame, names: dict) -> None:
    if edges.empty:
        st.info("No replies or mentions between members in this period.")
        return
    pairs = metrics.top_relationships(edges, names, n=20)
    st.caption(
        "Strongest pairs by number of replies and mentions. Tone is shown in each direction: "
        "green is friendly, red is hostile."
    )
    tone_cols = ["tone_a_to_b", "tone_b_to_a", "tone"]
    st.dataframe(
        tone_style(pairs, tone_cols),
        hide_index=True,
        use_container_width=True,
        column_config={
            "member_a": "Member A",
            "member_b": "Member B",
            "interactions": "Interactions",
            "tone_a_to_b": st.column_config.NumberColumn("Tone A → B", format="%+.2f"),
            "tone_b_to_a": st.column_config.NumberColumn("Tone B → A", format="%+.2f"),
            "tone": st.column_config.NumberColumn("Overall tone", format="%+.2f"),
        },
    )
    tense = pairs[pairs["tone"] < -0.15]
    if not tense.empty:
        st.error(
            "Tense relationships: "
            + ", ".join(f"{r.member_a} ↔ {r.member_b} ({r.tone:+.2f})" for r in tense.itertuples())
        )


def _messages_tab(msgs: pd.DataFrame, names: dict) -> None:
    human = metrics.human_messages(msgs).copy()
    c1, c2, c3 = st.columns([2, 1, 1])
    query = c1.text_input("Search text")
    label = c2.multiselect("Tone", ["positive", "neutral", "negative"])
    intent = c3.multiselect("Type", ["question", "thanks", "greeting", "complaint"])
    if query:
        human = human[human["text"].fillna("").str.contains(query, case=False, regex=False)]
    if label:
        human = human[human["sentiment_label"].isin(label)]
    if intent:
        human = human[human["intent"].isin(intent)]
    human["member"] = human["sender_id"].map(names)
    st.caption(f"{len(human):,} messages")
    st.dataframe(
        human[["timestamp", "member", "text", "sentiment_score", "sentiment_label", "intent"]]
        .sort_values("timestamp", ascending=False)
        .head(1000),
        hide_index=True,
        use_container_width=True,
        column_config={"sentiment_score": st.column_config.NumberColumn("Tone", format="%+.2f")},
    )


dashboard_page()
