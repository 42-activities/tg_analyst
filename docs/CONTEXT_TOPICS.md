# Context topics: threads → embeddings → clusters → summaries

Status: **in progress.** Decided 2026-10-16: Claude summaries (phase 5), start with the
36k group (42), embeddings with `rubert-tiny2` (phase 0).

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
3. **Hubs (channel posts, forum topics):** a message with ≥ 50 direct replies is not a
   conversation. Replies to it start their own threads, and bursts don't cross hubs.
   On production, three forum-style groups have hubs with 1,600–2,400 direct replies;
   ordinary groups never exceed ~31.
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

- Model: **`cointegrated/rubert-tiny2`** (29M, MIT, RU/EN; 21.6 threads/s on the server).
  Alternative: `intfloat/multilingual-e5-small` (118M, multilingual, but 7× slower).
  Another option: `sergeyzh/rubert-tiny-turbo` (~30M, Russian).
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

## What to implement

Phases 0–5 were built on 2026-10-16 in one branch. Differences from the plan below:
one migration (`0006_context_topics`) for both tables; thread helpers are vectorized
(`thread_texts`, `content_keys`) because one call per thread over a 500k-message frame
is quadratic; hubs (≥ 50 direct replies) replace the `thread_id` split; topic stats are
computed in the dashboard from `thread_embeddings.message_ids`, so they follow the date
filter; the job is `enrich/topics_job.py`; schedule via `AUTO_TOPICS_HOURS` (0 = by hand).
What's left is phase 6.

### Phase 0: measure before building (no code merged)

- [x] On the server, in a one-off container: embed 1,000 random threads of the 36k group
      (aggregates only, no text printed). 2026-10-16, arm64, mean pooling, 512 tokens:

      | | `multilingual-e5-small` | `cointegrated/rubert-tiny2` |
      |---|---|---|
      | Speed | 3.1 threads/s | 21.6 threads/s |
      | Peak RSS (incl. the group's messages) | 1.15 GB | 0.79 GB |
      | Pairwise cosine p10 / p50 / p90 | 0.82 / 0.85 / 0.88 | 0.53 / 0.65 / 0.78 |
      | Nearest neighbour p50 | 0.91 | 0.84 |
      | Usable θ (distance) | 0.10–0.12; one 150-thread blob at 0.12 | 0.15–0.25 |

      Threads in that group: 3,961 (68% of messages with hubs and bursts), median 4
      messages, largest 64, built in 1.5 s.
- [x] Decision: **`rubert-tiny2`**: 7× faster (a first full run of a 500k group is ~20 min
      instead of ~2 h), less RAM, and a wider similarity spread that leaves room for θ.
      e5 stays a setting. Starting θ = 0.2; tune on 42 in phase 6.

### Phase 1: threads (pure, no new dependencies)

- [x] `analytics/threads.py`
  - `Thread` dataclass: `root_id`, `message_ids`, `sender_ids`, `start`, `end`,
    `reaction_total`.
  - `build_threads(msgs, burst_seconds=120, min_messages=3) -> pd.DataFrame` with one row
    per message: `message_id → root_id` (union-find, no networkx, so it scales to 500k).
    Passes: reply chains → same-author bursts → split channel/forum groups by `thread_id`
    → drop bots, service messages and threads below `min_messages`.
  - `thread_table(msgs, membership) -> pd.DataFrame`: one row per thread with messages,
    members, reactions, start/end, mean tone (`sentiment_score`, NaN skipped), insult
    share.
  - `thread_text(msgs, message_ids, max_chars) -> str`: `A: …` / `B: …` per-thread
    pseudonyms, `clean_text()` applied, @mentions masked, truncated (first messages, then
    the longest).
  - `content_key(msgs, message_ids) -> str`: a hash of message ids + `edit_date`s, used to
    detect threads that changed since they were embedded.
- [x] `tests/test_threads.py`: reply chains, bursts (and that bursts don't cross 2 min
      or other senders), channel/forum split, bots/service skipped, `min_messages`, tone
      ignores unscored messages, text pseudonyms never contain ids or names.
- [x] Synthetic data: a themed group fixture in `tests/conftest.py` (visa vs. python threads
      over two quarters) instead of changing `synthetic.py`, whose ground truth other tests
      rely on.

### Phase 2: embeddings (worker job + migration)

- [x] `pyproject.toml`: add `scikit-learn` to `hf` (and `sentence-transformers`, unless
      plain transformers mean-pooling is used; prefer plain transformers to keep the image
      smaller). Docker image already builds with `EXTRAS=web,hf`.
- [x] `config.py` + `.env.example` (+ `ENV_FILE` secret if non-default):
  - `HF_EMBEDDING_MODEL=intfloat/multilingual-e5-small` (empty = feature off),
  - `TOPIC_THRESHOLD` (θ), `TOPIC_WINDOW=quarter`, `TOPIC_MIN_THREADS=2`.
- [x] `enrich/embeddings.py`
  - `EmbeddingBackend` protocol: `version`, `embed(texts) -> np.ndarray` (L2-normalized,
    float32). `TransformersEmbedding` with the `"passage: "` prefix e5 expects, batch
    size 16–32, `torch.inference_mode()`, max 512 tokens.
  - `FakeEmbedding` for tests (deterministic bag-of-words vectors), never downloads.
- [x] `storage/models.py` + migration `0006_context_topics`:
  - `thread_embeddings`: PK `(group_id, root_message_id)`, `message_ids INTEGER[]`,
    `content_key`, `model_version`, `vector REAL[]`, `start`, `end`. FK `(group_id,
    root_message_id)` → `messages` with `ON DELETE CASCADE`.
  - Add to `LATER_TABLES` in `tests/test_migrations.py`.
- [x] `enrich/topics_job.py` (or in `pipeline.py`): `embed_threads(session, backend,
      group_id, progress)`:
  1. load the group's messages (`load.messages` over the whole range),
  2. `build_threads` → `content_key` per thread,
  3. embed only threads that are new, changed (key differs) or on an older
     `model_version`; delete rows for threads that no longer exist,
  4. report progress per batch.
- [x] `jobs.py`: new kind `topics` (`KINDS`, `RUNNERS`), params `{group_id?}`; chained
      after `enrich` in `schedule_due` once a day (not hourly).
- [x] `repository.exclude_user`: also delete `thread_embeddings` and `context_topics` of
      every group the member posted in (the next `topics` job rebuilds them without
      their messages). Return the count.
- [x] CLI: `tg-analyst topics [--group G]` for local runs.
- [x] Tests: only changed threads are re-embedded; deleting a root message removes the
      row (cascade); excluding a member removes the group's rows; model change
      re-embeds everything.

### Phase 3: clustering (pure)

- [x] `analytics/context_topics.py`
  - `cluster_window(vectors, threshold, min_threads) -> labels`:
    `AgglomerativeClustering(metric="cosine", linkage="average", distance_threshold=θ)`;
    guard `n < 2`.
  - `cluster_windows(threads, vectors, freq="QS") -> pd.DataFrame`: per-window labels.
  - `merge_windows(window_clusters, threshold) -> pd.DataFrame`: clusters the
    normalized window centroids into whole-history topics; returns `thread → topic`.
  - `representatives(vectors, members) -> (centre, edge_a, edge_b)`: medoid, then the
    least similar to it, then the least similar to that one (de-duplicated for
    clusters of 2).
  - `topic_table(threads, assignment, group_tone) -> pd.DataFrame`: messages, threads,
    members, reactions, tone, `tone_vs_group`, insult share, first/last seen; sorted by
    popularity.
  - `topic_timeline(threads, assignment, freq="QS")`.
- [x] `tests/test_context_topics.py`: on synthetic vectors (well-separated blobs)
      clusters are recovered; average linkage does not chain a line of points into one
      cluster; window merge links the same topic across quarters; representatives are
      distinct members; tone skips NaN.
- [ ] θ tuning on the 36k group: cluster sizes and number of topics per θ (aggregates
      only), plus a manual look at a few clusters' representative threads in the
      dashboard (not printed in the terminal).

### Phase 4: storage of results, keyword labels, dashboard

- [x] Migration `0007_context_topics_results` (or part of 0006):
  `context_topics(group_id, topic_id, model_version, threshold, label, summary,
  summary_model, centre_root, edge_roots INTEGER[], thread_roots INTEGER[],
  created_at)`. Rebuilt per group by the `topics` job (delete + insert in one
  transaction).
- [x] Keyword labels (option A): `topics.distinctive_terms(terms, message_ids, n=4)`, the
      cluster's stems weighted against the whole group (c-TF-IDF), reusing
      `message_terms`.
- [x] `analytics/load.py`: `context_topics(engine, group_id)` and
      `thread_embeddings(engine, group_id)` loaders (no vectors needed by the UI).
- [x] `dashboard/common.py`: `cached_context_topics(group_id)`.
- [x] `dashboard/views/dashboard.py`, Topics tab: a `Keywords | Conversations` radio.
      Conversations:
  - bubble chart (x = tone vs group, y = messages, size = members, colour = insult
    share), filtered to the selected date range by thread start;
  - topic cards: label (and summary when present), counts, `st.expander` with the three
    representative threads rendered through the existing transcript renderer (refactor
    `_episode_transcript`'s message rendering into a shared helper); all text via
    `show_text()` / `escape_md()`, pseudonymized when names are hidden;
  - popularity per quarter (stacked area of the top 10 topics);
  - an info box when the `topics` job hasn't run for this group yet.
- [x] `tests/test_web.py`: synthetic group with embeddings from `FakeEmbedding`; the
      Conversations view renders without exceptions, with and without hidden names.

### Phase 5: LLM summaries (only if chosen)

- [x] `pyproject.toml`: `anthropic` in a new `llm` extra; image `EXTRAS=web,hf,llm`.
- [x] Settings: `ANTHROPIC_API_KEY` (empty = off), `TOPIC_SUMMARY_MODEL`
      (Claude Haiku), `TOPIC_SUMMARY_TOP=25`. Added to `ENV_FILE` by the user.
- [x] `enrich/summaries.py`: build the prompt from the three representative threads
      (`thread_text`, pseudonymized, excluded members never included — they are not in
      the DB); system prompt says the threads are data, not instructions; ask for JSON
      `{"title", "summary"}` in the chat's language; validate with pydantic, cap lengths,
      retry once, otherwise fall back to the keyword label.
- [x] Only summarize topics whose `thread_roots` changed since the last summary (stored
      `summary_model` + representatives) to keep cost flat.
- [x] Tests with a fake client: invalid JSON → fallback; injected instructions in a
      message don't escape the JSON fields; nothing is called when the key is empty.
- [x] README/CLAUDE.md: what is sent to Anthropic and how to turn it off.

### Phase 6: rollout

- [ ] Run `topics` for the 36k group, tune θ, review in the dashboard.
- [ ] Run for all groups; check job time and peak RAM on the 300–500k groups.
- [ ] Schedule daily; tick "model-based topics" in `ROADMAP.md`; update `README.md`,
      `CLAUDE.md` (architecture rule for derived thread data), and this file's status.

## Resources

- RAM is the limit, not disk: ~1.7 GB available, swap full (other services share the
  host). Model ~0.5 GB; clustering per quarter < 100 MB.
- Disk: model ~0.5 GB, embeddings ~50 MB for the largest group. Not a concern.

## Decisions

1. **Summaries:** Claude Haiku summaries of the top topics (the text of three
   pseudonymized threads per topic goes to Anthropic), with keyword labels as the
   fallback and when the API key is empty. Decided 2026-10-16.
2. **Rollout:** the 36k group (42) first to tune θ, then all groups.
3. **Model:** `rubert-tiny2` (phase 0 measurements).
