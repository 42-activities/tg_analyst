"""Alembic migrations: fresh databases, pre-migration databases (like production), reruns."""

from datetime import datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from tg_analyst.storage import repository as repo
from tg_analyst.storage.db import (
    BASELINE_REVISION,
    _alembic_config,
    make_engine,
    make_session_factory,
    migrate,
)
from tg_analyst.storage.models import Base, Message
from tg_analyst.storage.records import GroupRecord, MessageRecord

# Tables introduced by revisions after 0001_baseline.
LATER_TABLES = {"excluded_users", "reactions"}
# Columns that later revisions added to baseline-era tables.
LATER_COLUMNS = {
    "messages": ["reaction_count"],
    "enrichment": ["insult", "obscenity", "threat"],
    "jobs": ["progress"],
}


@pytest.fixture
def blank_engine(database_url):
    """A separate, completely empty database for each test."""
    url = make_url(database_url).set(database="tg_analyst_migration_test")
    admin = create_engine(
        make_url(database_url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    eng = make_engine(url.render_as_string(hide_password=False))
    yield eng
    eng.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    admin.dispose()


def _current_revision(engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _schema_diff(engine) -> list:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)


def test_fresh_database_gets_the_full_schema(blank_engine):
    migrate(blank_engine)
    assert _schema_diff(blank_engine) == []
    with blank_engine.connect() as conn:
        head = ScriptDirectory.from_config(_alembic_config(conn)).get_current_head()
    assert _current_revision(blank_engine) == head


def test_pre_migration_database_is_stamped_and_keeps_its_data(blank_engine):
    # What production looked like before Alembic: the baseline-era tables from create_all,
    # no version table. Tables added by later revisions must not exist yet.
    baseline_tables = [t for t in Base.metadata.sorted_tables if t.name not in LATER_TABLES]
    Base.metadata.create_all(blank_engine, tables=baseline_tables)
    with blank_engine.begin() as conn:
        for table, columns in LATER_COLUMNS.items():
            for column in columns:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    with make_session_factory(blank_engine)() as s:
        repo.upsert_group(s, GroupRecord(-1, "g"))
        repo.upsert_messages(
            s, [MessageRecord(-1, 1, datetime(2026, 1, 1), sender_id=1, text="kept")]
        )
        s.commit()

    migrate(blank_engine)

    assert _current_revision(blank_engine) is not None
    assert _schema_diff(blank_engine) == []
    with make_session_factory(blank_engine)() as s:
        assert s.scalar(select(func.count()).select_from(Message)) == 1


def test_migrate_is_idempotent(blank_engine):
    migrate(blank_engine)
    first = _current_revision(blank_engine)
    migrate(blank_engine)
    assert _current_revision(blank_engine) == first


def test_baseline_revision_exists(blank_engine):
    with blank_engine.connect() as conn:
        script = ScriptDirectory.from_config(_alembic_config(conn))
    assert script.get_revision(BASELINE_REVISION) is not None
