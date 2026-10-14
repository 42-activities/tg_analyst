"""Toxicity: scoring, storage, and how insults vs. swearing feed conflict detection."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from tg_analyst.analytics import conflict, load, members, metrics
from tg_analyst.enrich import sentiment, toxicity
from tg_analyst.enrich.pipeline import enrich, model_version
from tg_analyst.enrich.sentiment import VaderBackend
from tg_analyst.storage import repository as repo
from tg_analyst.storage.models import Enrichment
from tg_analyst.storage.records import GroupRecord, MessageRecord, ReactionRecord, ReactionSummary

GID = -100888
T0 = datetime(2026, 3, 1)


class FakeToxicity:
    """Keyword stand-in for the model: "idiot" insults, "damn" swears, "kill" threatens."""

    version = "tox:fake"

    def score(self, texts: Sequence[str]) -> list[toxicity.ToxicityResult]:
        return [
            toxicity.result(
                0.9 if "idiot" in t else 0.05,
                0.9 if "damn" in t else 0.05,
                0.9 if "kill" in t else 0.05,
            )
            for t in texts
        ]


def test_model_output_mapping():
    out = toxicity.TransformersToxicity._to_result(
        [
            {"label": "non-toxic", "score": 0.1},
            {"label": "insult", "score": 0.8},
            {"label": "obscenity", "score": 0.3},
            {"label": "threat", "score": 0.01},
            {"label": "dangerous", "score": 0.7},  # reputational risk, not toxicity
        ]
    )
    assert out == toxicity.ToxicityResult(0.8, 0.8, 0.3, 0.01)


def test_backends_follow_the_sentiment_backend(monkeypatch):
    settings = SimpleNamespace(
        sentiment_backend="vader", hf_sentiment_model="m", hf_toxicity_model="t"
    )
    sent, tox = toxicity.backends(settings)
    assert isinstance(sent, VaderBackend) and tox is None

    class FakeHF:
        def __init__(self, model):
            self.model = model

    monkeypatch.setitem(sentiment.BACKENDS, "hf", FakeHF)
    monkeypatch.setattr(toxicity, "TransformersToxicity", FakeHF)
    sent, tox = toxicity.backends(settings, "hf")
    assert sent.model == "m" and tox.model == "t"
    settings.hf_toxicity_model = ""
    assert toxicity.backends(settings, "hf")[1] is None


def test_production_model_version_fits_the_column():
    hf = SimpleNamespace(version="hf:cointegrated/rubert-tiny-sentiment-balanced")
    tox = SimpleNamespace(version="tox:cointegrated/rubert-tiny-toxicity")
    assert len(model_version(hf, tox)) <= Enrichment.__table__.c.model_version.type.length


def test_enrich_stores_toxicity_and_rescores_on_model_change(session):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    repo.upsert_messages(
        session,
        [
            MessageRecord(GID, 1, T0, sender_id=1, text="you are an idiot"),
            MessageRecord(GID, 2, T0, sender_id=2, text="damn, what a day"),
            MessageRecord(GID, 3, T0, sender_id=2, has_media=True, media_type="sticker"),
        ],
    )
    session.commit()
    assert enrich(session, VaderBackend()).scored == 2
    assert session.get(Enrichment, (GID, 1)).insult is None  # no toxicity backend

    # Adding a toxicity model changes the version, so everything is re-scored once.
    stats = enrich(session, VaderBackend(), toxicity=FakeToxicity())
    assert stats.scored == 2
    insulting, swearing = session.get(Enrichment, (GID, 1)), session.get(Enrichment, (GID, 2))
    assert insulting.insult == 0.9 and insulting.toxicity == 0.9
    assert swearing.obscenity == 0.9 and swearing.insult == 0.05
    assert insulting.model_version == model_version(VaderBackend(), FakeToxicity())
    assert session.get(Enrichment, (GID, 3)).toxicity is None
    assert enrich(session, VaderBackend(), toxicity=FakeToxicity()).scored == 0


def frame(rows):
    """rows: (tone, insult, obscenity, threat)."""
    return pd.DataFrame(rows, columns=["sentiment_score", "insult", "obscenity", "threat"])


def test_hostile_mask_separates_insults_from_swearing():
    f = frame(
        [
            (-0.9, 0.0, 0.0, 0.0),  # very negative, no toxicity: hostile by tone
            (-0.9, 0.0, 0.9, 0.0),  # negative but just swearing: not hostile
            (+0.5, 0.9, 0.0, 0.0),  # an insult with a smile: hostile
            (+0.2, 0.0, 0.0, 0.8),  # a threat: hostile
            (-0.9, 0.9, 0.9, 0.0),  # swearing *and* insulting: hostile
            (-0.9, None, None, None),  # not scored for toxicity: tone decides
            (0.0, 0.0, 0.0, 0.0),
        ]
    )
    assert conflict.hostile_mask(f, -0.5).tolist() == [True, False, True, True, True, True, False]
    tone_only = f[["sentiment_score"]]
    assert conflict.hostile_mask(tone_only, -0.5).tolist() == [
        True,
        True,
        False,
        False,
        True,
        True,
        False,
    ]
    assert conflict.toxic_labels(f).tolist()[:5] == [
        "",
        "swearing",
        "insult",
        "threat",
        "insult, swearing",
    ]


def edges_between(pairs):
    """pairs: (src, dst, minutes, tone, insult, obscenity)."""
    return pd.DataFrame(
        [
            {
                "source_user_id": s,
                "target_user_id": d,
                "type": "reply",
                "timestamp": pd.Timestamp(T0) + pd.Timedelta(minutes=m),
                "message_id": i,
                "sentiment_score": tone,
                "insult": insult,
                "obscenity": obscene,
                "threat": 0.0,
            }
            for i, (s, d, m, tone, insult, obscene) in enumerate(pairs)
        ]
    )


def test_conflicts_ignore_friendly_swearing_and_count_insults():
    friends = [(1, 2, m, -0.8, 0.0, 0.9) for m in range(0, 50, 10)]  # swearing, rough tone
    feud = [(3, 4, m, 0.1, 0.9, 0.0) for m in range(0, 40, 10)]  # polite-sounding insults
    feud += [(4, 3, 45, -0.8, 0.0, 0.0)]
    eps = conflict.detect_conflicts(edges_between(friends + feud), negative_below=-0.5)
    assert len(eps) == 1
    ep = eps.iloc[0]
    assert (ep["user_a"], ep["user_b"]) == (3, 4)
    assert ep["negative_interactions"] == 5 and ep["insults"] == 4 and ep["mutual"]


def test_insult_metrics():
    msgs = pd.DataFrame(
        [
            {
                "message_id": i,
                "sender_id": s,
                "timestamp": pd.Timestamp(T0) + pd.Timedelta(minutes=i),
                "text": "x",
                "reply_to_msg_id": None,
                "has_media": False,
                "is_service": False,
                "is_bot": False,
                "service_action": None,
                "sentiment_score": 0.0,
                "sentiment_label": "neutral",
                "intent": None,
                "name": str(s),
                "insult": insult,
                "obscenity": obscene,
                "threat": 0.0,
            }
            for i, (s, insult, obscene) in enumerate(
                [(1, 0.9, 0.0), (1, 0.1, 0.9), (2, 0.1, 0.0), (2, None, None)]
            )
        ]
    )
    k = metrics.kpis(msgs)
    assert k["insult_share"] == k["swearing_share"] == 0.3333  # of 3 scored messages
    edges = edges_between([(1, 2, 0, -0.5, 0.9, 0.0), (2, 1, 1, 0.0, 0.0, 0.0)])
    stats = metrics.member_stats(msgs, edges).set_index("user_id")
    assert stats.loc[1, "insults_sent"] == 1 and stats.loc[2, "insults_received"] == 1
    assert stats.loc[1, "insults_received"] == 0
    summary = members.member_summary(msgs, edges, 2)
    assert summary["insults_received"] == 1 and summary["insults_sent"] == 0


def test_reaction_edges_carry_no_toxicity(engine, session):
    repo.upsert_group(session, GroupRecord(GID, "g"))
    repo.upsert_messages(session, [MessageRecord(GID, 1, T0, sender_id=1, text="you idiot")])
    session.commit()
    enrich(session, VaderBackend(), toxicity=FakeToxicity())
    repo.store_reactions(session, GID, [ReactionSummary(1, 1, [ReactionRecord(2, "👍", T0)])])
    session.commit()
    e = load.interactions(engine, GID, T0 - timedelta(days=1), T0 + timedelta(days=1))
    assert len(e) == 1 and pd.isna(e.iloc[0]["insult"])  # the insult is the author's
    m = load.messages(engine, GID, T0 - timedelta(days=1), T0 + timedelta(days=1))
    assert m.iloc[0]["insult"] == 0.9
