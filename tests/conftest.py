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
