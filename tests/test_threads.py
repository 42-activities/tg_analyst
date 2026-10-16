from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from tg_analyst.analytics.threads import (
    build_threads,
    content_key,
    thread_table,
    thread_text,
)

T0 = datetime(2026, 3, 1, 12, 0)


def frame(rows) -> pd.DataFrame:
    """rows: (message_id, sender_id, seconds after T0, reply_to, text) + optional dict."""
    records = []
    for row in rows:
        mid, sender, seconds, reply, text, *extra = row
        record = {
            "message_id": mid,
            "sender_id": sender,
            "timestamp": T0 + timedelta(seconds=seconds),
            "reply_to_msg_id": reply,
            "thread_id": None,
            "text": text,
            "is_service": False,
            "is_bot": False,
            "sentiment_score": None,
            "insult": None,
            "threat": None,
            "reaction_count": None,
        }
        record.update(extra[0] if extra else {})
        records.append(record)
    df = pd.DataFrame(records)
    df["reply_to_msg_id"] = df["reply_to_msg_id"].astype("float")
    df["thread_id"] = df["thread_id"].astype("float")
    df["sentiment_score"] = df["sentiment_score"].astype("float")
    return df


def threads_of(membership: pd.DataFrame) -> list[list[int]]:
    return sorted(sorted(g.tolist()) for _, g in membership.groupby("root_id")["message_id"])


def test_reply_chains_form_threads_rooted_at_the_first_message():
    msgs = frame(
        [
            (1, 10, 0, None, "a"),
            (2, 11, 600, 1, "b"),
            (3, 12, 1200, 2, "c"),
            (4, 13, 1800, None, "unrelated"),
            (5, 10, 2400, 3, "d"),
        ]
    )
    membership = build_threads(msgs)
    assert threads_of(membership) == [[1, 2, 3, 5]]
    assert set(membership["root_id"]) == {1}


def test_bursts_join_the_same_senders_previous_message_within_two_minutes():
    msgs = frame(
        [
            (1, 10, 0, None, "first"),
            (2, 11, 30, 1, "reply"),
            (3, 11, 90, None, "and one more thing"),  # burst: joins 2
            (4, 11, 300, None, "too late"),  # > 2 min after 3
            (5, 12, 310, None, "someone else"),
            (6, 11, 320, None, "burst of 4"),
            (7, 11, 330, None, "and again"),
        ]
    )
    assert threads_of(build_threads(msgs)) == [[1, 2, 3], [4, 6, 7]]


def test_bursts_never_join_other_senders():
    msgs = frame([(1, 10, 0, None, "x"), (2, 11, 10, None, "y"), (3, 12, 20, None, "z")])
    assert build_threads(msgs).empty


def test_hubs_split_channel_posts_and_forum_topics():
    # Message 1 is a forum topic with many direct replies: each reply is its own exchange.
    rows = [(1, 99, 0, None, "topic")]
    rows += [(i, 1000 + i, i * 600, 1, f"comment {i}") for i in range(2, 8)]
    rows += [(8, 50, 5000, 2, "answer to 2"), (9, 51, 5100, 8, "more on 2")]
    membership = build_threads(frame(rows), hub_replies=5)
    assert threads_of(membership) == [[2, 8, 9]]


def test_bursts_do_not_cross_hubs():
    # One person posting in two forum topics within a minute: two separate exchanges.
    rows = [(1, 99, 0, None, "topic A"), (2, 98, 0, None, "topic B")]
    rows += [(i, 1000 + i, i, 1, "a") for i in range(3, 8)]
    rows += [(i, 1000 + i, i, 2, "b") for i in range(8, 13)]
    rows += [(13, 7, 500, 1, "in A"), (14, 7, 530, 2, "in B"), (15, 7, 540, 2, "in B again")]
    membership = build_threads(frame(rows), hub_replies=5, min_messages=2)
    assert threads_of(membership) == [[14, 15]]


def test_bots_service_messages_and_small_threads_are_skipped():
    msgs = frame(
        [
            (1, 10, 0, None, "q"),
            (2, 20, 10, 1, "bot answer", {"is_bot": True}),
            (3, 11, 20, 1, "human answer"),
            (4, 12, 30, None, None, {"is_service": True}),
            (5, 13, 40, 99, "reply to a message we don't have"),
        ]
    )
    assert build_threads(msgs).empty
    assert threads_of(build_threads(msgs, min_messages=2)) == [[1, 3]]


def test_thread_table_ignores_unscored_messages_in_tone():
    msgs = frame(
        [
            (1, 10, 0, None, "a", {"sentiment_score": 0.5, "reaction_count": 2}),
            (2, 11, 60, 1, "b", {"sentiment_score": None, "insult": 0.9}),
            (3, 10, 120, 2, "c", {"sentiment_score": -0.1, "reaction_count": 1}),
        ]
    )
    table = thread_table(msgs, build_threads(msgs))
    row = table.iloc[0]
    assert (row.root_id, row.messages, row.members, row.reactions) == (1, 3, 2, 3)
    assert np.isclose(row.tone, 0.2)
    assert np.isclose(row.insult_share, 1 / 3)
    assert row.end - row.start == timedelta(minutes=2)


def test_thread_text_uses_per_thread_letters_and_masks_names():
    msgs = frame(
        [
            (1, 123456, 0, None, "@alice_k look at https://example.com/x"),
            (2, 777, 10, 1, "ok"),
            (3, 888, 20, 2, None),  # sticker
            (4, 123456, 30, 2, "thanks"),
        ]
    )
    text = thread_text(msgs, [1, 2, 3, 4])
    assert text == "A: @user look at http\nB: ok\nA: thanks"
    assert "123456" not in text and "alice" not in text


def test_thread_text_keeps_first_and_longest_messages_when_truncated():
    rows = [(1, 1, 0, None, "start")]
    rows += [(i, i % 3, i, 1, "x" * (10 if i != 6 else 80)) for i in range(2, 10)]
    text = thread_text(frame(rows), range(1, 10), max_chars=140)
    lines = text.split("\n")
    assert lines[0] == "A: start"
    assert any(len(line) > 80 for line in lines)
    assert len(text) <= 140


def test_content_key_changes_with_text_and_membership():
    msgs = frame([(1, 1, 0, None, "a"), (2, 2, 1, 1, "b")])
    key = content_key(msgs, [1, 2])
    assert key == content_key(msgs, [2, 1])
    assert key != content_key(msgs, [1])
    edited = msgs.assign(text=["a", "b (edited)"])
    assert key != content_key(edited, [1, 2])
