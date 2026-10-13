from datetime import datetime, timedelta
from statistics import mean

import pytest
from sqlalchemy import select

from tg_analyst import synthetic
from tg_analyst.enrich.clean import clean_text, is_analyzable
from tg_analyst.enrich.intent import detect_intent
from tg_analyst.enrich.pipeline import enrich, model_version
from tg_analyst.enrich.sentiment import (
    SentimentResult,
    TransformersBackend,
    VaderBackend,
    get_backend,
)
from tg_analyst.storage import repository as repo
from tg_analyst.storage.models import Enrichment, Message
from tg_analyst.storage.records import GroupRecord, MessageRecord

GID = -100555
T0 = datetime(2026, 2, 1)


class FakeBackend:
    def __init__(self, version="fake-1"):
        self.version = version
        self.calls: list[list[str]] = []

    def score(self, texts):
        self.calls.append(list(texts))
        return [SentimentResult(0.5, "positive") for _ in texts]


# --- cleaning -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("see https://example.com/x?a=1  now", "see http now"),
        ("@alice_99 thanks", "@user thanks"),
        ("mail me at a@b.com", "mail me at a@b.com"),  # emails are not mentions
        ("line1\n\n line2", "line1 line2"),
        (None, ""),
    ],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("hello", True),
        ("привет", True),
        ("🔥🔥", True),
        ("http", False),
        ("@user 123 !!", False),
        ("", False),
    ],
)
def test_is_analyzable(text, ok):
    assert is_analyzable(text) is ok


# --- intent ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Thanks a lot, that really helped!", "thanks"),
        ("Спасибо большое", "thanks"),
        ("Hi all, how do I set up the environment?", "question"),
        ("подскажите где документация", "question"),
        ("Hello everyone", "greeting"),
        ("Добро пожаловать!", "greeting"),
        ("This is so frustrating, nothing works.", "complaint"),
        ("опять ничего не работает", "complaint"),
        ("Meeting is moved to Thursday.", None),
        ("", None),
    ],
)
def test_detect_intent(text, intent):
    assert detect_intent(text) == intent


def test_positive_bug_report_is_not_a_complaint():
    assert detect_intent("Great, the bug is fixed", "positive") is None


# --- backends -------------------------------------------------------------------------


def test_vader_labels():
    res = VaderBackend().score(
        ["Thanks a lot, that really helped!", "Ok, noted.", "So frustrating, nothing works."]
    )
    assert [r.label for r in res] == ["positive", "neutral", "negative"]
    assert all(-1 <= r.score <= 1 for r in res)


def test_transformers_output_mapping():
    named = [
        {"label": "Negative", "score": 0.1},
        {"label": "Neutral", "score": 0.2},
        {"label": "Positive", "score": 0.7},
    ]
    assert TransformersBackend._to_result(named) == SentimentResult(0.6, "positive")
    generic = [
        {"label": "LABEL_0", "score": 0.8},
        {"label": "LABEL_1", "score": 0.15},
        {"label": "LABEL_2", "score": 0.05},
    ]
    assert TransformersBackend._to_result(generic) == SentimentResult(-0.75, "negative")


def test_unknown_backend():
    with pytest.raises(ValueError):
        get_backend("nope")


# --- pipeline -------------------------------------------------------------------------


def _seed(session):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    repo.upsert_messages(
        session,
        [
            MessageRecord(GID, 1, T0, sender_id=1, text="Hi, how does this work?"),
            MessageRecord(
                GID,
                2,
                T0 + timedelta(minutes=1),
                sender_id=2,
                text=None,
                has_media=True,
                media_type="sticker",
            ),
            MessageRecord(
                GID,
                3,
                T0 + timedelta(minutes=2),
                sender_id=3,
                is_service=True,
                service_action="join",
                action_user_ids=[3],
            ),
            MessageRecord(GID, 4, T0 + timedelta(minutes=3), sender_id=2, text="https://x.y"),
        ],
    )
    session.commit()


def test_enrich_scores_text_and_marks_textless_rows(session):
    _seed(session)
    backend = FakeBackend()
    stats = enrich(session, backend, batch_size=2)

    assert (stats.scored, stats.skipped) == (1, 2)
    assert backend.calls == [["Hi, how does this work?"]]
    rows = {e.message_id: e for e in session.scalars(select(Enrichment))}
    assert set(rows) == {1, 2, 4}  # service message 3 is not enriched
    assert rows[1].sentiment_label == "positive" and rows[1].intent == "question"
    assert rows[2].sentiment_label is None
    assert rows[1].model_version == model_version(backend)


def test_enrich_is_incremental_and_rescores_on_version_change(session):
    _seed(session)
    enrich(session, FakeBackend())
    again = FakeBackend()
    assert enrich(session, again).scored == 0 and again.calls == []

    upgraded = FakeBackend("fake-2")
    assert enrich(session, upgraded).scored == 1
    versions = set(session.scalars(select(Enrichment.model_version)))
    assert versions == {model_version(upgraded)}


def test_force_rescores_everything_once(session):
    _seed(session)
    enrich(session, FakeBackend())
    forced = FakeBackend()
    stats = enrich(session, forced, force=True, batch_size=1)
    assert (stats.scored, stats.skipped) == (1, 2)


def test_vader_on_synthetic_ground_truth(session):
    community = synthetic.generate(days=30)
    synthetic.load(session, community)
    enrich(session, VaderBackend())

    rows = session.execute(
        select(Message.sender_id, Enrichment.sentiment_score)
        .join(
            Enrichment,
            (Enrichment.group_id == Message.group_id)
            & (Enrichment.message_id == Message.message_id),
        )
        .where(Enrichment.sentiment_score.is_not(None))
    ).all()
    tone: dict[str, list[float]] = {}
    for sender, score in rows:
        tone.setdefault(community.roles[sender], []).append(score)

    assert mean(tone["helper"]) > 0.2
    assert mean(tone["feuder"]) < 0
    assert mean(tone["critic"]) < mean(tone["regular"]) < mean(tone["helper"])
