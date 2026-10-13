"""Interaction graph: who talks to whom, how often, and in what tone. Pure functions."""

import networkx as nx
import pandas as pd


def build_graph(edges: pd.DataFrame, names: dict[int, str] | None = None) -> nx.DiGraph:
    """Directed graph: edge u->v aggregates every reply/mention from u to v.

    Edge attributes: ``weight`` (interaction count) and ``tone`` (mean sentiment of the
    messages that created the edge; NaN if none were scored).
    """
    g = nx.DiGraph()
    if edges.empty:
        return g
    agg = edges.groupby(["source_user_id", "target_user_id"]).agg(
        weight=("message_id", "size"), tone=("sentiment_score", "mean")
    )
    for (src, dst), row in agg.iterrows():
        g.add_edge(int(src), int(dst), weight=int(row["weight"]), tone=float(row["tone"]))
    if names:
        nx.set_node_attributes(g, {n: names.get(n, str(n)) for n in g.nodes}, "name")
    return g


def undirected(g: nx.DiGraph) -> nx.Graph:
    """Collapse both directions of a pair into one edge (weights summed)."""
    u = nx.Graph()
    u.add_nodes_from(g.nodes(data=True))
    for a, b, d in g.edges(data=True):
        if u.has_edge(a, b):
            u[a][b]["weight"] += d["weight"]
        else:
            u.add_edge(a, b, weight=d["weight"])
    return u


def centrality(g: nx.DiGraph) -> pd.DataFrame:
    """Per-member structural metrics.

    - ``in_weight`` / ``out_weight``: interactions received / sent
    - ``in_degree`` / ``out_degree``: distinct people who address them / they address
    - ``betweenness``: how often a member sits on shortest paths between others, i.e.
      bridges otherwise separate parts of the community (stronger ties = shorter paths)
    """
    if g.number_of_nodes() == 0:
        return pd.DataFrame(
            columns=["user_id", "in_weight", "out_weight", "in_degree", "out_degree", "betweenness"]
        )
    u = undirected(g)
    for _, _, d in u.edges(data=True):
        d["distance"] = 1.0 / d["weight"]
    betweenness = nx.betweenness_centrality(u, weight="distance", normalized=True)
    return pd.DataFrame(
        {
            "user_id": list(g.nodes),
            "in_weight": [g.in_degree(n, weight="weight") for n in g.nodes],
            "out_weight": [g.out_degree(n, weight="weight") for n in g.nodes],
            "in_degree": [g.in_degree(n) for n in g.nodes],
            "out_degree": [g.out_degree(n) for n in g.nodes],
            "betweenness": [betweenness[n] for n in g.nodes],
        }
    )


def communities(g: nx.DiGraph, seed: int = 7) -> dict[int, int]:
    """Louvain clusters on the undirected, volume-weighted graph: user_id -> cluster index.

    Clusters are numbered by size (0 = largest). Deterministic for a given ``seed``.
    """
    if g.number_of_nodes() == 0:
        return {}
    parts = nx.community.louvain_communities(undirected(g), weight="weight", seed=seed)
    parts = sorted(parts, key=len, reverse=True)
    return {node: i for i, part in enumerate(parts) for node in part}


def isolated_members(active_user_ids, g: nx.DiGraph) -> list[int]:
    """Members who posted but whom nobody ever replied to or mentioned."""
    return sorted(uid for uid in set(active_user_ids) if uid not in g or g.in_degree(uid) == 0)
