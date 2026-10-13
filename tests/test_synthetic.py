from collections import Counter

from sqlalchemy import func, select

from tg_analyst import synthetic
from tg_analyst.storage.models import Interaction, Message


def test_generation_is_deterministic():
    a = synthetic.generate(days=10, seed=7)
    b = synthetic.generate(days=10, seed=7)
    assert [(m.message_id, m.sender_id, m.text) for m in a.messages] == [
        (m.message_id, m.sender_id, m.text) for m in b.messages
    ]


def test_ids_increase_with_time_and_replies_point_backwards():
    c = synthetic.generate(days=20)
    ts = [m.timestamp for m in c.messages]
    assert ts == sorted(ts)
    assert [m.message_id for m in c.messages] == list(range(1, len(c.messages) + 1))
    assert all(m.reply_to_msg_id < m.message_id for m in c.messages if m.reply_to_msg_id)


def test_load_builds_relationship_ground_truth(session):
    c = synthetic.generate(days=60)
    synthetic.load(session, c)

    assert session.scalar(select(func.count()).select_from(Message)) == len(c.messages)
    joins = session.scalar(
        select(func.count()).select_from(Message).where(Message.service_action == "join")
    )
    assert joins == sum(1 for r in c.roles.values() if r == "newcomer")

    # The feud pair should be each other's most frequent reply target.
    by_name = {u.username: u.user_id for u in c.users}
    replies = Counter(
        session.execute(
            select(Interaction.source_user_id, Interaction.target_user_id).where(
                Interaction.type == "reply"
            )
        ).all()
    )
    feud_a, feud_b = by_name["feuda"], by_name["feudb"]
    top_target_of_a = max((n, dst) for (src, dst), n in replies.items() if src == feud_a)[1]
    assert top_target_of_a == feud_b
