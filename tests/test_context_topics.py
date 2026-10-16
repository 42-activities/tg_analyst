from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from tg_analyst.analytics.context_topics import (
    cluster_window,
    cluster_windows,
    merge_windows,
    representatives,
    topic_table,
    topic_timeline,
)


def blobs(centres: int, per: int, dims: int = 16, noise: float = 0.05, seed: int = 0):
    """Well-separated unit vectors: ``per`` noisy copies of ``centres`` random directions."""
    rng = np.random.default_rng(seed)
    base = np.eye(dims)[:centres]
    vectors = np.repeat(base, per, axis=0) + rng.normal(0, noise, (centres * per, dims))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors.astype(np.float32), np.repeat(np.arange(centres), per)


def same_partition(a, b) -> bool:
    return len(set(zip(a, b, strict=True))) == len(set(a)) == len(set(b))


def test_cluster_window_recovers_separated_topics():
    vectors, truth = blobs(4, 10)
    labels = cluster_window(vectors, threshold=0.2)
    assert same_partition(labels, truth)


def test_cluster_window_drops_small_clusters_and_handles_tiny_inputs():
    vectors, _ = blobs(3, 5)
    lonely = np.eye(16, dtype=np.float32)[[10]]
    labels = cluster_window(np.vstack([vectors, lonely]), threshold=0.2, min_threads=2)
    assert labels[-1] == -1 and (labels[:-1] >= 0).all()
    assert list(cluster_window(lonely, threshold=0.2)) == [-1]
    assert len(cluster_window(lonely[:0], threshold=0.2)) == 0


def test_average_linkage_does_not_chain_a_line_into_one_cluster():
    # Points along an arc, each close to its neighbour: single linkage would join them all.
    angles = np.linspace(0, np.pi / 2, 40)
    vectors = np.stack([np.cos(angles), np.sin(angles)], axis=1).astype(np.float32)
    labels = cluster_window(vectors, threshold=0.05, min_threads=1)
    assert len(set(labels)) > 3


def test_chunking_gives_the_same_topics_as_one_pass():
    vectors, truth = blobs(5, 40, seed=1)
    order = np.random.default_rng(2).permutation(len(vectors))
    vectors, truth = vectors[order], truth[order]
    assert same_partition(cluster_window(vectors, threshold=0.2, max_points=30), truth)


def test_merge_links_the_same_topic_across_quarters():
    vectors, truth = blobs(3, 12)
    # Every topic appears in two quarters.
    start = [datetime(2026, 1, 15) if i % 2 else datetime(2026, 5, 15) for i in range(36)]
    threads = pd.DataFrame({"root_id": np.arange(100, 136), "start": start})
    per_window = cluster_windows(threads, vectors, threshold=0.2)
    assert per_window["window"].nunique() == 2
    assert per_window.groupby("window")["cluster"].nunique().tolist() == [3, 3]
    topics = merge_windows(per_window, threads["root_id"].to_numpy(), vectors, threshold=0.2)
    assert same_partition(topics[threads["root_id"]].to_numpy(), truth)


def test_representatives_are_distinct_and_start_at_the_centre():
    vectors = np.array([[1, 0], [0.8, 0.6], [0.6, -0.8], [0.95, 0.31]], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    # Centre: closest to the mean; then the farthest from it; then the farthest from that.
    assert representatives(vectors, [11, 12, 13, 14]) == [11, 13, 12]
    assert representatives(vectors[:3], [11, 12, 13])[0] == 11
    assert sorted(representatives(vectors[:3], [11, 12, 13])) == [11, 12, 13]
    assert sorted(representatives(vectors[:2], [11, 12])) == [11, 12]
    assert representatives(vectors[:1], [11]) == [11]


def test_topic_table_and_timeline():
    t0 = datetime(2026, 3, 30)
    msgs = pd.DataFrame(
        {
            "message_id": [1, 2, 3, 4, 5, 6, 7],
            "sender_id": [1, 2, 1, 3, 3, 4, 5],
            "timestamp": [t0 + timedelta(days=d) for d in (0, 0, 0, 5, 5, 5, 6)],
            "sentiment_score": [0.4, None, 0.2, -0.5, -0.3, -0.4, 0.9],
            "insult": [0, 0, 0, 0.9, 0, 0, 0],
            "reaction_count": [1, None, 2, 0, 0, 0, 0],
            "is_service": False,
            "is_bot": False,
        }
    )
    membership = pd.DataFrame({"message_id": [1, 2, 3, 4, 5, 6], "root_id": [1, 1, 1, 4, 4, 4]})
    assignment = pd.Series({1: 1, 4: 0})
    table = topic_table(msgs, membership, assignment)
    assert table["topic_id"].tolist() == [0, 1] or table["messages"].tolist() == [3, 3]
    first = table.set_index("topic_id").loc[1]
    assert (first.threads, first.messages, first.members, first.reactions) == (1, 3, 2, 3)
    assert np.isclose(first.tone, 0.3)  # the unscored message is skipped
    group_tone = np.mean([0.4, 0.2, -0.5, -0.3, -0.4, 0.9])
    assert np.isclose(first.tone_vs_group, 0.3 - group_tone)
    assert np.isclose(table.set_index("topic_id").loc[0].insult_share, 1 / 3)

    timeline = topic_timeline(msgs, membership, assignment)
    assert set(timeline["period"].dt.month) == {1, 4}
    assert timeline["messages"].sum() == 6
