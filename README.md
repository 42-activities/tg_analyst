# TG Analyst — Telegram Community Intelligence Platform

An automated data pipeline and analytics dashboard that turns fast-moving Telegram group chats into insight about **people and their relationships**: who talks to whom, how members feel about each other and about the community, who holds the group together, and where friction is building.

Telegram groups host tech communities, customer support and local and professional networks, yet they have no native analytics. Thousands of messages disappear into the scroll history every day. TG Analyst extracts the raw conversations, enriches them with NLP and presents the results as interactive, relationship-centric analytics.

> **Focus:** the sentiment analysis here is about **user relationships and community dynamics**: member-to-member tone, how members treat newcomers, how admins and members interact, and whether conflict is rising. It does not measure market or crypto sentiment.

> **Status:** ingestion, enrichment and a first web UI work. See [`docs/ROADMAP.md`](docs/ROADMAP.md).

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
| 1. Ingestion | Telethon (MTProto) | Back-fills history, streams new messages, captures reply/mention/reaction edges and handles `FloodWaitError` rate limits. |
| 2. Storage | PostgreSQL 16 | Normalized tables for messages, users and interactions, plus enrichment results. |
| 3. Enrichment | VADER, HuggingFace (multilingual), optional LLM API | Cleans text, scores polarity (-1.0 to +1.0), flags toxicity, detects intent (question, thanks, complaint, greeting), extracts topics and builds the interaction graph (NetworkX). |
| 4. Presentation | Streamlit + Plotly | KPI cards, relationship network, sentiment timelines, heatmaps and the message explorer. |

### Core data model (draft)

- **`groups`**: `group_id`, `title`, `username`, `member_count`
- **`users`**: `user_id`, `username`, `display_name`, `is_bot`, `first_seen`, `last_seen`
- **`messages`**: `message_id`, `group_id`, `sender_id`, `timestamp`, `text`, `reply_to_msg_id`, `thread_id`, `views`, `forwards`, `has_media`, `media_type`
- **`interactions`**: `source_user_id`, `target_user_id`, `message_id`, `type` (`reply` | `mention` | `reaction` | `forward`), `timestamp`
- **`enrichment`**: `message_id`, `sentiment_score`, `sentiment_label`, `toxicity`, `intent`, `topics`, `model_version`

---

## Dashboard modules

### Community health and KPI overview
Message velocity, DAU/WAU/MAU, active vs. lurking members, media-to-text ratio, average replies per message and median time to first reply.

### Relationship network
- An interactive **who-talks-to-whom graph**: edge weight is interaction volume and edge colour is average tone.
- **Pair view**: the sentiment trend between two specific members.
- **Clusters and cliques**: subgroups detected with community detection (e.g. Louvain).
- **Bridges and isolates**: members who connect clusters, and members nobody replies to.

### Member profiles and roles
- Per-user tone: how a member **gives** sentiment and how they **receive** it.
- Role classification: *connector*, *helper* (answers questions), *conversation starter*, *lurker*, *critic*, *newcomer*.
- Influence ranking: most replied-to members, centrality scores and the members whose messages get the most responses.

### Sentiment and mood tracker
- Community tone over time as a rolling average, overlaid on message volume.
- Topic sentiment: which subjects bring the group together and which divide it.
- Conflict detection: bursts of negative reply chains between the same members.

### Newcomer and retention analytics
- Welcome quality: how fast a newcomer's first message gets a response, and in what tone.
- Cohort retention: are members who join in month X still active N weeks later?
- Churn signals: activity is falling off or the tone a member receives is turning negative.

### Activity heatmaps
Day-of-week × hour-of-day matrices that show peak hours, for the whole group or for one member.

### Message and thread explorer
A filterable table by keyword, date range, user, interaction pair, sentiment and intent, for example *negative replies directed at newcomers in the last 30 days*.

---

## Use cases

- **Community managers and moderators:** spot rising conflict early, see who welcomes newcomers and who drives them away, recognize and reward helpful members, and schedule announcements for peak hours.
- **Product and support teams:** see how users treat each other and the team, find unanswered questions and identify trusted community helpers.
- **Researchers:** study social structure, influence and group dynamics in online communities.

---

## Getting started

Development plan: [`docs/ROADMAP.md`](docs/ROADMAP.md). Done so far: ingestion and storage (M1), sentiment and intent enrichment (M2), and a web UI with a job queue and dashboards.

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

Open <http://localhost:8501>. On the **Jobs** page, enter a group (`@name`, `t.me/name` or an id) and click *Queue back-fill*. The worker fetches the history and runs sentiment analysis, and the results show up on the **Dashboard**. *Load demo data* gives you a synthetic community to explore without Telegram.

- **Remote server:** the UI has no login and listens on 127.0.0.1 only. Use an SSH tunnel: `ssh -L 8501:localhost:8501 <server>`.
- **Multilingual chats (RU/KK…):** set `SENTIMENT_BACKEND=hf` in `.env` and build with `docker compose build --build-arg EXTRAS=web,hf`. VADER, the default, understands English only.
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

The deploy needs these repository secrets: `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY` (its public half goes in `~/.ssh/authorized_keys` on the server) and `SSH_KNOWN_HOSTS`. The server's `.env` stays in place. The deploy also refuses to run if tracked files in the server checkout have uncommitted edits.

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
│   ├── analytics/         # loaders + pure metric functions
│   ├── dashboard/         # Streamlit app: pages/dashboard.py, pages/jobs.py
│   └── graph/             # (M3) centrality, communities, roles
├── tests/
├── docs/ROADMAP.md
├── Dockerfile, docker-compose.yml
├── .github/workflows/ci.yml
├── CLAUDE.md
└── README.md
```

---

## Privacy and ethics

This tool processes personal conversations, so:
- Analyze only groups you are a member of and are permitted to analyze. Respect Telegram's Terms of Service and local privacy law (e.g. GDPR).
- Never commit `.env`, `*.session` files or databases.
- Prefer **pseudonymized user IDs** in exports and shared dashboards.
- The analytics describe interaction patterns. Do not use them to profile or target individuals.

## License

TBD
