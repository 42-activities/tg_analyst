import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tg_analyst.storage.db import make_engine, make_session_factory, migrate

# Tests need a PostgreSQL server. Locally: `docker compose up -d db`. The test database is
# created on first use and wiped before every test, so never point this at real data.
DEFAULT_TEST_URL = "postgresql+psycopg://tg_analyst:tg_analyst@localhost:5432/tg_analyst_test"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = make_url(os.environ.get("TEST_DATABASE_URL") or DEFAULT_TEST_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception as exc:
        pytest.exit(
            f"Cannot reach the test PostgreSQL at {url.render_as_string(hide_password=True)}: "
            f"{exc.__class__.__name__}. Start it with `docker compose up -d db` or set "
            "TEST_DATABASE_URL.",
            returncode=2,
        )
    finally:
        admin.dispose()
    return url.render_as_string(hide_password=False)


@pytest.fixture
def engine(database_url):
    """A test database with the schema built exactly as in production: by migrations."""
    eng = make_engine(database_url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    migrate(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with make_session_factory(engine)() as s:
        yield s


THEMES = {
    "visa": [
        "visa appointment at the embassy is booked",
        "the embassy asked for another visa form",
        "visa queue at the embassy took hours",
    ],
    "python": [
        "python release notes look good",
        "python packaging with the new release",
        "python typing in the release is great",
    ],
}


@pytest.fixture
def themed_group(session):
    """A group with reply-chain threads about two themes across two quarters.

    Returns (group_id, {theme: [root ids]}). Synthetic: no real names or messages.
    """
    from datetime import datetime, timedelta

    from tg_analyst.storage import repository as repo
    from tg_analyst.storage.records import GroupRecord, MessageRecord, UserRecord

    gid = -1009000000001
    repo.upsert_group(session, GroupRecord(gid, "Themed Group", "themed"))
    users = [UserRecord(500 + i, f"member{i}", f"Member Name {i}") for i in range(6)]
    repo.upsert_users(session, [(u, datetime(2026, 1, 1)) for u in users])
    records, roots, mid = [], {theme: [] for theme in THEMES}, 0
    for day in range(0, 180, 15):  # 12 days, two quarters
        for theme, lines in THEMES.items():
            ts = datetime(2026, 1, 2) + timedelta(days=day, hours=len(records) % 7)
            for i, line in enumerate(lines):
                mid += 1
                records.append(
                    MessageRecord(
                        gid,
                        mid,
                        ts + timedelta(minutes=10 * i),
                        sender_id=users[(mid + i) % 6].user_id,
                        text=f"@member1 {line}" if i == 1 else line,
                        reply_to_msg_id=mid - 1 if i else None,
                    )
                )
            roots[theme].append(mid - 2)
    repo.upsert_messages(session, records)
    session.commit()
    return gid, roots
