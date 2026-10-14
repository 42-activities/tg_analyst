"""Headless smoke tests of the Streamlit app (no browser needed)."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from tg_analyst import synthetic  # noqa: E402
from tg_analyst.config import get_settings  # noqa: E402
from tg_analyst.enrich.pipeline import enrich  # noqa: E402
from tg_analyst.enrich.sentiment import VaderBackend  # noqa: E402
from tg_analyst.storage.db import init_db, make_engine, make_session_factory  # noqa: E402

APP = str(Path(__file__).parents[1] / "src/tg_analyst/dashboard/app.py")


@pytest.fixture
def db_url(engine, database_url, monkeypatch):
    # `engine` resets the test database; the app connects to it through DATABASE_URL.
    url = database_url
    monkeypatch.setenv("DATABASE_URL", url)
    _clear_caches()
    yield url
    _clear_caches()


def _clear_caches() -> None:
    # Streamlit caches live for the whole process; each test needs its own database engine.
    import streamlit as st

    get_settings.cache_clear()
    st.cache_resource.clear()
    st.cache_data.clear()


def _app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def test_dashboard_empty_state(db_url):
    at = _app()
    assert any("No data yet" in i.value for i in at.info)


def test_dashboard_with_data(db_url):
    eng = make_engine(db_url)
    init_db(eng)
    with make_session_factory(eng)() as s:
        synthetic.load(
            s,
            synthetic.generate(
                days=20,
                start=datetime.now().replace(microsecond=0) - timedelta(days=21),
            ),
        )
        enrich(s, VaderBackend())
    at = _app()
    assert at.title[0].value == "Synthetic Dev Community"
    assert [m.label for m in at.metric][:2] == ["Messages", "Active members"]
    # The feud is detected and its message chain is rendered in the Relationships tab.
    assert len(at.chat_message) > 10
    assert any("hostile" in c.value for c in at.caption)


def test_jobs_page_queues_backfill(db_url):
    at = AppTest.from_file(APP, default_timeout=30)
    at.switch_page("pages/jobs.py")
    at.run()
    assert not at.exception, at.exception
    at.text_input[0].input("https://t.me/some_group/")
    at.button[0].click().run()  # the form submit button comes first
    assert any("Queued job" in s.value and "some_group" in s.value for s in at.success)
    assert any("Nobody is excluded" in i.value for i in at.info)
