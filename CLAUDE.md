# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project summary

TG Analyst is a Telegram community intelligence platform with four stages: **ingest** (Telethon) → **store** (SQLite, later PostgreSQL) → **enrich** (sentiment, intent, interaction graph) → **visualize** (Streamlit + Plotly). See `README.md` for the full design.

**Domain focus:** the analysis is about **user relationships and community dynamics**: who interacts with whom, the tone between members, how newcomers are treated, member roles, conflict and churn. It is **not** a crypto, market or ticker sentiment tool. Do not add finance-specific features (tickers, price correlation, hype tracking) unless asked.

The project is at an early stage. If a module described in the README doesn't exist yet, create it following the planned layout below.

## Tech stack

- Python 3.11+ with a `src/` layout. The package is `tg_analyst`.
- **Telethon** for the MTProto client. It is async-first.
- **SQLAlchemy 2.x** with SQLite by default. Keep the code portable to PostgreSQL: no SQLite-only SQL.
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
pip install -e ".[dev]"                                    # install
pytest                                                     # tests
ruff check . && ruff format .                              # lint + format
python -m tg_analyst.ingest backfill --group <g>           # back-fill history
python -m tg_analyst.ingest listen --group <g>             # live stream
python -m tg_analyst.enrich run                            # enrichment
streamlit run src/tg_analyst/dashboard/app.py              # dashboard
```

Update this section when the real entry points change.

## Architecture rules

- **Keep the layers separate.** The dashboard reads from storage and `analytics/` only. It never calls Telethon or runs model inference. Enrichment is a separate batch step that writes its results back to the database.
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
