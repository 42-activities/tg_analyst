"""Engine and session helpers."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tg_analyst.storage.models import Base


def make_engine(url: str) -> Engine:
    """Create an engine for a PostgreSQL URL (``postgresql+psycopg://...``)."""
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        raise ValueError(f"Only PostgreSQL is supported, got {engine.dialect.name!r}")
    return engine


# Arbitrary constant identifying the schema-creation advisory lock.
_SCHEMA_LOCK_ID = 724_461_001


def init_db(engine: Engine) -> None:
    """Create missing tables. Safe when several processes start at once (web, worker...)."""
    with engine.begin() as conn:
        # Without this, concurrent CREATE TABLEs race on PostgreSQL's catalog.
        conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _SCHEMA_LOCK_ID})
        Base.metadata.create_all(conn)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
