"""Alembic environment. Runs on the connection handed over by ``storage.db.migrate``."""

from alembic import context

from tg_analyst.storage.models import Base

config = context.config
connection = config.attributes.get("connection")
if connection is None:  # pragma: no cover - `alembic revision --autogenerate` from the CLI
    from sqlalchemy import create_engine

    from tg_analyst.config import get_settings

    connection = create_engine(get_settings().database_url).connect()

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
