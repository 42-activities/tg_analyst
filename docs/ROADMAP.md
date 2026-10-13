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
- [ ] More KPIs: WAU/MAU, media ratio, median time to first reply
- [ ] Interaction graph (NetworkX): weighted by volume, edges carry average tone
- [ ] Centrality (degree, betweenness), communities (Louvain), bridges and isolates
- [x] Directional sentiment: given vs. received per member, and tone between pairs (aggregate)
- [ ] Tone between pairs over time
- [ ] Role classification: connector, helper, starter, lurker, critic, newcomer
- [ ] Newcomer analytics: time and tone of the first reply, cohort retention, churn signals
- [ ] Conflict detection: bursts of negative reply chains between the same pair

## M4 — Dashboard
Goal: a Streamlit app over M3.

- [ ] Global filters: group, date range, user, keyword
- [ ] Pages: Overview (KPIs), Network, Members, Mood, Newcomers, Heatmaps, Explorer
- [ ] Pseudonymization toggle for screenshots and sharing

## M5 — Hardening and scale
- [ ] Alembic migrations (replacing `create_all`)
- [ ] PostgreSQL deployment profile (docker-compose)
- [ ] Reaction edges (`GetMessageReactionsList`, available only in some groups)
- [ ] Topic extraction (keywords, then BERTopic)
- [x] Docker image; arm64 auto-deploy on push to `main` with rollback
- [x] Public URL with HTTPS and basic auth (https://tg-analyst.naurzalinov.me)
- [ ] Scheduled enrichment
