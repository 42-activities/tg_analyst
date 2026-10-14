"""Headless smoke tests of the Streamlit app (no browser needed)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from sqlalchemy import update  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from tg_analyst import jobs, synthetic  # noqa: E402
from tg_analyst.config import get_settings  # noqa: E402
from tg_analyst.enrich.pipeline import enrich  # noqa: E402
from tg_analyst.enrich.sentiment import VaderBackend  # noqa: E402
from tg_analyst.storage.db import init_db, make_engine, make_session_factory  # noqa: E402
from tg_analyst.storage.models import Job  # noqa: E402

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
    assert any(sub.value == "Topic details" for sub in at.subheader)
    reactions = next(m for m in at.metric if m.label == "Reactions")
    assert int(reactions.value.replace(",", "")) > 100
    # Replies and mentions only: the page still renders.
    at.sidebar.toggle[1].set_value(False).run()
    assert not at.exception, at.exception


def test_jobs_page_queues_backfill(db_url):
    at = AppTest.from_file(APP, default_timeout=30)
    at.switch_page("views/jobs.py")
    at.run()
    assert not at.exception, at.exception
    at.text_input[0].input("https://t.me/some_group/")
    at.button[0].click().run()  # the form submit button comes first
    assert any("Queued job" in s.value and "some_group" in s.value for s in at.success)
    assert any("Nobody is excluded" in i.value for i in at.info)


def test_member_page(db_url):
    eng = make_engine(db_url)
    init_db(eng)
    with make_session_factory(eng)() as s:
        community = synthetic.generate(
            days=20, start=datetime.now().replace(microsecond=0) - timedelta(days=21)
        )
        synthetic.load(s, community)
        enrich(s, VaderBackend())
    feud_a = next(u.user_id for u in community.users if u.username == "feuda")
    at = AppTest.from_file(APP, default_timeout=60)
    at.switch_page("views/member.py")
    at.query_params["user"] = str(feud_a)
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "feuda"
    assert "first message" in at.caption[0].value  # role, community and dates line
    assert any(sub.value == "Conflict episodes" for sub in at.subheader)


def test_show_text_handles_media_only_messages():
    from tg_analyst.dashboard.common import NO_TEXT, show_text

    assert show_text(float("nan")) == NO_TEXT  # pandas NaN for a missing text
    assert show_text(None) == NO_TEXT and show_text("") == NO_TEXT
    assert show_text("@bob [x](y)", hide_names=True) == "@user \\[x\\]\\(y\\)"
    assert show_text("abcdef", limit=3) == "abc"


def test_jobs_page_shows_progress(db_url):
    eng = make_engine(db_url)
    init_db(eng)
    factory = make_session_factory(eng)
    with factory() as s:
        jobs.enqueue(s, "backfill", {"group": "big_group"})
        running = jobs.claim_next(s)
        jobs.enqueue(s, "enrich")
        s.execute(
            update(Job)
            .where(Job.id == running.id)
            .values(
                started_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10),
                progress={
                    "fraction": 0.25,
                    "detail": "40,500 messages stored, up to 2018-08-08",
                    "updated": datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
                },
            )
        )
        s.commit()
    at = AppTest.from_file(APP, default_timeout=30)
    at.switch_page("views/jobs.py")
    at.run()
    assert not at.exception, at.exception
    captions = " ".join(c.value for c in at.caption)
    assert "about 30 min left" in captions and "1 job(s) waiting" in captions
    assert not at.warning  # queued behind a running job is not "worker down"
