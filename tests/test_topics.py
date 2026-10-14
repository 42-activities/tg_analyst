"""Keyword topics: extraction, tone per topic, trends and member topics."""

from datetime import datetime, timedelta

import pandas as pd

from tg_analyst.analytics import topics
from tg_analyst.enrich.keywords import keywords, stem

T0 = datetime(2026, 1, 1)


def chat(rows) -> pd.DataFrame:
    """rows: (sender, day, text, tone) -> a frame shaped like ``analytics.load.messages``."""
    df = pd.DataFrame(
        [
            {
                "message_id": i + 1,
                "sender_id": sender,
                "timestamp": T0 + timedelta(days=day, minutes=i),
                "text": text,
                "sentiment_score": tone,
                "sentiment_label": "negative"
                if tone < -0.2
                else "positive"
                if tone > 0.2
                else "neutral",
                "is_service": False,
                "is_bot": False,
            }
            for i, (sender, day, text, tone) in enumerate(rows)
        ]
    )
    return df


def test_keywords_merge_inflections_and_drop_noise():
    assert keywords("Кто-нибудь получал визу? У меня визы нет") == {stem("визу"): "визу"}
    assert stem("визу") == stem("визы") == stem("виза")
    assert stem("préfecture") == stem("prefecture")
    assert keywords("@bob https://t.me/x 2026, ok и это") == {}
    assert keywords(None) == {}


def test_top_topics_tone_and_labels():
    msgs = chat(
        [
            (1, 0, "Опять отказали в визе", -0.8),
            (2, 0, "Визу не дают, ужас", -0.7),
            (3, 1, "С визой одни проблемы", -0.6),
            (1, 1, "Пицца сегодня отличная", 0.8),
            (2, 2, "Пицца вкусная", 0.7),
            (3, 2, "Пиццу заказали", 0.6),
            (4, 3, "мойкотик мойкотик мойкотик", 0.0),  # one person's catchphrase
            (4, 4, "мойкотик", 0.0),
            (4, 4, "Чат без текста", 0.0),
            (5, 4, float("nan"), 0.0),  # media-only
        ]
    )
    terms = topics.message_terms(msgs)
    top = topics.top_topics(terms).set_index("stem")
    visa, pizza = stem("визы"), stem("пицца")
    assert set(top.index) == {visa, pizza}  # the catchphrase has a single author
    assert top.loc[visa, "messages"] == 3 and top.loc[visa, "members"] == 3
    assert top.loc[visa, "tone_vs_group"] < 0 < top.loc[pizza, "tone_vs_group"]
    assert top.loc[visa, "negative_share"] == 1.0
    assert top.loc[pizza, "topic"] == "пицца"


def test_bots_and_service_messages_are_ignored():
    msgs = chat([(1, 0, "Пицца", 0.0), (2, 0, "Пицца", 0.0), (3, 0, "Пицца", 0.0)])
    msgs.loc[0, "is_bot"] = True
    msgs.loc[1, "is_service"] = True
    assert topics.message_terms(msgs)["sender_id"].tolist() == [3]
    assert topics.top_topics(topics.message_terms(msgs.iloc[:0])).empty


def test_trending_topics():
    old = [(u % 4, d, "Обсуждаем проект и стажировку", 0.1) for u, d in enumerate(range(60))]
    recent = [(u % 4, 60 + u, "Бассейн закрыли, проект тоже", -0.3) for u in range(10)]
    terms = topics.message_terms(chat(old + recent))
    trending = topics.trending_topics(terms, recent_days=30)
    assert trending.iloc[0]["stem"] == stem("бассейн")
    assert trending.iloc[0]["before"] == 0 and trending.iloc[0]["lift"] > 5
    assert stem("стажировку") not in set(trending["stem"])  # not discussed recently


def test_topic_drilldown_and_member_topics():
    rows = [(1, d, "Банк заблокировал карту", -0.5) for d in range(4)]
    rows += [(2, d, "Банк и пицца", 0.0) for d in range(2)]
    rows += [(u, d, "Пицца на кампусе", 0.5) for u in (2, 3, 4) for d in range(3)]
    terms = topics.message_terms(chat(rows))
    bank = stem("банк")

    who = topics.topic_members(terms, bank)
    assert who["sender_id"].tolist() == [1, 2] and who.iloc[0]["messages"] == 4
    assert topics.topic_timeline(terms, bank)["messages"].sum() == 6
    assert len(topics.topic_messages(terms, bank)) == 6

    mine = topics.member_topics(terms, 1).set_index("stem")
    assert bank in mine.index and mine.loc[bank, "lift"] > 1
    assert stem("пицца") not in mine.index
    assert topics.member_topics(terms, 99).empty


def test_without_names_drops_member_names():
    terms = topics.message_terms(chat([(u, 0, "Марина сказала про пиццу", 0.0) for u in (1, 2, 3)]))
    kept = topics.without_names(terms, ["marina_k", "Марина Иванова", None])
    assert set(kept["stem"]) == {stem("пиццу")}
