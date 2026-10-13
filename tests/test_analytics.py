from datetime import datetime

import pandas as pd
import pytest

from tg_analyst import synthetic
from tg_analyst.analytics import load, metrics
from tg_analyst.enrich.pipeline import enrich
from tg_analyst.enrich.sentiment import VaderBackend

START, END = datetime(2025, 1, 1), datetime(2027, 1, 1)


@pytest.fixture
def community(engine, session):
    c = synthetic.generate(days=45)
    synthetic.load(session, c)
    enrich(session, VaderBackend())
    msgs = load.messages(engine, c.group.group_id, START, END)
    edges = load.interactions(engine, c.group.group_id, START, END)
    return c, msgs, edges


def test_kpis(community):
    c, msgs, _ = community
    k = metrics.kpis(msgs)
    bots = {uid for uid, role in c.roles.items() if role == "bot"}
    humans = [m for m in c.messages if not m.is_service and m.sender_id not in bots]
    assert k["messages"] == len(humans)
    assert k["joins"] == sum(1 for m in c.messages if m.service_action == "join")
    assert 0 < k["avg_dau"] <= k["active_members"]
    assert -1 <= k["avg_tone"] <= 1 and 0 <= k["negative_share"] <= 1


def test_kpis_empty():
    empty = pd.DataFrame(columns=["is_service", "is_bot", "sender_id"])
    assert metrics.kpis(empty)["messages"] == 0


def test_daily_activity_is_continuous(community):
    _, msgs, _ = community
    daily = metrics.daily_activity(msgs)
    assert len(daily) == 45
    assert daily["messages"].sum() == metrics.kpis(msgs)["messages"]


def test_daily_activity_leaves_silent_days_without_tone():
    msgs = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-01 10:00", "2026-01-03 10:00"]),
            "sender_id": [1, 2],
            "sentiment_score": [0.5, -0.5],
            "is_service": False,
            "is_bot": False,
        }
    )
    daily = metrics.daily_activity(msgs)
    assert daily["messages"].tolist() == [1, 0, 1]
    assert pd.isna(daily["tone"].iloc[1])


def test_heatmap_shape_and_total(community):
    _, msgs, _ = community
    heat = metrics.activity_heatmap(msgs)
    assert heat.shape == (7, 24)
    assert heat.to_numpy().sum() == metrics.kpis(msgs)["messages"]


def test_member_stats_and_relationships_match_ground_truth(community):
    c, msgs, edges = community
    stats = metrics.member_stats(msgs, edges).set_index("user_id")
    role = stats.index.map(c.roles)
    assert (
        stats.loc[role == "helper", "tone_given"].mean()
        > stats.loc[role == "feuder", "tone_given"].mean()
    )

    names = dict(zip(msgs["sender_id"], msgs["name"], strict=False))
    pairs = metrics.top_relationships(edges, names, n=5)
    top = pairs.iloc[0]
    assert {top["member_a"], top["member_b"]} == {"feuda", "feudb"}
    assert top["tone"] < 0


def test_pseudonym_map_is_stable():
    m = metrics.pseudonym_map([30, 10, 20, 10])
    assert m == {10: "Member 1", 20: "Member 2", 30: "Member 3"}
