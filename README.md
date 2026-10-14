# TG Analyst — Telegram Community Intelligence Platform

An automated data pipeline and analytics dashboard that turns fast-moving Telegram group chats into insight about **people and their relationships**: who talks to whom, how members feel about each other and about the community, who holds the group together, and where friction is building.

Telegram groups host tech communities, customer support and local and professional networks, yet they have no native analytics. Thousands of messages disappear into the scroll history every day. TG Analyst extracts the raw conversations, enriches them with NLP and presents the results as interactive, relationship-centric analytics.

> **Focus:** the sentiment analysis here is about **user relationships and community dynamics**: member-to-member tone, how members treat newcomers, how admins and members interact, and whether conflict is rising. It does not measure market or crypto sentiment.

> **Status:** in production at <https://tg-analyst.naurzalinov.me> (password-protected). Ingestion, Russian-capable sentiment, relationship analytics and the dashboard all work. Next up: a per-member page, reactions, topics, then "ask the chat" (RAG). See [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Core objectives

1. **Automate data extraction.** Ingest historical and real-time messages, reply chains, mentions, reactions, forwards, views and media indicators from target groups with Telethon, a client for Telegram's MTProto API.
2. **Map relationships.** Build an interaction graph from replies, mentions and reactions, then use it to find each member's role in the community, its clusters and its isolated members.
3. **Quantify relational sentiment.** Classify each message as positive, neutral or negative, and **attribute that tone to the interaction**: how A speaks to B, how the community responds to a given member, and how the tone between two members changes over time.
4. **Track community health.** Measure how newcomers are welcomed and retained, how fast questions get answered, how toxicity spikes and how members churn.
5. **Deliver visual intelligence.** Show everything in a Streamlit + Plotly dashboard with filters for date, group, user and keyword.

---

## Architecture

```
┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  Ingestion   │──▶│   Storage    │──▶│  NLP & Enrichment│──▶│  Presentation    │
│  (Telethon)  │   │ (PostgreSQL) │   │ sentiment, graph │   │ Streamlit+Plotly │
└──────────────┘   └──────────────┘   └──────────────────┘   └──────────────────┘
```

| Layer | Component | Responsibility |
|---|---|---|
| 1. Ingestion | Telethon (MTProto) | Resumable history back-fill and a live listener. Builds reply and mention edges and waits out `FloodWaitError` rate limits. |
| 2. Storage | PostgreSQL 16 | Normalized tables for messages, users and interactions, plus enrichment results and a job queue. |
| 3. Enrichment | Transformer sentiment (Russian or multilingual model), VADER for English | Cleans text, scores tone (-1.0 to +1.0) and detects intent (question, thanks, complaint, greeting). Toxicity and topics are planned. |
| 4. Analytics | pandas + NetworkX (pure functions) | Interaction graph, centrality and communities, member roles, newcomer outcomes, conflict episodes. |
| 5. Presentation | Streamlit + Plotly | Dashboard tabs and a Jobs page that queues work for a background worker. |

### Core data model

- **`groups`**: `group_id`, `title`, `username`, `member_count`
- **`users`**: `user_id`, `username`, `display_name`, `is_bot`, `first_seen`, `last_seen`
- **`messages`**: `message_id`, `group_id`, `sender_id`, `timestamp`, `text`, `reply_to_msg_id`, `thread_id`, `views`, `forwards`, `has_media`, `media_type`
- **`interactions`**: `source_user_id`, `target_user_id`, `message_id`, `type` (`reply` | `mention` | `reaction` | `forward`), `timestamp`
- **`enrichment`**: `message_id`, `sentiment_score`, `sentiment_label`, `toxicity`, `intent`, `topics`, `model_version`
- **`jobs`**: work queued from the web UI (`backfill`, `enrich`, `build_edges`, `seed`) and run by the worker

---

## Dashboard

What the dashboard shows today. ⏳ marks parts that are planned but not built yet.

### Overview (top of the page)
Messages, active members, daily and weekly active members, average tone, share of negative messages, new members, typical wait for a reply, share of replies and of media. The period defaults to the group's whole history.

### Activity & mood
- Daily message volume with a 7-day rolling tone line
- A day-of-week × hour heatmap of when the community is active
- Message types (question, thanks, greeting, complaint) and their tone

### Members
- Per member: messages, how many people they talk to, replies and mentions received, **tone given** (their own messages) vs. **tone received** (what others say to them)
- **Role** for each member: *helper* (answers questions in a friendly tone), *connector* (bridges sub-groups), *starter* (starts threads others join), *critic*, *lurker*, *newcomer*, *regular*. Thresholds are relative to the group, so the roles adapt to its size and tone.
- A scatter plot of tone given vs. tone received

### Network
- A who-talks-to-whom map: dot size = attention received, colour = sub-group (Louvain communities), line colour = tone of the pair
- The top connectors, and members nobody ever replied to

### Relationships
- The strongest pairs, with tone in each direction and alerts for tense pairs
- **Conflict episodes:** bursts of hostile exchanges between two members, either mutual (a feud) or one-sided. "Hostile" is relative to the group: its most negative 10% of messages, so rough-but-friendly chats aren't flagged everywhere. **Click an episode to read the actual message chain.**
- Weekly tone between any two members

### Newcomers
Whether a newcomer's first message got an answer, how fast and in what tone, and whether answered vs. unanswered newcomers were still active 14 days later. This needs join events, which large groups sometimes hide from non-admins.

### Messages
Search by text, tone and message type.

### ⏳ Planned
A per-member drill-down page, topic extraction with tone per topic, reactions as interactions and toxicity scores. Later: "ask the chat" (RAG) over the group's Q&A, see M6 in the roadmap.

### Staying in sync with Telegram
The worker keeps every scraped group up to date on its own. It fetches new messages and scores them every `AUTO_REFRESH_MINUTES` (default 60). Every `AUTO_RECONCILE_HOURS` (default 24) it re-checks the last `RECONCILE_DAYS` (default 30) of stored messages: messages deleted in Telegram are deleted here too, and edited ones are updated and re-scored. A full-history re-check can be started from the Jobs page.

---

## Use cases

- **Community managers and moderators:** spot rising conflict early, see who welcomes newcomers and who drives them away, recognize and reward helpful members, and schedule announcements for peak hours.
- **Product and support teams:** see how users treat each other and the team, find unanswered questions and identify trusted community helpers.
- **Researchers:** study social structure, influence and group dynamics in online communities.

---

## Getting started

Development plan: [`docs/ROADMAP.md`](docs/ROADMAP.md). Done so far: ingestion and storage (M1), sentiment and intent (M2), relationship analytics (M3) and most of the dashboard (M4).

### Prerequisites
- Docker with Compose v2 (Python 3.12+ too, if you run outside Docker)
- Telegram API credentials (`api_id`, `api_hash`) from <https://my.telegram.org>. Not needed for demo data.
- A Telegram account that is a member of the groups you want to analyze

### Run with Docker (recommended)

```bash
cp .env.example .env                      # fill in TG_API_ID, TG_API_HASH, TG_PHONE
docker compose run --rm app login         # once: enter the code Telegram sends you
docker compose up -d                      # PostgreSQL + web UI + worker
```

Open <http://localhost:8501>. On the **Jobs** page, enter a group (`@name`, a `t.me/…` link, a Telegram Web link such as `web.telegram.org/k/#-1774464434`, or a numeric id) and click *Queue back-fill*. Set *Max messages* to `0` to fetch the whole history. Re-running resumes after the last fetched message, so it never duplicates anything. The worker fetches the history and runs sentiment analysis, and the results show up on the **Dashboard**. *Load demo data* gives you a synthetic community to explore without Telegram.

- **Remote server:** the UI has no login and listens on 127.0.0.1 only. Use an SSH tunnel: `ssh -L 8501:localhost:8501 <server>`.
- **Sentiment model:** the image includes CPU-only torch + transformers. Production uses `SENTIMENT_BACKEND=hf` with `cointegrated/rubert-tiny-sentiment-balanced`, a small Russian model (~70 msg/s on this server). For other languages, set `HF_SENTIMENT_MODEL=cardiffnlp/twitter-xlm-roberta-base-sentiment`, which is multilingual but ~18× slower and needs ~1.4 GB RAM. `vader` works for English only. Changing the model re-scores messages on the next enrichment run.
- **Live updates:** `TG_GROUP=<group> docker compose --profile live up -d listener`. Don't run it while a back-fill job is running, because both share one Telegram session.
- **Ports in use:** set `WEB_PORT` / `POSTGRES_PORT`.

### Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,web]"               # add ,hf for the multilingual model
cp .env.example .env                      # points at the Compose PostgreSQL on localhost:5432
docker compose up -d db                   # the only database supported

tg-analyst login                          # once
tg-analyst web & tg-analyst worker        # UI on http://localhost:8501

# or drive everything from the CLI:
tg-analyst seed                           # synthetic community
tg-analyst backfill --group <g> [--since 2026-01-01] [--limit N]
tg-analyst enrich [--backend vader|hf]    # sentiment + intent for new messages
tg-analyst listen --group <g>
pytest && ruff check .            # tests create and wipe a tg_analyst_test database
```

### CI/CD

`.github/workflows/pipeline.yml` runs on every pull request and every push to `main`:

1. **CI** (`ci.yml`):
   - `ruff`
   - `pytest` on Python 3.12 against a PostgreSQL 16 service
   - a Docker smoke test that builds the image and runs `seed` against PostgreSQL
2. **Release** (`docker-deploy.yml`, a copy of the shared `bakyt92/ci-templates` workflow):
   - **Build:** an **arm64** image goes to `ghcr.io/42-activities/tg_analyst-app:<commit sha>` (plus `latest`).
   - **Deploy:** SSH to the server, check out the commit in `~/projects/tg_analyst`, pull the image, then `docker compose up -d --no-build`.
   - **Health check and rollback:** it waits for `http://127.0.0.1:8501/_stcore/health`. If that fails, it rolls back to the previous commit and image.

Pull requests build the image but never push or deploy.

The deploy needs these repository secrets:
- `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY` (its public half goes in `~/.ssh/authorized_keys` on the server) and `SSH_KNOWN_HOSTS`
- `ENV_FILE`: the complete production `.env` (Telegram credentials, `POSTGRES_PASSWORD`, `SENTIMENT_BACKEND`…). **GitHub owns the server's `.env`.** Every deploy overwrites it with this secret, so manual edits on the server are lost. To change config:
  ```bash
  gh secret set ENV_FILE < .env.production   # a local copy, never committed
  gh workflow run pipeline.yml               # redeploy to apply it
  ```
  Keep `POSTGRES_PASSWORD` unchanged: the existing database user was created with it.

The deploy refuses to run if tracked files in the server checkout have uncommitted edits.

The Telegram login session is not configuration. It lives in the `tgdata` Docker volume, survives deploys, and is created once with `docker compose run --rm app login`.

### Production

- The site is <https://tg-analyst.naurzalinov.me>, behind host nginx with HTTP basic auth. See `deploy/nginx/`.
- HTTPS certificates come from certbot.

---

## Project structure

```
tg_analyst/
├── src/tg_analyst/
│   ├── cli.py             # `tg-analyst` entry point
│   ├── config.py          # settings loaded from .env
│   ├── synthetic.py       # deterministic synthetic community
│   ├── ingest/            # Telethon → records conversion, backfill, live listener
│   ├── storage/           # SQLAlchemy models, upserts, interaction-edge builder
│   ├── jobs.py            # DB-backed job queue + worker
│   ├── enrich/            # cleaning, sentiment backends (VADER / HF), intent
│   ├── analytics/         # loaders + pure functions: metrics, newcomers, conflict
│   ├── graph/             # interaction graph, centrality, communities, roles
│   └── dashboard/         # Streamlit app: pages/dashboard.py, pages/jobs.py
├── tests/
├── docs/ROADMAP.md
├── Dockerfile, docker-compose.yml
├── .github/workflows/     # pipeline.yml → ci.yml + docker-deploy.yml
├── deploy/nginx/          # production nginx site (basic auth, WebSocket proxy)
├── CLAUDE.md
└── README.md
```

---

## Privacy and ethics

This tool processes personal conversations, so:
- Analyze only groups you are a member of and are permitted to analyze. Respect Telegram's Terms of Service and local privacy law (e.g. GDPR).
- Never commit `.env`, `*.session` files or databases.
- Prefer **pseudonymized user IDs** in exports and shared dashboards.
- **Members can opt out.** On the Jobs page (*Privacy: excluded members*) or with `tg-analyst exclude --user @name`, a member's messages are deleted along with the replies and mentions pointing at them, and every future back-fill skips them. Don't delete rows by hand: a re-scrape would bring them back.
- The analytics describe interaction patterns. Do not use them to profile or target individuals.

## License

TBD
