import os

import pytest

from tg_analyst.storage.db import make_engine, make_session_factory
from tg_analyst.storage.models import Base


@pytest.fixture
def engine(tmp_path):
    # CI also runs the suite against PostgreSQL by setting TEST_DATABASE_URL.
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    eng = make_engine(url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    with make_session_factory(engine)() as s:
        yield s
