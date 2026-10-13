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
- [ ] Toxicity score (Detoxify or a HuggingFace model, optional). Moved to M5.
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
- [ ] Per-user drill-down page
- [x] Tabs: Activity & mood, Members (with roles), Network, Relationships (with conflicts and pair timelines), Newcomers, Messages
- [x] Pseudonymization toggle for screenshots and sharing

## M5 — Hardening and scale
- [ ] Alembic migrations (replacing `create_all`)
- [ ] PostgreSQL deployment profile (docker-compose)
- [ ] Reaction edges (`GetMessageReactionsList`, available only in some groups)
- [ ] Topic extraction (keywords, then BERTopic)
- [x] Docker image; arm64 auto-deploy on push to `main` with rollback
- [x] Public URL with HTTPS and basic auth (https://tg-analyst.naurzalinov.me)
- [ ] Scheduled enrichment
