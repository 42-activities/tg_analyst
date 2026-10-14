"""Dashboard page: community activity, mood, members and relationships."""

import math
from datetime import date, datetime, timedelta

import networkx as nx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from tg_analyst.analytics import conflict, metrics
from tg_analyst.dashboard.common import (
    TONE_SCALE,
    cached_date_range,
    cached_edges,
    cached_groups,
    cached_messages,
    cached_relationships,
    escape_md,
    fmt,
    fmt_duration,
    show_text,
    tone_style,
)
from tg_analyst.graph import network
from tg_analyst.graph.roles import ROLE_DESCRIPTIONS, ROLE_ORDER


def dashboard_page() -> None:
    groups = cached_groups()
    if groups.empty:
        st.title("Community dashboard")
        st.info("No data yet. Queue a back-fill or load demo data on the **Jobs** page.")
        return

    with st.sidebar:
        labels = {r.group_id: r.title or r.username or str(r.group_id) for r in groups.itertuples()}
        group_id = st.selectbox("Group", list(labels), format_func=labels.get)
        first, last = cached_date_range(group_id)
        default = (
            (first.date(), last.date())
            if first is not None
            else (date.today() - timedelta(days=365), date.today())
        )
        period = st.date_input("Period", value=default, help="Defaults to all data for the group")
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

    rel = cached_relationships(group_id, start, end)
    tabs = st.tabs(
        ["Activity & mood", "Members", "Network", "Relationships", "Newcomers", "Messages"]
    )
    with tabs[0]:
        _activity_tab(msgs)
    with tabs[1]:
        _members_tab(msgs, edges, names, rel)
    with tabs[2]:
        _network_tab(msgs, names, rel)
    with tabs[3]:
        _relationships_tab(msgs, edges, names, rel, hide_names)
    with tabs[4]:
        _newcomers_tab(names, rel)
    with tabs[5]:
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
    cols = st.columns(6)
    cols[0].metric("Avg weekly active", fmt(k["avg_wau"], "{:.1f}"))
    cols[1].metric(
        "Reply wait",
        fmt_duration(k["median_first_reply_seconds"]),
        help="Median time until someone else replies to a message (answered messages only)",
    )
    cols[2].metric(
        "Replies", fmt(k["reply_share"], "{:.0%}"), help="Share of messages that are replies"
    )
    cols[3].metric(
        "Media",
        fmt(k["media_share"], "{:.0%}"),
        help="Share of messages with photos, video, stickers…",
    )


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


def _members_tab(msgs: pd.DataFrame, edges: pd.DataFrame, names: dict, rel: dict) -> None:
    stats = metrics.member_stats(msgs, edges)
    if stats.empty:
        st.info("No member activity.")
        return
    stats["name"] = stats["user_id"].map(names)
    stats["role"] = stats["user_id"].map(rel["roles"]).fillna("regular")
    st.page_link("views/member.py", label="Open a member's full profile", icon=":material/person:")
    counts = stats["role"].value_counts()
    c1, c2 = st.columns([2, 3])
    chosen = c1.multiselect(
        "Show roles", ROLE_ORDER, format_func=lambda r: f"{r} ({counts.get(r, 0)})"
    )
    with c2.expander("What do the roles mean?"):
        st.markdown("\n".join(f"- **{r}**: {ROLE_DESCRIPTIONS[r]}" for r in ROLE_ORDER))
    if chosen:
        stats = stats[stats["role"].isin(chosen)]
    st.caption(
        "**Tone given**: average tone of a member's own messages. "
        "**Tone received**: average tone of replies and mentions directed at them."
    )
    st.dataframe(
        tone_style(
            stats[
                [
                    "name",
                    "role",
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
            "role": "Role",
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
            color="role",
            text="name",
            title="How members speak vs. how others speak to them (top 30 by activity)",
            labels={"tone_given": "Tone given", "tone_received": "Tone received"},
        )
        fig.add_hline(y=0, line_dash="dot").add_vline(x=0, line_dash="dot")
        fig.update_traces(textposition="top center").update_layout(height=480)
        st.plotly_chart(fig, use_container_width=True)


def _relationships_tab(
    msgs: pd.DataFrame, edges: pd.DataFrame, names: dict, rel: dict, hide_names: bool = False
) -> None:
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

    st.subheader("Conflict episodes")
    episodes = rel["conflicts"]
    if episodes.empty:
        st.success("No bursts of hostile exchanges between the same two members.")
    else:
        st.caption(
            "Three or more hostile replies/mentions between the same two members, at most a week "
            f"apart, where hostility dominated their exchange. Hostile = tone ≤ "
            f"{rel['hostility_threshold']:+.2f} (this group's most negative 10%). **Mutual** = "
            "both sides hostile (a feud); otherwise one-sided. **Click a row to read the "
            "messages.**"
        )
        view = episodes.assign(
            member_a=episodes["user_a"].map(names), member_b=episodes["user_b"].map(names)
        )
        picked = st.dataframe(
            view[
                [
                    "member_a",
                    "member_b",
                    "start",
                    "end",
                    "negative_interactions",
                    "mutual",
                    "mean_tone",
                ]
            ],
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            key="conflict_episodes",
            column_config={
                "member_a": "Member A",
                "member_b": "Member B",
                "start": "From",
                "end": "To",
                "negative_interactions": "Hostile messages",
                "mutual": "Mutual",
                "mean_tone": st.column_config.NumberColumn("Tone", format="%+.2f"),
            },
        )
        rows = picked.selection.rows if picked is not None else []
        episode = episodes.iloc[rows[0] if rows else 0]
        _episode_transcript(msgs, edges, names, episode, rel["hostility_threshold"], hide_names)

    st.subheader("Tone between two members over time")
    timeline = rel["pair_timeline"]
    pair_totals = timeline.groupby(["user_a", "user_b"])["interactions"].sum().nlargest(50)
    options = list(pair_totals.index)
    if not episodes.empty:
        flagged = list(dict.fromkeys(zip(episodes["user_a"], episodes["user_b"], strict=False)))
        options = flagged + [p for p in options if p not in flagged]
    if options:
        pair = st.selectbox(
            "Pair",
            options,
            format_func=lambda p: f"{names.get(p[0], p[0])} ↔ {names.get(p[1], p[1])}",
        )
        series = timeline[(timeline["user_a"] == pair[0]) & (timeline["user_b"] == pair[1])]
        fig = go.Figure()
        fig.add_bar(
            x=series["period"],
            y=series["interactions"],
            name="Interactions",
            marker_color="#9db4d3",
        )
        fig.add_scatter(
            x=series["period"],
            y=series["tone"],
            name="Tone",
            yaxis="y2",
            mode="lines+markers",
            line=dict(color="#c0392b"),
        )
        fig.update_layout(
            height=320,
            hovermode="x unified",
            margin=dict(t=20, b=20),
            yaxis=dict(title="Interactions per week"),
            yaxis2=dict(title="Tone", overlaying="y", side="right", range=[-1, 1], zeroline=True),
            legend=dict(orientation="h", y=1.12),
        )
        st.plotly_chart(fig, use_container_width=True)


def _episode_transcript(
    msgs, edges, names: dict, episode, threshold: float, hide_names: bool = False
) -> None:
    def show(text: str) -> str:
        # Pseudonymization also masks @usernames inside message text.
        return show_text(text, hide_names)

    a, b = int(episode["user_a"]), int(episode["user_b"])
    chain = conflict.episode_messages(
        msgs, edges, a, b, episode["start"], episode["end"], negative_below=threshold
    )
    name_a, name_b = names.get(a, str(a)), names.get(b, str(b))
    st.markdown(
        f"**{escape_md(name_a)} ↔ {escape_md(name_b)}**, "
        f"{episode['start']:%Y-%m-%d %H:%M} – {episode['end']:%Y-%m-%d %H:%M}: "
        f"{len(chain[chain['kind'] == 'exchange'])} messages between them, "
        f"{int(chain['hostile'].sum())} hostile"
    )
    if chain.empty:
        st.info("The messages for this episode are outside the selected period.")
        return
    avatars = {a: "🟦", b: "🟧"}
    by_id = chain.set_index("message_id")
    with st.container(height=520):
        for row in chain.itertuples():
            who = names.get(row.sender_id, str(row.sender_id))
            with st.chat_message(name=who, avatar=avatars.get(row.sender_id, "💬")):
                meta = [f"**{escape_md(who)}**", f"{row.timestamp:%Y-%m-%d %H:%M}"]
                if pd.notna(row.sentiment_score):
                    meta.append(f"tone {row.sentiment_score:+.2f}")
                if row.hostile:
                    meta.append("🔴 hostile")
                if row.kind == "context":
                    meta.append("_context: the message being replied to_")
                st.caption(" · ".join(meta))
                if pd.notna(row.reply_to_msg_id) and int(row.reply_to_msg_id) in by_id.index:
                    parent = by_id.loc[int(row.reply_to_msg_id)]
                    snippet = (parent["text"] or "")[:80]
                    st.caption(
                        f"↪ replying to {escape_md(names.get(parent['sender_id'], ''))}: "
                        f"{show(snippet)}"
                    )
                st.markdown(show(row.text) if row.text else "_(no text: media or sticker)_")


def _network_tab(msgs: pd.DataFrame, names: dict, rel: dict) -> None:
    graph = rel["graph"]
    if graph.number_of_nodes() == 0:
        st.info("No replies or mentions between members in this period.")
        return
    limit = st.slider("Members shown (most connected first)", 10, 200, 40, step=10)
    sub = _network_figure(graph, rel["communities"], rel["roles"], names, limit)
    st.plotly_chart(sub, use_container_width=True)
    st.caption(
        "Each dot is a member, sized by interactions received and coloured by community "
        "(groups who mostly talk among themselves). Lines show who talks to whom: thicker = "
        "more often, red = hostile tone, green = friendly."
    )

    cen = rel["centrality"].copy()
    cen["name"] = cen["user_id"].map(names)
    cen["community"] = cen["user_id"].map(rel["communities"])
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Connectors**: who links otherwise separate groups")
        st.dataframe(
            cen.nlargest(10, "betweenness")[["name", "betweenness", "in_degree", "community"]],
            hide_index=True,
            use_container_width=True,
            column_config={
                "name": "Member",
                "betweenness": st.column_config.ProgressColumn(
                    "Bridging",
                    min_value=0.0,
                    max_value=float(cen["betweenness"].max() or 1),
                    format="%.3f",
                ),
                "in_degree": "Addressed by (people)",
                "community": "Community",
            },
        )
    with c2:
        active = metrics.human_messages(msgs)["sender_id"].dropna().astype(int).unique()
        isolated = network.isolated_members(active, graph)
        st.markdown(f"**Unanswered members**: {len(isolated)} of {len(active)} who wrote")
        st.caption("Members whom nobody ever replied to or mentioned in this period.")
        st.dataframe(
            pd.DataFrame({"Member": [names.get(u, str(u)) for u in isolated]}).head(200),
            hide_index=True,
            use_container_width=True,
            height=300,
        )


def _network_figure(graph, communities: dict, roles: pd.Series, names: dict, limit: int):
    top = sorted(graph.nodes, key=lambda n: graph.degree(n, weight="weight"), reverse=True)
    sub = network.undirected(graph.subgraph(top[:limit]))
    # Layout on log-damped weights so a few heavy pairs don't collapse everyone into the core.
    for _, _, d in sub.edges(data=True):
        d["layout_weight"] = 1 + math.log(d["weight"])
    pos = nx.spring_layout(
        sub, weight="layout_weight", seed=1, k=2.5 / max(1, len(sub)) ** 0.5, iterations=200
    )
    labelled = set(top[:15])
    palette = px.colors.qualitative.D3
    fig = go.Figure()
    max_w = max((d["weight"] for *_, d in sub.edges(data=True)), default=1)
    for a, b, d in sub.edges(data=True):
        tones = [graph[u][v]["tone"] for u, v in ((a, b), (b, a)) if graph.has_edge(u, v)]
        tones = [t for t in tones if t == t]  # drop NaN
        tone = sum(tones) / len(tones) if tones else 0.0
        color = "#c0392b" if tone < -0.15 else "#27ae60" if tone > 0.25 else "#b0b0b0"
        fig.add_scatter(
            x=[pos[a][0], pos[b][0]],
            y=[pos[a][1], pos[b][1]],
            mode="lines",
            line=dict(width=0.3 + 4 * d["weight"] / max_w, color=color),
            hoverinfo="skip",
            showlegend=False,
            opacity=0.45,
        )
    nodes = list(sub.nodes)
    received = [graph.in_degree(n, weight="weight") for n in nodes]
    biggest = max(received) or 1
    fig.add_scatter(
        x=[pos[n][0] for n in nodes],
        y=[pos[n][1] for n in nodes],
        mode="markers+text",
        text=[names.get(n, str(n)) if n in labelled else "" for n in nodes],
        textposition="top center",
        textfont=dict(size=9),
        marker=dict(
            size=[8 + 30 * r / biggest for r in received],
            color=[palette[communities.get(n, 0) % len(palette)] for n in nodes],
            line=dict(width=1, color="white"),
        ),
        hovertext=[
            f"{names.get(n, n)}<br>role: {roles.get(n, '–')}<br>community: "
            f"{communities.get(n, '–')}<br>interactions received: {r}"
            for n, r in zip(nodes, received, strict=False)
        ],
        hoverinfo="text",
        showlegend=False,
    )
    fig.update_layout(
        height=620,
        margin=dict(t=10, b=10, l=10, r=10),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _newcomers_tab(names: dict, rel: dict) -> None:
    out, s = rel["newcomers"], rel["newcomer_summary"]
    if out.empty:
        st.info(
            "No join events in this period. Large groups often hide join messages from "
            "non-admins, in which case newcomers can't be tracked."
        )
        return
    cols = st.columns(5)
    cols[0].metric("Joined", s["joined"])
    cols[1].metric("Wrote something", s["wrote"])
    cols[2].metric(
        "First message answered in 24 h",
        fmt(s["welcome_rate"], "{:.0%}"),
        help="Someone replied to the newcomer's first message within 24 hours",
    )
    cols[3].metric("Typical wait for that answer", fmt_duration(s["median_first_reply_seconds"]))
    cols[4].metric("Still active after 14 days", fmt(s["retention"], "{:.0%}"))
    if s["retention_welcomed"] is not None and s["retention_not_welcomed"] is not None:
        st.plotly_chart(
            px.bar(
                x=["First message answered in 24 h", "Not answered in time"],
                y=[s["retention_welcomed"], s["retention_not_welcomed"]],
                text=[f"{s['retention_welcomed']:.0%}", f"{s['retention_not_welcomed']:.0%}"],
                labels={"x": "", "y": "Still active after 14 days"},
                title="Does a quick answer keep newcomers around?",
                color_discrete_sequence=["#5b8bd0"],
            )
            .update_traces(textposition="outside")
            .update_layout(height=300, yaxis_tickformat=".0%", yaxis_range=[0, 1.1]),
            use_container_width=True,
        )
    view = out.assign(member=out["user_id"].map(names))
    view["wait"] = view["first_reply_seconds"].map(fmt_duration)
    st.dataframe(
        view[
            [
                "member",
                "joined",
                "first_message",
                "wait",
                "first_reply_tone",
                "messages",
                "retained",
            ]
        ].sort_values("joined", ascending=False),
        hide_index=True,
        use_container_width=True,
        column_config={
            "member": "Member",
            "joined": "Joined",
            "first_message": "First message",
            "wait": "First message answered after",
            "first_reply_tone": st.column_config.NumberColumn(
                "Tone of that answer", format="%+.2f"
            ),
            "messages": "Messages",
            "retained": "Still active after 14 days",
        },
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
