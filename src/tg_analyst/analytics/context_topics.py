"""Context topics: clusters of similar conversation threads. Pure functions, no I/O.

Threads come from ``analytics.threads`` and their vectors from ``enrich.embeddings``
(L2-normalized, so a dot product is the cosine similarity). Threads are clustered per
time window (average linkage, cosine distance below ``threshold``), then the windows'
cluster centroids are clustered again into topics that span the whole history.

Average linkage builds an n x n distance matrix, so no clustering step ever sees more
than ``max_points`` points: larger sets are split into chunks and their centroids are
clustered in turn.
"""

import numpy as np
import pandas as pd

from tg_analyst.analytics.conflict import flag
from tg_analyst.analytics.metrics import human_messages

MAX_POINTS = 4000  # 4000^2 float64 distances = 128 MB
TOPIC_COLUMNS = [
    "topic_id",
    "threads",
    "messages",
    "members",
    "reactions",
    "tone",
    "tone_vs_group",
    "insult_share",
    "first_seen",
    "last_seen",
]


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


def _centroids(vectors: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Normalized mean vector per label (labels >= 0), and the labels in that order."""
    ids = np.unique(labels[labels >= 0])
    means = np.vstack([vectors[labels == i].mean(axis=0) for i in ids]) if len(ids) else None
    return (_normalize(means) if means is not None else vectors[:0]), ids


def _agglomerate(vectors: np.ndarray, threshold: float, max_points: int) -> np.ndarray:
    """Average-linkage cluster ids for every row (singletons get their own id)."""
    from sklearn.cluster import AgglomerativeClustering

    n = len(vectors)
    if n < 2:
        return np.zeros(n, dtype=int)
    if n <= max_points:
        model = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average", distance_threshold=threshold
        )
        return model.fit_predict(vectors)
    # Too many points for one distance matrix: cluster consecutive chunks, then their
    # centroids, and map the centroid clusters back to the rows.
    chunks = np.array_split(np.arange(n), int(np.ceil(n / max_points)))
    local = np.empty(n, dtype=int)
    offset = 0
    for chunk in chunks:
        labels = _agglomerate(vectors[chunk], threshold, max_points)
        local[chunk] = labels + offset
        offset += labels.max() + 1
    centroids, ids = _centroids(vectors, local)
    merged = _agglomerate(centroids, threshold, max_points)
    return merged[np.searchsorted(ids, local)]


def cluster_window(
    vectors: np.ndarray, threshold: float, min_threads: int = 2, max_points: int = MAX_POINTS
) -> np.ndarray:
    """Cluster id per thread, or -1 for threads in clusters smaller than ``min_threads``.

    Ids are numbered by cluster size, largest first.
    """
    labels = _agglomerate(vectors, threshold, max_points)
    if len(labels) == 0:
        return labels
    sizes = pd.Series(labels).value_counts()
    keep = sizes[sizes >= min_threads]
    renumber = {label: i for i, label in enumerate(keep.index)}
    return np.array([renumber.get(label, -1) for label in labels], dtype=int)


def cluster_windows(
    threads: pd.DataFrame,
    vectors: np.ndarray,
    threshold: float,
    period: str = "Q",
    min_threads: int = 2,
    max_points: int = MAX_POINTS,
) -> pd.DataFrame:
    """Per-window clusters: ``root_id, window, cluster`` for threads in a cluster.

    ``threads`` has ``root_id`` and ``start``, row-aligned with ``vectors``.
    """
    columns = ["root_id", "window", "cluster"]
    if threads.empty:
        return pd.DataFrame(columns=columns)
    windows = pd.to_datetime(threads["start"]).dt.to_period(period).dt.start_time
    parts = []
    for window, rows in pd.Series(np.arange(len(threads))).groupby(windows.to_numpy()):
        labels = cluster_window(vectors[rows.to_numpy()], threshold, min_threads, max_points)
        part = pd.DataFrame(
            {"root_id": threads["root_id"].to_numpy()[rows], "window": window, "cluster": labels}
        )
        parts.append(part[part["cluster"] >= 0])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=columns)


def merge_windows(
    window_clusters: pd.DataFrame,
    roots: np.ndarray,
    vectors: np.ndarray,
    threshold: float,
    max_points: int = MAX_POINTS,
) -> pd.Series:
    """``root_id -> topic_id`` across the whole history.

    Each window cluster's normalized centroid is clustered with the same method; clusters
    that don't merge with others stay topics of their own. Topic ids are numbered by the
    number of threads, largest first.
    """
    if window_clusters.empty:
        return pd.Series(dtype=int, name="topic_id")
    position = pd.Series(np.arange(len(roots)), index=roots)
    keys = window_clusters["window"].astype(str) + "/" + window_clusters["cluster"].astype(str)
    codes, uniques = pd.factorize(keys)
    rows = position[window_clusters["root_id"]].to_numpy()
    centroids, ids = _centroids(vectors[rows], codes)
    merged = _agglomerate(centroids, threshold, max_points)
    topic = pd.Series(merged[np.searchsorted(ids, codes)], index=window_clusters["root_id"])
    order = topic.value_counts().index
    return topic.map({old: new for new, old in enumerate(order)}).rename("topic_id")


def representatives(vectors: np.ndarray, roots) -> list[int]:
    """Up to three distinct threads: the centre (closest to the centroid), the thread least
    similar to the centre, and the thread least similar to that one."""
    roots = list(roots)
    if len(roots) <= 1:
        return roots
    centroid = _normalize(vectors.mean(axis=0, keepdims=True))[0]
    centre = int(np.argmax(vectors @ centroid))
    chosen = [centre]
    while len(chosen) < min(3, len(roots)):
        similarity = vectors @ vectors[chosen[-1]]
        similarity[chosen] = np.inf
        chosen.append(int(np.argmin(similarity)))
    return [roots[i] for i in chosen]


def _with_topics(msgs: pd.DataFrame, membership: pd.DataFrame, assignment: pd.Series):
    topics = membership.assign(topic_id=membership["root_id"].map(assignment)).dropna()
    topics["topic_id"] = topics["topic_id"].astype(int)
    return topics.merge(msgs, on="message_id")


def topic_table(msgs: pd.DataFrame, membership: pd.DataFrame, assignment: pd.Series):
    """Popularity and tone per topic, most messages first.

    Tone is the mean ``sentiment_score`` of scored messages (unscored are skipped), and
    ``tone_vs_group`` subtracts the group's mean tone over the same messages frame.
    """
    frame = _with_topics(msgs, membership, assignment)
    if frame.empty:
        return pd.DataFrame(columns=TOPIC_COLUMNS)
    frame["reactions"] = frame["reaction_count"].fillna(0) if "reaction_count" in frame else 0
    frame["insult"] = flag(frame, "insult") | flag(frame, "threat")
    out = frame.groupby("topic_id").agg(
        threads=("root_id", "nunique"),
        messages=("message_id", "size"),
        members=("sender_id", "nunique"),
        reactions=("reactions", "sum"),
        tone=("sentiment_score", "mean"),
        insult_share=("insult", "mean"),
        first_seen=("timestamp", "min"),
        last_seen=("timestamp", "max"),
    )
    out["reactions"] = out["reactions"].astype(int)
    out["tone_vs_group"] = out["tone"] - human_messages(msgs)["sentiment_score"].mean()
    out = out.reset_index()[TOPIC_COLUMNS]
    return out.sort_values(["messages", "topic_id"], ascending=[False, True], ignore_index=True)


def topic_timeline(
    msgs: pd.DataFrame, membership: pd.DataFrame, assignment: pd.Series, period: str = "Q"
) -> pd.DataFrame:
    """Messages per topic and period (``topic_id, period, messages``)."""
    frame = _with_topics(msgs, membership, assignment)
    if frame.empty:
        return pd.DataFrame(columns=["topic_id", "period", "messages"])
    frame["period"] = frame["timestamp"].dt.to_period(period).dt.start_time
    return frame.groupby(["topic_id", "period"]).size().reset_index(name="messages")
