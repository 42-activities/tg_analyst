# Context topics: threads → embeddings → clusters → summaries

Status: **planned, not started.** Two decisions are open (see the end).

Keyword topics (`analytics/topics.py`) count stemmed words. They are fast and local, but
they can't tell "visa for a job" from "job interview", and they give no narrative. Context
topics group whole conversations by meaning, then describe each group in a sentence or two.

This is the "model-based topics" item in M5 of `ROADMAP.md`. It computes embeddings but
is **not RAG** (M6): there is no question answering and no retrieval for a prompt.

## Pipeline

```
messages ──► 1. threads ──► 2. embeddings ──► 3. clusters per window ──► 4. merge windows
                                                                      │
             6. dashboard ◄── 5. label/summary (3 threads per cluster) ◄┘
```

### 1. Build threads (pure, `analytics/threads.py`)

1. **Reply chains:** connected components over `reply_to_msg_id` (union-find).
2. **Same-author bursts:** a message from the same sender within 2 minutes of their
   previous message joins that message's thread (people split one thought into several
   messages).
3. **Channel discussion groups / forums:** when nearly every message replies to a channel
   post or forum topic (`thread_id` = Telegram's top message), split by that top message,
   so one post's comment section doesn't become one lopsided thread.
4. Keep threads with **≥ 3 messages**. Leftover single messages are dropped, or later
   attached to the nearest cluster only if their similarity is above the threshold.
5. Skip bots, service messages and excluded members (as everywhere else).

Measured on production (structure only, no text):

| Group size | Replies | Threads ≥ 3 | Msgs in threads | + 2-min rule |
|---|---|---|---|---|
| 503k | 28% | 23.6k | 21% | 37% |
| 284k | 59% | 24.4k | 58% | 83% |
| 45k | 53% | 4.6k | 55% | 84% |
| 36k | 45% | 3.2k | 43% | 71% |
| 14k | 83% | 1.0k | 32% | 59% |
| 11k | 96% | 1.1k | 49% | 60% |
| 6k | 99% | 0.4k | 29% | 42% |

Typical thread: 3–4 messages; the largest: 144. Groups where people rarely press Reply
(the 503k one) lose most messages; that's accepted.

### 2. Thread embeddings (worker job, `enrich/embeddings.py`)

- Model: **`intfloat/multilingual-e5-small`** (118M, ~0.5 GB RAM, MIT, RU/EN/FR).
  Fallback: `cointegrated/rubert-tiny2` or `sergeyzh/rubert-tiny-turbo` (~30M, Russian).
- Not Jina v3: ~2.2 GB RAM in fp32 (the server has ~1.7 GB free and no free swap),
  custom remote code, and a non-commercial licence (CC BY-NC).
- Thread text = messages in order, as `A: …`/`B: …` (per-thread pseudonyms, never ids or
  names), truncated to the model's 512 tokens (first messages + the longest replies).
- Stored with `model_version`, like enrichment, so a model change triggers re-embedding.
  Rows are derived from messages and must be rebuilt when a thread's messages change
  (edit, delete, exclusion).
- Speed on the arm64 CPU: measure first; expected minutes for 36k messages, tens of
  minutes for the 300–500k groups. Runs as a worker job with progress reporting.
- New deps in the `hf` extra: `sentence-transformers` (or plain transformers mean-pooling)
  and `scikit-learn`.

### 3. Cluster threads per time window (pure, `analytics/context_topics.py`)

- `AgglomerativeClustering(metric="cosine", linkage="average", distance_threshold=θ)`.
  Average (or complete) linkage, **not single linkage**, which chains everything into one
  giant cluster.
- **Per quarter**, not the whole history: the distance matrix is n², and 31k threads would
  need ~7.7 GB RAM. A quarter is ~3k threads (~70 MB).
- **θ is tuned on real data**, per model. e5 similarities are compressed (most pairs
  0.7–0.9), so the 0.25–0.35 "typical" distance would merge everything. Tune on the 36k
  group: inspect cluster sizes and a sample of members per θ.
- Clusters with < 2 threads are dropped.

### 4. Merge windows into whole-history topics

Cluster the quarterly cluster centroids (a few thousand points) with the same method.
This gives topics across the whole history and, for free, how each topic grows or fades
per quarter.

### 5. Label and summarize

Per cluster pick three threads:

- **centre**: the thread closest to the normalized centroid (the medoid);
- **edges**: the thread least similar to the centre, then the thread least similar to that
  one. These are the two sub-angles of the topic. (Alternative: the earliest and the
  latest thread, which shows how the topic evolved.)

Then either:

- **A. Keyword labels (local):** distinctive stems of the cluster's threads vs. the rest
  of the group (c-TF-IDF over `enrich/keywords.py` stems). No text leaves the server.
- **B. LLM summary:** send the three threads (truncated to ~1.5k chars each) to Claude
  Haiku, asking for strict JSON `{"title": "...", "summary": "..."}` in the chat's
  language. Only the top 20–30 clusters per group, so the cost is cents per group.
  Requirements:
  - pseudonymized text; excluded members' messages never sent;
  - chat text is untrusted input: the output is validated as JSON and rendered with
    `escape_md()`; a message that tries to instruct the model must not change anything
    beyond the summary text;
  - `ANTHROPIC_API_KEY` in the `ENV_FILE` secret, and a setting to turn it off;
  - summaries stored with the model name and the cluster's thread ids, regenerated only
    when the cluster changes.

### 6. Popularity, tone and the dashboard

- Popularity: messages, distinct members, reactions, threads.
- Tone: mean `sentiment_score` of the cluster's messages (skip unscored, never treat them
  as 0), shown **relative to the group** like keyword topics; insult share from
  toxicity. No re-scoring needed.
- Topics tab: a "Keywords | Conversations" toggle. Conversations shows:
  - a bubble chart: x = tone vs. group, y = messages, size = members, colour = insult
    share;
  - topic cards (title, summary, counts) with the three representative threads in an
    expander, using the existing transcript renderer;
  - topic popularity per quarter.

## Storage (Alembic migration)

- `thread_embeddings(group_id, root_message_id, message_ids, model_version, vector)` as
  float arrays (no pgvector needed at this size).
- `context_topics(group_id, window, topic_id, thread_ids, centre/edge ids, label, summary,
  summary_model, created_at)`.
- Rebuilt by the job; dropped or marked stale when member messages are deleted or a
  member is excluded.

## Steps

1. Thread builder + tests on synthetic data (reply chains, bursts, forum/channel split).
2. Embedding job (model download into the `tgdata` volume, RAM and speed measured on the
   server) + migration.
3. Clustering per window + merge, θ tuned on the 36k group (aggregates only).
4. Keyword labels (option A) and the dashboard view. Usable on its own.
5. Option B (LLM summaries) behind a setting, if chosen.
6. Roll out to all groups; schedule after enrichment (e.g. nightly).

## Resources

- RAM is the limit, not disk: ~1.7 GB available, swap full (other services share the
  host). Model ~0.5 GB; clustering per quarter < 100 MB.
- Disk: model ~0.5 GB, embeddings ~50 MB for the largest group. Not a concern.

## Open decisions

1. **Summaries:** Claude (text of three threads per topic goes to Anthropic) or keyword
   labels only (nothing leaves the server)?
2. **Rollout:** start with the 36k group to tune θ, then all groups?
