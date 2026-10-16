# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project summary

TG Analyst is a Telegram community intelligence platform with four stages: **ingest** (Telethon) → **store** (PostgreSQL) → **enrich** (sentiment, intent, interaction graph) → **visualize** (Streamlit + Plotly). See `README.md` for the full design.

**Domain focus:** the analysis is about **user relationships and community dynamics**: who interacts with whom, the tone between members, how newcomers are treated, member roles, conflict and churn. It is **not** a crypto, market or ticker sentiment tool. Do not add finance-specific features (tickers, price correlation, hype tracking) unless asked.

Milestones 1–3 (ingestion, enrichment, relationship analytics) and most of the M4 dashboard are implemented and running in production. The remaining work is in `docs/ROADMAP.md` (M4 drill-down, M5 hardening). If a module from the planned layout doesn't exist yet, create it where the layout below puts it.

## Tech stack

- Python 3.12+ with a `src/` layout. The package is `tg_analyst`.
- **Telethon** for the MTProto client. It is async-first.
- **SQLAlchemy 2.x** on **PostgreSQL only** (psycopg 3). SQLite was deliberately dropped, so PostgreSQL features (`ON CONFLICT`, advisory locks, JSON) are fine to use.
- **pandas** for analytics and **NetworkX** for the interaction graph.
- Sentiment backends: **VADER** (English only) and **HuggingFace** transformers. Production chats are mostly Russian, so production uses `cointegrated/rubert-tiny-sentiment-balanced` (~70 msg/s on the arm64 server, ~0.5 GB RAM). `cardiffnlp/twitter-xlm-roberta-base-sentiment` is the multilingual alternative, but it's ~18× slower and needs ~1.4 GB RAM; the server has little to spare. Toxicity uses `cointegrated/rubert-tiny-toxicity` (~50 MB, ~60 msg/s): separate insult/obscenity/threat probabilities. It runs automatically with the `hf` sentiment backend (`enrich.toxicity.backends`), and its version is part of `model_version`. An LLM API is an optional, pluggable backend.
- **Streamlit + Plotly** for the dashboard.
- **pytest** for tests and **ruff** for lint and format.

## Planned layout

```
src/tg_analyst/
  config.py      # pydantic-settings; reads .env
  ingest/        # Telethon client, backfill, live listener
  storage/       # ORM models, repositories, migrations (alembic)
  enrich/        # text cleaning, sentiment, toxicity, intent, topics
  graph/         # network.py (graph, centrality, communities), roles.py
  analytics/     # load.py (only I/O) + pure functions: metrics, newcomers, conflict, members, topics
  dashboard/     # Streamlit app.py + views/
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

- **Never develop in `~/projects/tg_analyst`.** It's the production checkout: the deploy runs `git checkout` there and aborts if tracked files have uncommitted edits. Work in the git worktree `~/projects/tg_analyst-dev` (own `.venv`) on a feature branch, then fast-forward `main` and push.
- Run tests against a throwaway PostgreSQL, not the production compose DB: `docker run -d --rm --name tga_testpg -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -p 127.0.0.1:55433:5432 postgres:16-alpine`, then `TEST_DATABASE_URL=postgresql+psycopg://test:test@127.0.0.1:55433/tg_analyst_test pytest`.
- Check analytics changes against the synthetic ground truth (tests) **and** the real production data (read-only, aggregates only, never print message text or names). Rules tuned on synthetic data have broken on the real chat before: a fixed -0.3 hostility cut-off flagged 234 "conflicts" in a rough-but-friendly Russian chat.
- Writing GitHub secrets (`gh secret set`) and adding SSH keys are blocked for Claude. Prepare the change, then give the user the exact command to run.

- Plan and progress are tracked in `docs/ROADMAP.md`. Tick items off as they land.
- `pipeline.yml` runs `ci.yml` (ruff, pytest on Python 3.12 + PostgreSQL 16, Docker smoke test), then `docker-deploy.yml`. **Every push to `main` deploys to production** (arm64 image → GHCR → SSH → `docker compose up`, with health check and rollback). Keep CI green: run `ruff check`, `ruff format --check` and `pytest` before committing.
- `docker-deploy.yml` is a verbatim copy of `bakyt92/ci-templates`. Change it there first, then re-copy it.
- Production is `~/projects/tg_analyst` on the arm64 server, served at https://tg-analyst.naurzalinov.me through host nginx with basic auth (`deploy/nginx/`). Never commit `.env` or credentials. The repo is **public**.
- Production config comes from the `ENV_FILE` repository secret, which the deploy writes to the server's `.env`. Don't tell the user to edit `.env` on the server: the next deploy overwrites it. New settings go into `ENV_FILE` (and `.env.example`).
- Use `repository._insert()` (PostgreSQL `INSERT ... ON CONFLICT`) for upserts, and chunk multi-row writes with `_chunks()`.
- Develop analytics and dashboard features against `tg-analyst seed`. Its ground truth (helpers, the FeudA/FeudB pair, ignored newcomers who churn) is meant to be asserted in tests.
- New dependencies go in `pyproject.toml`. Heavy ML dependencies belong in optional extras (e.g. `[hf]`), so the base Docker image stays slim.

## Architecture rules

- **Keep the layers separate.** The dashboard reads from storage and `analytics/` only. It never calls Telethon or runs model inference. Work the UI triggers goes through the `jobs` table and is executed by `tg-analyst worker` (`jobs.RUNNERS`). Runners take a `report(fraction, detail)` callback (`tg_analyst.progress`); pass it down to long loops so the Jobs page can show progress. Enrichment is a separate batch step that writes its results back to the database.
- **Streamlit pages** live in `dashboard/views/*.py` (deliberately not `pages/`: Streamlit auto-discovers that folder and shows raw file names in the sidebar on deep links), with shared cached resources in `dashboard/common.py`. `tests/test_web.py` smoke-tests them headlessly with `AppTest`.
- **Schema changes go through Alembic.** Change `storage/models.py`, then generate a revision with `alembic revision --autogenerate --rev-id 000N_short_name -m "…"` against a scratch database that has been migrated to head. Review the generated file and add the new table names to `LATER_TABLES` in `tests/test_migrations.py`. Never build schemas with `create_all()`. `storage.db.migrate()` runs on web/worker start under an advisory lock, and stamps a pre-migration database (production before 2026-10-14) at `0001_baseline`.
- **The database mirrors Telegram.** Deletions and edits are applied by the reconcile job (`ingest.service.reconcile`, scheduled by `jobs.schedule_due`) and by the listener. Anything derived from messages (enrichment, edges, and future chunks/embeddings) must cascade from `messages` or be rebuilt when a message changes.
- **Excluded members** (`excluded_users`) are a privacy opt-out: ingestion skips them, and `repo.exclude_user()` purges their messages and the edges pointing at them. Use it instead of deleting rows by hand, which a re-scrape would undo.
- **The interaction edge is the core unit.** Replies, mentions, reactions and forwards each become a row in `interactions` (`source_user_id → target_user_id`). Relationship metrics such as pair sentiment, centrality and roles derive from these edges joined with message sentiment, so keep edge extraction correct and complete. A reaction edge's `message_id` is the message reacted *to*, written by the target, so its tone is the emoji's (`enrich/reactions.py`, applied in `load.interactions`), never the message's sentiment. Use `conflict.written(edges)` where only written interactions make sense ("talks to", hostility threshold, transcripts).
- **Hostility ≠ negative tone.** Use `conflict.hostile_mask()`: insults and threats are hostile, swearing alone is not, and tone counts only below the group-relative threshold. On the real chat only ~3% of negative-sentiment messages are insults.
- **Thresholds are relative to the group.** Roles use percentiles and medians, and conflict "hostility" is the group's most negative 10% (never above -0.3). Don't hard-code absolute tone cut-offs: chats differ hugely in baseline tone.
- **User content is untrusted.** Render message text with `escape_md()` (no Markdown, links or images from chat text), never with `unsafe_allow_html`. When pseudonymization is on, mask @usernames inside text as well as names.
- **Sentiment is directional.** Store a message's sentiment once, then attribute it to the edge. "Sentiment given" (as sender) and "sentiment received" (as target) are different metrics, so don't conflate them.
- **Ingestion must be idempotent.** Upsert on `(group_id, message_id)`. Track a per-group high-water mark (`last_message_id`) so back-fills can resume and incremental runs stay cheap.
- **Handle rate limits.** Catch Telethon's `FloodWaitError` and sleep for `e.seconds`. Never use tight loops against the API.
- **Version the models.** Store `model_version` with each enrichment row so a model change can trigger re-scoring.
- **Pure analytics.** Functions in `analytics/` and `graph/` take DataFrames or graphs and return results with no I/O, which makes them easy to unit test.
- **Context topics** (`docs/CONTEXT_TOPICS.md`) are derived data: `analytics/threads.py` builds threads (reply chains, same-author bursts, hubs split), the `topics` job (`enrich/topics_job.py`) embeds new or changed threads (`content_key`), clusters them (`analytics/context_topics.py`, average linkage, never more than `MAX_POINTS` per distance matrix) and replaces the group's `context_topics`. `thread_embeddings` cascade from their root message; `exclude_user` clears the group's topic rows. Thread text sent to a model always goes through `thread_text` (letters for speakers, mentions masked), and model output is rendered with `show_text`/`escape_md`.
- **Topics are keywords** (`enrich/keywords.py`: Snowball stems, RU/EN/FR stopwords, plus `GENERIC_WORDS` filtered by stem). They're computed from message text when the dashboard loads (`analytics.topics.message_terms`, about 3 s for 36k messages, cached), not stored, so tuning the stopwords needs no re-scoring. If a generic word tops the real group's topic list, add it to `GENERIC_WORDS`. Tone per topic is shown relative to the group (`tone_vs_group`).
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
