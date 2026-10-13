# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project summary

TG Analyst is a Telegram community intelligence platform with four stages: **ingest** (Telethon) → **store** (PostgreSQL) → **enrich** (sentiment, intent, interaction graph) → **visualize** (Streamlit + Plotly). See `README.md` for the full design.

**Domain focus:** the analysis is about **user relationships and community dynamics**: who interacts with whom, the tone between members, how newcomers are treated, member roles, conflict and churn. It is **not** a crypto, market or ticker sentiment tool. Do not add finance-specific features (tickers, price correlation, hype tracking) unless asked.

Milestones 1–3 (ingestion, enrichment, relationship analytics) and most of the M4 dashboard are implemented. If a module from the planned layout doesn't exist yet, create it where the layout below puts it.

## Tech stack

- Python 3.12+ with a `src/` layout. The package is `tg_analyst`.
- **Telethon** for the MTProto client. It is async-first.
- **SQLAlchemy 2.x** on **PostgreSQL only** (psycopg 3). SQLite was deliberately dropped, so PostgreSQL features (`ON CONFLICT`, advisory locks, JSON) are fine to use.
- **pandas** for analytics and **NetworkX** for the interaction graph.
- **VADER** as the fast English baseline. For non-English or mixed chats (common in Telegram), use a multilingual **HuggingFace** model such as `cardiffnlp/twitter-xlm-roberta-base-sentiment`. An LLM API is an optional, pluggable backend.
- **Streamlit + Plotly** for the dashboard.
- **pytest** for tests and **ruff** for lint and format.

## Planned layout

```
src/tg_analyst/
  config.py      # pydantic-settings; reads .env
  ingest/        # Telethon client, backfill, live listener
  storage/       # ORM models, repositories, migrations (alembic)
  enrich/        # text cleaning, sentiment, toxicity, intent, topics
  graph/         # interaction graph building, centrality, communities, roles
  analytics/     # pure functions: DataFrame in -> metrics/DataFrame out
  dashboard/     # Streamlit app.py + pages/
tests/
data/            # local DB and *.session files; git-ignored
```

## Commands

```bash
pip install -e ".[dev,web]"                     # install (add ,hf for the multilingual model)
docker compose up -d db                        # PostgreSQL for dev and tests
pytest                                         # uses/creates tg_analyst_test (override: TEST_DATABASE_URL)
ruff check . && ruff format .                  # lint + format
tg-analyst seed                                # synthetic community, no Telegram needed
tg-analyst backfill --group <g> [--since DATE] # resumable history fetch
tg-analyst listen --group <g>                  # live stream
tg-analyst build-edges                         # re-derive interactions
tg-analyst enrich [--backend vader|hf]         # sentiment + intent
tg-analyst login                               # one-time Telegram login (interactive)
tg-analyst web / tg-analyst worker             # Streamlit UI / job runner
docker compose up -d db && docker compose run --rm app seed   # containerized stack
```

## Workflow

- Plan and progress are tracked in `docs/ROADMAP.md`. Tick items off as they land.
- `pipeline.yml` runs `ci.yml` (ruff, pytest on Python 3.12 + PostgreSQL 16, Docker smoke test), then `docker-deploy.yml`. **Every push to `main` deploys to production** (arm64 image → GHCR → SSH → `docker compose up`, with health check and rollback). Keep CI green: run `ruff check`, `ruff format --check` and `pytest` before committing.
- `docker-deploy.yml` is a verbatim copy of `bakyt92/ci-templates`. Change it there first, then re-copy it.
- Production is `~/projects/tg_analyst` on the arm64 server, served at https://tg-analyst.naurzalinov.me through host nginx with basic auth (`deploy/nginx/`). Never commit `.env` or credentials. The repo is **public**.
- Production config comes from the `ENV_FILE` repository secret, which the deploy writes to the server's `.env`. Don't tell the user to edit `.env` on the server: the next deploy overwrites it. New settings go into `ENV_FILE` (and `.env.example`).
- Use `repository._insert()` (PostgreSQL `INSERT ... ON CONFLICT`) for upserts, and chunk multi-row writes with `_chunks()`.
- Develop analytics and dashboard features against `tg-analyst seed`. Its ground truth (helpers, the FeudA/FeudB pair, ignored newcomers who churn) is meant to be asserted in tests.
- New dependencies go in `pyproject.toml`. Heavy ML dependencies belong in optional extras (e.g. `[hf]`), so the base Docker image stays slim.

## Architecture rules

- **Keep the layers separate.** The dashboard reads from storage and `analytics/` only. It never calls Telethon or runs model inference. Work the UI triggers goes through the `jobs` table and is executed by `tg-analyst worker` (`jobs.RUNNERS`). Enrichment is a separate batch step that writes its results back to the database.
- **Streamlit pages** live in `dashboard/pages/*.py`, with shared cached resources in `dashboard/common.py`. `tests/test_web.py` smoke-tests them headlessly with `AppTest`.
- `init_db` must stay safe when several processes call it at once: it takes a PostgreSQL advisory lock.
- **The interaction edge is the core unit.** Replies, mentions, reactions and forwards each become a row in `interactions` (`source_user_id → target_user_id`). Relationship metrics such as pair sentiment, centrality and roles derive from these edges joined with message sentiment, so keep edge extraction correct and complete.
- **Sentiment is directional.** Store a message's sentiment once, then attribute it to the edge. "Sentiment given" (as sender) and "sentiment received" (as target) are different metrics, so don't conflate them.
- **Ingestion must be idempotent.** Upsert on `(group_id, message_id)`. Track a per-group high-water mark (`last_message_id`) so back-fills can resume and incremental runs stay cheap.
- **Handle rate limits.** Catch Telethon's `FloodWaitError` and sleep for `e.seconds`. Never use tight loops against the API.
- **Version the models.** Store `model_version` with each enrichment row so a model change can trigger re-scoring.
- **Pure analytics.** Functions in `analytics/` and `graph/` take DataFrames or graphs and return results with no I/O, which makes them easy to unit test.
- **Cache in Streamlit.** Use `st.cache_data` for queries and aggregates, and `st.cache_resource` for DB engines and models.
- Store timestamps in UTC. Convert to the viewer's local time only in the dashboard.

## Conventions

- Type hints everywhere. Use dataclasses or pydantic for structured records.
- Use `async`/`await` in ingest code. Keep everything else synchronous unless there's a reason not to.
- Use the `logging` module, not `print`, in library code.
- Tests must not hit the Telegram API or download models. Use fixtures with synthetic messages and mock the Telethon client.
- Keep functions small and name metrics explicitly, e.g. `median_first_reply_seconds`, not `mfr`.

## Secrets and privacy (important)

- Credentials (`TG_API_ID`, `TG_API_HASH`, `TG_PHONE`) live in `.env`. Never hard-code or log them. Keep `.env.example` up to date with placeholder values.
- Telethon `*.session` files grant **full account access**. They must stay git-ignored and live under `data/`.
- Never commit databases, exports or real message content. Test fixtures must be synthetic.
- Pseudonymize user identifiers in exports and shareable views. Real usernames are visible only in the local dashboard.
- Skip bots (`is_bot`) and service messages (joins, pins) in relationship metrics, and count joins separately for retention analytics.
