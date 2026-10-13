# TG Analyst — Telegram Community Intelligence Platform

An automated data pipeline and analytics dashboard that turns fast-moving Telegram group chats into insight about **people and their relationships**: who talks to whom, how members feel about each other and about the community, who holds the group together, and where friction is building.

Telegram groups host tech communities, customer support and local and professional networks, yet they have no native analytics. Thousands of messages disappear into the scroll history every day. TG Analyst extracts the raw conversations, enriches them with NLP and presents the results as interactive, relationship-centric analytics.

> **Focus:** the sentiment analysis here is about **user relationships and community dynamics**: member-to-member tone, how members treat newcomers, how admins and members interact, and whether conflict is rising. It does not measure market or crypto sentiment.

> **Status:** early stage. This document describes the target design.

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

## Getting started (planned)

### Prerequisites
- Python 3.11+
- Telegram API credentials (`api_id`, `api_hash`) from <https://my.telegram.org>
- A Telegram account that is a member of the groups you want to analyze

### Setup

```bash
git clone <repo-url> tg_analyst && cd tg_analyst
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in TG_API_ID, TG_API_HASH, TG_PHONE
```

### Usage

```bash
# 1. Back-fill history for a group
python -m tg_analyst.ingest backfill --group <username_or_id> --since 2025-01-01

# 2. Stream new messages
python -m tg_analyst.ingest listen --group <username_or_id>

# 3. Run enrichment (sentiment, intent, interaction graph)
python -m tg_analyst.enrich run

# 4. Launch the dashboard
streamlit run src/tg_analyst/dashboard/app.py
```

---

## Project structure (planned)

```
tg_analyst/
├── src/tg_analyst/
│   ├── config.py          # settings loaded from .env
│   ├── ingest/            # Telethon client, backfill, live listener
│   ├── storage/           # SQLAlchemy models, repositories, migrations
│   ├── enrich/            # cleaning, sentiment, toxicity, intent, topics
│   ├── graph/             # interaction graph, centrality, communities, roles
│   ├── analytics/         # KPI and metric computations (pure functions)
│   └── dashboard/         # Streamlit app and pages
├── tests/
├── data/                  # local DB and session files (git-ignored)
├── .env.example
├── pyproject.toml
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
