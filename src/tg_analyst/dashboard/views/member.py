"""Member page: one person's activity, tone, role, relationships and conflicts."""

from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from tg_analyst.analytics import members, metrics, topics
from tg_analyst.dashboard.common import (
    cached_date_range,
    cached_edges,
    cached_groups,
    cached_messages,
    cached_relationships,
    cached_terms,
    fmt,
    reactions_toggle,
    show_text,
    tone_style,
)
from tg_analyst.graph.roles import ROLE_DESCRIPTIONS


def member_page() -> None:
    groups = cached_groups()
    if groups.empty:
        st.title("Member")
        st.info("No data yet. Queue a back-fill on the **Jobs** page.")
        return

    with st.sidebar:
        labels = {r.group_id: r.title or r.username or str(r.group_id) for r in groups.itertuples()}
        group_id = st.selectbox("Group", list(labels), format_func=labels.get)
        hide_names = st.toggle("Pseudonymize members", help="For screenshots and sharing")
        with_reactions = reactions_toggle()

    first, last = cached_date_range(group_id)
    if first is None:
        st.info("This group has no messages yet.")
        return
    start = datetime.combine(first.date(), datetime.min.time())
    end = datetime.combine(last.date() + timedelta(days=1), datetime.min.time())
    msgs = cached_messages(group_id, start, end)
    edges = cached_edges(group_id, start, end, with_reactions)
    rel = cached_relationships(group_id, start, end, with_reactions)

    names = dict(zip(msgs["sender_id"], msgs["name"], strict=False))
    if hide_names:
        names = metrics.pseudonym_map(names)
    human = metrics.human_messages(msgs)
    ranked = human["sender_id"].value_counts()
    if ranked.empty:
        st.info("No member activity.")
        return
    options = [int(u) for u in ranked.index]
    wanted = st.query_params.get("user")
    default = (
        options.index(int(wanted))
        if wanted and wanted.lstrip("-").isdigit() and int(wanted) in options
        else 0
    )
    user_id = st.selectbox(
        "Member",
        options,
        index=default,
        format_func=lambda u: f"{names.get(u, u)} · {ranked[u]} messages",
    )
    st.query_params["user"] = str(user_id)

    summary = members.member_summary(msgs, edges, user_id)
    role = rel["roles"].get(user_id, "regular")
    st.title(names.get(user_id, str(user_id)))
    st.caption(
        f"**{role}**: {ROLE_DESCRIPTIONS.get(role, '')} · community "
        f"{rel['communities'].get(user_id, '–')} · first message "
        f"{summary['first_seen']:%Y-%m-%d}, last {summary['last_seen']:%Y-%m-%d}"
    )

    cols = st.columns(6)
    cols[0].metric(
        "Messages",
        f"{summary['messages']:,}",
        help=f"{summary['share_of_group']:.1%} of the group's messages",
    )
    cols[1].metric("Active days", summary["active_days"])
    cols[2].metric(
        "Tone given",
        fmt(summary["tone_given"], "{:+.2f}"),
        help="Average tone of their own messages",
    )
    cols[3].metric(
        "Tone received",
        fmt(summary["tone_received"], "{:+.2f}"),
        help="Average tone of replies and mentions directed at them",
    )
    cols[4].metric("Talks to", summary["talks_to"], help="Distinct people they reply to or mention")
    cols[5].metric(
        "Messages answered",
        fmt(summary["messages_answered"], "{:.0%}"),
        help="Share of their messages that got a reply from someone else",
    )

    extra = []
    if summary["reactions_given"] or summary["reactions_received"]:
        extra.append(
            f"Reactions: gave {summary['reactions_given']:,}, "
            f"received {summary['reactions_received']:,}"
        )
    if summary["insults_sent"] or summary["insults_received"]:
        extra.append(
            f"Insults: sent {summary['insults_sent']:,}, received {summary['insults_received']:,}"
        )
    if extra:
        st.caption(" · ".join(extra))
    _activity(msgs, user_id)
    _partners(edges, user_id, names)
    terms = cached_terms(group_id, start, end)
    if hide_names:
        terms = topics.without_names(terms, [*msgs["username"], *msgs["display_name"]])
    _topics(terms, user_id)
    _conflicts(rel, user_id, names)
    _recent(human, user_id, hide_names)


def _activity(msgs: pd.DataFrame, user_id: int) -> None:
    weekly = members.member_activity(msgs, user_id)
    fig = go.Figure()
    fig.add_bar(
        x=weekly["period"], y=weekly["messages"], name="Messages per week", marker_color="#9db4d3"
    )
    fig.add_scatter(
        x=weekly["period"],
        y=weekly["tone"],
        name="Tone",
        yaxis="y2",
        mode="lines",
        line=dict(color="#c0392b"),
    )
    fig.update_layout(
        title="Activity and tone over time",
        height=320,
        hovermode="x unified",
        yaxis2=dict(overlaying="y", side="right", range=[-1, 1], title="Tone"),
        legend=dict(orientation="h", y=1.15),
        margin=dict(t=60, b=20),
    )
    c1, c2 = st.columns([3, 2])
    c1.plotly_chart(fig, use_container_width=True)
    own = msgs[msgs["sender_id"] == user_id]
    c2.plotly_chart(
        px.imshow(
            metrics.activity_heatmap(own),
            aspect="auto",
            color_continuous_scale="Blues",
            labels=dict(x="Hour (UTC)", y="", color="Messages"),
            title="When they write",
        ).update_layout(height=320),
        use_container_width=True,
    )


def _partners(edges: pd.DataFrame, user_id: int, names: dict) -> None:
    st.subheader("Who they talk to")
    partners = members.member_partners(edges, user_id)
    if partners.empty:
        st.info("They never replied to or mentioned anyone, and nobody addressed them.")
        return
    partners.insert(0, "partner", partners["partner_id"].map(lambda u: names.get(u, str(u))))
    st.dataframe(
        tone_style(partners.drop(columns="partner_id"), ["tone_to_them", "tone_from_them"]),
        hide_index=True,
        use_container_width=True,
        column_config={
            "partner": "With",
            "to_them": "Sent to them",
            "from_them": "Received from them",
            "tone_to_them": st.column_config.NumberColumn("Tone sent", format="%+.2f"),
            "tone_from_them": st.column_config.NumberColumn("Tone received", format="%+.2f"),
            "total": "Interactions",
        },
    )


def _topics(terms: pd.DataFrame, user_id: int) -> None:
    mine = topics.member_topics(terms, user_id)
    if mine.empty:
        return
    st.subheader("What they talk about")
    st.caption("Topics they raise more often than the group does.")
    st.dataframe(
        tone_style(mine.drop(columns="stem"), ["tone"]),
        hide_index=True,
        use_container_width=True,
        column_config={
            "topic": "Topic",
            "messages": "Their messages",
            "lift": st.column_config.NumberColumn(
                "Vs. group", format="%.1f×", help="How much more often than the group average"
            ),
            "tone": st.column_config.NumberColumn("Tone", format="%+.2f"),
        },
    )


def _conflicts(rel: dict, user_id: int, names: dict) -> None:
    episodes = rel["conflicts"]
    mine = episodes[(episodes["user_a"] == user_id) | (episodes["user_b"] == user_id)]
    if mine.empty:
        return
    st.subheader("Conflict episodes")
    st.caption("Read the messages in **Dashboard → Relationships → Conflict episodes**.")
    view = mine.assign(
        other=[
            names.get(b if a == user_id else a, "")
            for a, b in zip(mine["user_a"], mine["user_b"], strict=False)
        ]
    )
    st.dataframe(
        view[["other", "start", "end", "negative_interactions", "mutual", "mean_tone"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "other": "With",
            "start": "From",
            "end": "To",
            "negative_interactions": "Hostile messages",
            "mutual": "Mutual",
            "mean_tone": st.column_config.NumberColumn("Tone", format="%+.2f"),
        },
    )


def _recent(human: pd.DataFrame, user_id: int, hide_names: bool) -> None:
    st.subheader("Recent messages")
    own = human[human["sender_id"] == user_id].sort_values("timestamp", ascending=False).head(50)
    for row in own.itertuples():
        tone = f" · tone {row.sentiment_score:+.2f}" if pd.notna(row.sentiment_score) else ""
        st.caption(f"{row.timestamp:%Y-%m-%d %H:%M}{tone}")
        st.markdown(show_text(row.text, hide_names))


member_page()
