import pytest
from sqlalchemy import select

from tg_analyst import jobs
from tg_analyst.ingest import groupref
from tg_analyst.ingest.groupref import GroupRefError, candidate_ids, parse_group_ref
from tg_analyst.storage.db import make_session_factory
from tg_analyst.storage.models import Job


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@python_ru", "python_ru"),
        ("python_ru", "python_ru"),
        ("https://t.me/python_ru", "python_ru"),
        ("t.me/python_ru/12345", "python_ru"),
        ("https://telegram.me/python_ru?start=x", "python_ru"),
        ("https://t.me/c/1774464434/55", "-1001774464434"),
        ("web.telegram.org/k/#-1774464434", "-1774464434"),
        ("https://web.telegram.org/a/#-1001774464434", "-1001774464434"),
        ("https://web.telegram.org/k/#@python_ru", "python_ru"),
        ("  -1001774464434 ", "-1001774464434"),
    ],
)
def test_parse_group_ref(raw, expected):
    assert parse_group_ref(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "https://t.me/+AbCdEf123", "t.me/joinchat/xyz", "web.telegram.org/k/", "a b"]
)
def test_parse_group_ref_rejects(raw):
    with pytest.raises(GroupRefError):
        parse_group_ref(raw)


def test_candidate_ids():
    assert candidate_ids("-1001774464434") == [-1001774464434]
    assert candidate_ids("-1774464434") == [-1001774464434, -1774464434]
    assert candidate_ids("1774464434") == [-1001774464434, -1774464434, 1774464434]
    assert candidate_ids("python_ru") == []


class _NotLoggedInClient:
    """Fails loudly if anything tries the interactive login flow."""

    disconnected = False

    async def __aenter__(self):
        raise AssertionError("worker must not call client.start()")

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return False

    async def disconnect(self):
        self.disconnected = True


def test_backfill_job_without_login_fails_clearly(engine, monkeypatch):
    from tg_analyst.config import get_settings
    from tg_analyst.ingest import service

    client = _NotLoggedInClient()
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "x" * 32)
    monkeypatch.setattr(service, "make_client", lambda *a: client)
    get_settings.cache_clear()
    factory = make_session_factory(engine)
    try:
        with factory() as s:
            jobs.enqueue(s, "backfill", {"group": "web.telegram.org/k/#-1774464434"})
        jobs.run_one(factory)
        with factory() as s:
            job = s.scalar(select(Job))
        assert job.status == jobs.FAILED
        assert "not logged in" in job.error and "EOFError" not in job.error
        assert client.disconnected
    finally:
        get_settings.cache_clear()


class _FakeResolver:
    def __init__(self, known: set[int]):
        self.known, self.dialogs_loaded, self.calls = known, False, []

    async def get_entity(self, ref):
        self.calls.append(ref)
        if isinstance(ref, int) and ref in self.known and self.dialogs_loaded:
            return ref
        raise ValueError("Could not find the input entity")

    async def get_dialogs(self):
        self.dialogs_loaded = True


async def test_numeric_ids_try_supergroup_form_after_loading_dialogs():
    from tg_analyst.ingest.service import _find_entity

    client = _FakeResolver(known={-1001774464434})
    assert await _find_entity(client, "web.telegram.org/k/#-1774464434") == -1001774464434
    assert client.dialogs_loaded


async def test_unknown_group_gives_membership_hint():
    from tg_analyst.ingest.service import _find_entity

    with pytest.raises(groupref.GroupRefError, match="member"):
        await _find_entity(_FakeResolver(known=set()), "-1774464434")
