# Roadmap

Development plan for TG Analyst. Each milestone ends with working, tested code on `main`.
`README.md` describes the design and `CLAUDE.md` holds the working rules.

## M1 — Foundation and ingestion
Goal: real or synthetic Telegram data lands in a normalized database, and the interaction edges are built.

- [x] Package skeleton (`src/` layout, `pyproject.toml`, ruff, pytest)
- [x] Settings from `.env` (`pydantic-settings`)
- [x] Storage: SQLAlchemy 2.x models for `groups`, `users`, `messages`, `interactions` and `enrichment`
- [x] PostgreSQL upserts (`ON CONFLICT`) so ingestion is idempotent
- [x] Conversion from Telethon to plain records: text, reply headers, mentions, media type, service actions (join/leave)
- [x] Back-fill with a resumable per-group high-water mark
- [x] Live listener (`events.NewMessage`)
- [x] Interaction edges derived in SQL from stored messages: replies and mentions. They can be rebuilt at any time.
- [x] Synthetic community generator (`tg-analyst seed`) for development and tests
- [x] CLI: `init-db`, `seed`, `backfill`, `listen`, `build-edges`

## M2 — Enrichment
Goal: every message has a tone, an intent and a model version.

- [x] Text cleaning: URL and mention placeholders, emoji kept, empty and sticker-only messages skipped
- [x] Sentiment backend interface with a VADER implementation as the baseline (English only)
- [x] Multilingual HuggingFace backend (`cardiffnlp/twitter-xlm-roberta-base-sentiment`), `hf` extra. Verified on RU, KK and EN.
- [x] Production uses the small Russian model `cointegrated/rubert-tiny-sentiment-balanced`: 18× faster than XLM-R and 72% label agreement with it on real chat data. The image ships `hf` by default.
- [x] Intent heuristics (EN and RU): question, thanks, greeting, complaint
- [x] Toxicity: insult/obscenity/threat with `cointegrated/rubert-tiny-toxicity` (see M5)
- [x] `tg-analyst enrich` that processes only rows missing or carrying a stale `model_version`

## M2.5 — Web UI (pulled forward)
- [x] DB-backed job queue (`jobs` table) and `tg-analyst worker`
- [x] Jobs page: queue a back-fill by group name, enrichment, demo data, live job status
- [x] Dashboard: KPIs, activity and tone timeline, heatmap, intent breakdown, members (tone given vs. received), top relationships with tense-pair alerts, message explorer, pseudonymization
- [x] `tg-analyst login` (one-time interactive Telegram login for the worker)
- [x] Docker Compose services `web` and `worker`

## M3 — Relationship analytics
Goal: pure functions that turn the database into relationship metrics.

- [x] Basic KPI layer: messages, active members, DAU, average tone, negative share, joins
- [x] More KPIs: weekly active members, media share, median time to first reply
- [x] Interaction graph (NetworkX): weighted by volume, edges carry average tone
- [x] Centrality (degree, betweenness), communities (Louvain), bridges and isolates
- [x] Directional sentiment: given vs. received per member, and tone between pairs (aggregate)
- [x] Tone between pairs over time (weekly)
- [x] Role classification: connector, helper, starter, lurker, critic, newcomer (relative thresholds; matches synthetic ground truth)
- [x] Newcomer analytics: whether/when/how warmly the first message was answered, 14-day retention split by welcome
- [x] Conflict detection: bursts of hostile exchanges per pair, mutual (feud) vs one-sided

## M4 — Dashboard
Goal: a Streamlit app over M3.

- [x] Global filters: group, date range (defaults to the group's data); keyword/tone/type in the explorer
- [x] Per-member page: profile, role, activity and tone over time, writing hours, partners with tone each way, conflicts, recent messages (`/member?user=<id>`)
- [x] Tabs: Activity & mood, Members (with roles), Network, Relationships (with conflicts and pair timelines), Newcomers, Messages
- [x] Pseudonymization toggle for screenshots and sharing

## M5 — Hardening and scale
- [x] Alembic migrations (replacing `create_all`); production stamped at `0001_baseline`
- [x] PostgreSQL deployment profile (docker-compose)
- [x] Privacy opt-out: excluded members are purged and skipped by ingestion (`0002_excluded_users`)
- [x] Toxicity scores (`0004_toxicity_labels`): insults and threats count as hostile, swearing alone doesn't; insult KPI, member columns, toxicity filter, labels in conflict transcripts
- [x] Reaction edges: `reactions` table (`0003_reactions`), emoji tone, back-fill/reconcile/listener, full list via `GetMessageReactionsList` when the summary is partial, "Count reactions" toggle, reaction KPI and emoji chart
- [x] Topic extraction, keyword version: stemmed keywords, tone vs. group, trending, per-topic drill-down, member topics (Topics tab, member page)
- [ ] Model-based topics (BERTopic or embedding clusters), only if keywords fall short. Could share embeddings with M6.
- [x] Docker image; arm64 auto-deploy on push to `main` with rollback
- [x] Public URL with HTTPS and basic auth (https://tg-analyst.naurzalinov.me)
- [x] Sync with Telegram: apply edits and deletions (listener events + periodic re-check of stored messages)
- [x] Scheduled refresh: periodic incremental back-fill + enrichment for every group

## M6 — Ask the chat (RAG) — planned, not started
Goal: answer practical questions from the group's collective memory (titre de séjour, préfecture, plan de formation, banks, 42 rules…) and cite the original messages.

```
messages ──▶ chunk builder ──▶ chunks (+ chunk_messages) ──▶ embeddings (pgvector)
   (worker job, like enrich)                                        │
question ──▶ embed ──▶ hybrid search (vector + PostgreSQL full-text) ┴▶ top-k threads ──▶ LLM ──▶ answer with message links
```

**Prerequisites (before relying on it)**
- [x] Database in sync with Telegram (edits/deletions, see M5)
- [ ] Deletions must also remove chunks and embeddings (cascade from `messages`), or answers keep quoting deleted messages
- [ ] Privacy and consent (GDPR applies): keep it private to the group, pseudonymize names in answers, honour opt-outs (`excluded_users`), and decide whether chat text may go to a cloud LLM or must stay on a local model.
- [ ] Content stays private: the repo is public, so test data stays synthetic. Chunks and embeddings live only in the production database.

**Phase 1: retrieval only, no LLM**
- [ ] Storage: the `pgvector` extension in the same PostgreSQL (image `pgvector/pgvector:pg16` instead of `postgres:16-alpine`), via an Alembic migration
- [ ] Conversation chunks, not single messages: reply chains (`reply_to_msg_id`, `thread_id`) plus nearby messages in a time window, i.e. "question + the answers it got". Table `chunk_messages(chunk_id, group_id, message_id)` so every result can cite its messages.
- [ ] Multilingual embeddings (RU/FR/EN): `BAAI/bge-m3` or `intfloat/multilingual-e5-base` in an optional extra, run as a worker batch job, with `model_version` like enrichment. Check RAM: the server has ~2 GB free.
- [ ] Hybrid search: vector similarity + PostgreSQL full-text, for exact terms like "titre", "RIB", "APL"
- [ ] Dashboard search page that shows the matching threads, with recency shown and favoured
- [ ] Evaluate retrieval quality on real questions before building phase 2

**Phase 2: answers**
- [ ] Pluggable LLM backend (e.g. Claude) that answers only from the retrieved threads, cites message links, and is date-aware (older admin advice may be outdated)
- [ ] Serving: a small `rag` service the dashboard calls, for interactive latency (the dashboard itself must not run models)
