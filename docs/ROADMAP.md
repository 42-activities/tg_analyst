# Roadmap

Development plan for TG Analyst. Each milestone ends with working, tested code on `main`.
`README.md` describes the design and `CLAUDE.md` holds the working rules.

## M1 — Foundation and ingestion
Goal: real or synthetic Telegram data lands in a normalized database, and the interaction edges are built.

- [x] Package skeleton (`src/` layout, `pyproject.toml`, ruff, pytest)
- [x] Settings from `.env` (`pydantic-settings`)
- [x] Storage: SQLAlchemy 2.x models for `groups`, `users`, `messages`, `interactions` and `enrichment`
- [x] Dialect-aware upserts (SQLite and PostgreSQL) so ingestion is idempotent
- [x] Conversion from Telethon to plain records: text, reply headers, mentions, media type, service actions (join/leave)
- [x] Back-fill with a resumable per-group high-water mark
- [x] Live listener (`events.NewMessage`)
- [x] Interaction edges derived in SQL from stored messages: replies and mentions. They can be rebuilt at any time.
- [x] Synthetic community generator (`tg-analyst seed`) for development and tests
- [x] CLI: `init-db`, `seed`, `backfill`, `listen`, `build-edges`

## M2 — Enrichment
Goal: every message has a tone, an intent and a model version.

- [ ] Text cleaning: URLs, emoji normalization, empty and sticker-only messages
- [ ] Sentiment backend interface with a VADER implementation as the baseline
- [ ] Multilingual HuggingFace backend (`cardiffnlp/twitter-xlm-roberta-base-sentiment`) as an optional extra
- [ ] Intent heuristics: question, thanks, greeting, complaint
- [ ] Toxicity score (Detoxify or a HuggingFace model, optional)
- [ ] `tg-analyst enrich` that processes only rows missing or carrying a stale `model_version`

## M3 — Relationship analytics
Goal: pure functions that turn the database into relationship metrics.

- [ ] KPI layer: message velocity, DAU/WAU/MAU, media ratio, replies per message, median time to first reply
- [ ] Interaction graph (NetworkX): weighted by volume, edges carry average tone
- [ ] Centrality (degree, betweenness), communities (Louvain), bridges and isolates
- [ ] Directional sentiment: given vs. received per member, and tone between pairs over time
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
- [ ] Scheduled enrichment and a Docker image
