"""Milestone 3 analytics checked against the synthetic community's ground truth."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from tg_analyst import synthetic
from tg_analyst.analytics import conflict, load, metrics, newcomers
from tg_analyst.enrich.pipeline import enrich
from tg_analyst.enrich.sentiment import VaderBackend
from tg_analyst.graph import network, roles

START, END = datetime(2025, 1, 1), datetime(2027, 1, 1)


@pytest.fixture
def world(engine, session):
    c = synthetic.generate(days=90)
    synthetic.load(session, c)
    enrich(session, VaderBackend())
    msgs = load.messages(engine, c.group.group_id, START, END)
    edges = load.interactions(engine, c.group.group_id, START, END)
    by_name = {u.username: u.user_id for u in c.users}
    return c, msgs, edges, by_name


# --- graph ---------------------------------------------------------------------------------


def test_graph_aggregates_edges_and_tone():
    edges = pd.DataFrame(
        {
            "source_user_id": [1, 1, 2],
            "target_user_id": [2, 2, 1],
            "message_id": [10, 11, 12],
            "sentiment_score": [0.5, -0.5, 0.2],
            "type": "reply",
            "timestamp": pd.Timestamp("2026-01-01"),
        }
    )
    g = network.build_graph(edges, {1: "a", 2: "b"})
    assert g[1][2] == {"weight": 2, "tone": 0.0}
    assert g.nodes[1]["name"] == "a"
    assert network.undirected(g)[1][2]["weight"] == 3


def test_empty_graph_is_handled():
    empty = pd.DataFrame(
        columns=["source_user_id", "target_user_id", "message_id", "sentiment_score"]
    )
    g = network.build_graph(empty)
    assert network.centrality(g).empty and network.communities(g) == {}


def test_helpers_bridge_the_community(world):
    c, _, edges, _ = world
    cen = network.centrality(network.build_graph(edges)).set_index("user_id")
    top = cen["betweenness"].nlargest(4).index
    assert {c.roles[u] for u in top} == {"helper"}


def test_communities_cover_everyone_and_are_deterministic(world):
    _, _, edges, _ = world
    g = network.build_graph(edges)
    first, second = network.communities(g), network.communities(g)
    assert first == second and set(first) == set(g.nodes)
    sizes = pd.Series(first).value_counts()
    assert list(sizes.index) == sorted(sizes.index)  # cluster 0 is the largest


def test_isolated_members():
    edges = pd.DataFrame(
        {"source_user_id": [1], "target_user_id": [2], "message_id": [1], "sentiment_score": [0.0]}
    )
    assert network.isolated_members([1, 2, 3], network.build_graph(edges)) == [1, 3]


# --- roles ---------------------------------------------------------------------------------


def test_roles_match_ground_truth(world):
    c, msgs, edges, _ = world
    feats = roles.member_features(msgs, network.centrality(network.build_graph(edges)))
    assigned = roles.classify_roles(feats, msgs["timestamp"].max())
    truth = assigned.index.map(c.roles)

    def got(role):
        return set(assigned[truth == role])

    assert got("helper") == {"helper"}
    assert got("critic") == {"critic"} and got("feuder") == {"critic"}
    assert got("lurker") == {"lurker"}
    assert "critic" not in got("regular") and "helper" not in got("regular")
    # Recent joiners are newcomers; nobody who was there from day one is.
    since = msgs["timestamp"].max() - timedelta(days=30)
    recent = feats.index[feats["member_since"] >= since]
    assert set(assigned[recent]) == {"newcomer"}
    assert set(assigned[truth.isin(["admin", "helper", "regular"])]).isdisjoint({"newcomer"})


def test_every_role_has_a_description():
    assert set(roles.ROLE_ORDER) == set(roles.ROLE_DESCRIPTIONS)


# --- newcomers -----------------------------------------------------------------------------


def test_newcomer_outcomes(world):
    c, msgs, _, _ = world
    out = newcomers.newcomer_outcomes(msgs)
    assert len(out) == sum(r == "newcomer" for r in c.roles.values())
    assert (out["first_message"] >= out["joined"]).all()
    welcomed = out[out["welcomed"]]
    assert (welcomed["first_reply_seconds"] <= 24 * 3600).all()

    s = newcomers.newcomer_summary(out)
    assert s["joined"] == len(out) and 0 < s["welcome_rate"] < 1
    # The synthetic community makes ignored newcomers churn: welcome predicts retention.
    assert s["retention_welcomed"] > s["retention_not_welcomed"]


def test_retention_is_unknown_when_window_too_short():
    t0 = pd.Timestamp("2026-01-01")
    msgs = pd.DataFrame(
        {
            "message_id": [1, 2],
            "sender_id": [5, 5],
            "timestamp": [t0, t0 + timedelta(hours=1)],
            "text": [None, "hi"],
            "reply_to_msg_id": [None, None],
            "is_service": [True, False],
            "service_action": ["join", None],
            "action_user_ids": [[5], None],
            "is_bot": False,
            "sentiment_score": [None, 0.1],
            "intent": [None, "greeting"],
        }
    )
    out = newcomers.newcomer_outcomes(msgs)
    assert len(out) == 1 and pd.isna(out.loc[0, "retained"]) and not out.loc[0, "welcomed"]


def test_newcomers_empty():
    empty = pd.DataFrame(
        columns=[
            "service_action",
            "action_user_ids",
            "timestamp",
            "is_service",
            "is_bot",
            "sender_id",
        ]
    )
    assert newcomers.newcomer_outcomes(empty).empty
    assert newcomers.newcomer_summary(newcomers.newcomer_outcomes(empty))["joined"] == 0


# --- conflict ------------------------------------------------------------------------------


def test_feud_is_the_top_conflict(world):
    _, _, edges, by_name = world
    episodes = conflict.detect_conflicts(edges)
    top = episodes.iloc[0]
    assert {top["user_a"], top["user_b"]} == {by_name["feuda"], by_name["feudb"]}
    assert top["mutual"] and top["mean_tone"] < -0.3


def test_conflict_chaining_and_threshold():
    t0 = pd.Timestamp("2026-01-01")
    edges = pd.DataFrame(
        {
            "source_user_id": [1, 2, 1, 1, 1],
            "target_user_id": [2, 1, 2, 2, 2],
            "message_id": range(5),
            "sentiment_score": [-0.6, -0.7, -0.5, -0.8, 0.9],
            "timestamp": [
                t0,
                t0 + timedelta(days=1),
                t0 + timedelta(days=2),
                t0 + timedelta(days=30),
                t0 + timedelta(days=31),
            ],
        }
    )
    ep = conflict.detect_conflicts(edges, negative_below=-0.3, min_negative=3, gap_days=7)
    assert len(ep) == 1 and ep.loc[0, "negative_interactions"] == 3 and ep.loc[0, "mutual"]
    assert conflict.detect_conflicts(edges, negative_below=-0.3, min_negative=4).empty


def test_pair_tone_timeline(world):
    _, _, edges, by_name = world
    tl = conflict.pair_tone_timeline(edges)
    pair = sorted([by_name["feuda"], by_name["feudb"]])
    feud = tl[(tl["user_a"] == pair[0]) & (tl["user_b"] == pair[1])]
    assert len(feud) >= 10 and feud["tone"].mean() < 0
    assert tl["interactions"].sum() == len(edges)


# --- kpis ----------------------------------------------------------------------------------


def test_extended_kpis(world):
    _, msgs, _, _ = world
    k = metrics.kpis(msgs)
    assert 0 < k["avg_wau"] <= k["active_members"]
    assert 0 < k["media_share"] < 0.2
    assert k["median_first_reply_seconds"] > 0


def test_hostility_threshold_adapts_to_the_group():
    polite = pd.Series([0.5] * 95 + [-0.4] * 5)
    rough = pd.Series([-0.9] * 20 + [-0.5] * 30 + [0.2] * 50)
    assert conflict.hostility_threshold(polite) == -0.3  # never above the ceiling
    assert conflict.hostility_threshold(rough) == -0.9
    assert conflict.hostility_threshold(pd.Series(dtype=float)) == -0.3


def test_banter_with_mostly_friendly_exchange_is_not_a_conflict():
    t0 = pd.Timestamp("2026-01-01")
    n = 20  # 3 hostile messages spread across 20 exchanges over a few days
    tones = [0.4] * n
    for k in (0, 9, 19):
        tones[k] = -0.8
    edges = pd.DataFrame(
        {
            "source_user_id": [1, 2] * (n // 2),
            "target_user_id": [2, 1] * (n // 2),
            "message_id": range(n),
            "sentiment_score": tones,
            "timestamp": [t0 + timedelta(hours=4 * i) for i in range(n)],
        }
    )
    assert conflict.detect_conflicts(edges, negative_below=-0.3).empty
    assert len(conflict.detect_conflicts(edges, negative_below=-0.3, min_hostile_share=0)) == 1
