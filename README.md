# TG Analyst — Telegram Community Intelligence Platform

An automated data pipeline and analytics dashboard that turns fast-moving Telegram group chats into insight about **people and their relationships**: who talks to whom, how members feel about each other and about the community, who holds the group together, and where friction is building.

Telegram groups host tech communities, customer support and local and professional networks, yet they have no native analytics. Thousands of messages disappear into the scroll history every day. TG Analyst extracts the raw conversations, enriches them with NLP and presents the results as interactive, relationship-centric analytics.

> **Focus:** the sentiment analysis here is about **user relationships and community dynamics**: member-to-member tone, how members treat newcomers, how admins and members interact, and whether conflict is rising. It does not measure market or crypto sentiment.

> **Status:** Milestone 1 (ingestion and storage) is done. See [`docs/ROADMAP.md`](docs/ROADMAP.md).

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
│  (Telethon)  │   │ (SQLite→PG)  │   │ sentiment, graph │   │ Streamlit+Plotly │
└──────────────┘   └──────────────┘   └──────────────────┘   └──────────────────┘
```

| Layer | Component | Responsibility |
|---|---|---|
| 1. Ingestion | Telethon (MTProto) | Back-fills history, streams new messages, captures reply/mention/reaction edges and handles `FloodWaitError` rate limits. |
| 2. Storage | SQLite (default), PostgreSQL (scale) | Normalized tables for messages, users and interactions, plus enrichment results. |
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

Development plan: [`docs/ROADMAP.md`](docs/ROADMAP.md). **Milestone 1** is implemented: storage, ingestion, the synthetic data generator, Docker and CI/CD. Enrichment, analytics and the dashboard come next.

### Prerequisites
- Docker with Compose v2, **or** Python 3.11+
- Telegram API credentials (`api_id`, `api_hash`) from <https://my.telegram.org>. Not needed for synthetic data.
- A Telegram account that is a member of the groups you want to analyze

### Run with Docker (recommended)

```bash
cp .env.example .env                      # fill in TG_API_ID, TG_API_HASH, TG_PHONE
docker compose up -d db                   # PostgreSQL 16 (POSTGRES_PORT to change host port)
docker compose run --rm app seed          # load a synthetic community, no Telegram needed
docker compose run --rm app backfill --group <username_or_id> --since 2026-01-01
TG_GROUP=<username_or_id> docker compose --profile live up -d listener
```

The first `backfill` asks for the Telegram login code interactively. The resulting session file is stored in the `tgdata` volume.

### Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                   # add ,postgres for PostgreSQL
cp .env.example .env                      # defaults to SQLite at data/tg_analyst.db

tg-analyst seed                           # synthetic community (48 users, ~4k messages)
tg-analyst backfill --group <g> [--since 2026-01-01] [--limit N]   # resumable
tg-analyst listen --group <g>             # stream new messages
tg-analyst build-edges                    # re-derive reply/mention interactions
pytest && ruff check .
```

### CI/CD

GitHub Actions (`.github/workflows/ci.yml`) runs on every push and pull request:
1. **lint:** `ruff check` and `ruff format --check`
2. **test:** pytest on Python 3.11 and 3.12 × SQLite and PostgreSQL
3. **docker:** builds the image and smoke-tests it with `seed`. On `main` and `v*` tags it then pushes to `ghcr.io/42-activities/tg_analyst` with tags `latest`, `main`, `sha-<short>` and semver.

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
│   ├── enrich/            # (M2) cleaning, sentiment, toxicity, intent, topics
│   ├── graph/             # (M3) interaction graph, centrality, communities, roles
│   ├── analytics/         # (M3) KPI and metric computations (pure functions)
│   └── dashboard/         # (M4) Streamlit app and pages
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
