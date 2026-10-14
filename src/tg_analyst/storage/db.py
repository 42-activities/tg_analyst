"""Engine and session helpers."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    """Create an engine for a PostgreSQL URL (``postgresql+psycopg://...``)."""
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        raise ValueError(f"Only PostgreSQL is supported, got {engine.dialect.name!r}")
    return engine


# Arbitrary constant identifying the schema-creation advisory lock.
_SCHEMA_LOCK_ID = 724_461_001


# Revision matching the schema that existed before migrations were introduced.
BASELINE_REVISION = "0001_baseline"


def _alembic_config(conn) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    cfg.attributes["connection"] = conn
    return cfg


def migrate(engine: Engine) -> None:
    """Bring the schema to the latest Alembic revision.

    Safe when several processes start at once (web, worker…): an advisory lock
    serializes them. A database created before migrations existed (tables present, no
    ``alembic_version``) is stamped at the baseline first, so nothing is recreated.
    """
    with engine.begin() as conn:
        # Without this, concurrent DDL races on PostgreSQL's catalog.
        conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _SCHEMA_LOCK_ID})
        cfg = _alembic_config(conn)
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and "messages" in tables:
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")


def init_db(engine: Engine) -> None:
    """Create or upgrade the schema (alias of :func:`migrate`, kept for callers)."""
    migrate(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
