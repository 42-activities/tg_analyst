"""Deterministic synthetic community for development, demos and tests.

The generated group has recognizable relationship patterns so analytics can be checked
against known ground truth: helpful members, a pair of members in a running feud, a
critic, lurkers, a bot, and newcomers who either get welcomed (and stay) or get ignored
(and churn). Reactions follow the same story: helpers collect 👍 and ❤️, and the feuding
pair throw 🤡 and 👎 at each other.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tg_analyst.storage.records import (
    GroupRecord,
    MessageRecord,
    ReactionRecord,
    ReactionSummary,
    UserRecord,
)

SYNTHETIC_GROUP_ID = -1001000000001

POSITIVE = [
    "Thanks a lot, that really helped!",
    "Great point, totally agree.",
    "Welcome aboard, glad to have you here!",
    "Awesome work on this, love it.",
    "This community is the best, appreciate everyone.",
    "Nice, happy to help anytime.",
]
NEUTRAL = [
    "I pushed the update to the shared folder.",
    "Meeting is moved to Thursday.",
    "Has anyone tried the new version yet?",
    "Here is the link from yesterday.",
    "I think it depends on the setup.",
    "Ok, noted.",
]
NEGATIVE = [
    "That is just wrong and you know it.",
    "Honestly this is a terrible idea.",
    "Stop spamming the chat, it's annoying.",
    "Why do you always ignore what I say?",
    "This is so frustrating, nothing works.",
    "You clearly didn't read the thread.",
]
QUESTIONS = [
    "Hi all, how do I set up the environment?",
    "Can someone explain how the config works?",
    "Where can I find the docs for this?",
    "Is there a guide for beginners?",
]


@dataclass(slots=True)
class Persona:
    user: UserRecord
    role: str  # admin | helper | regular | critic | feuder | lurker | bot | newcomer
    weight: float  # relative posting frequency
    joined: datetime
    churn_after: datetime | None = None


@dataclass(slots=True)
class SyntheticCommunity:
    group: GroupRecord
    users: list[UserRecord]
    messages: list[MessageRecord]
    roles: dict[int, str]  # user_id -> ground-truth role
    reactions: list[ReactionSummary] = field(default_factory=list)


def _hour_weights() -> list[float]:
    # Quiet nights, a lunchtime bump and an evening peak.
    return [
        0.2,
        0.1,
        0.1,
        0.1,
        0.1,
        0.2,
        0.4,
        0.7,
        1.0,
        1.1,
        1.2,
        1.3,
        1.6,
        1.4,
        1.2,
        1.2,
        1.3,
        1.5,
        1.9,
        2.3,
        2.4,
        2.0,
        1.2,
        0.6,
    ]


def generate(days: int = 90, seed: int = 42, start: datetime | None = None) -> SyntheticCommunity:
    rng = random.Random(seed)
    start = start or datetime(2026, 1, 1)
    group = GroupRecord(SYNTHETIC_GROUP_ID, "Synthetic Dev Community", "synthetic_dev", None)

    personas: list[Persona] = []
    uid = 1000

    def add(role: str, weight: float, joined: datetime, name: str) -> Persona:
        nonlocal uid
        uid += 1
        p = Persona(UserRecord(uid, name.lower(), name, is_bot=role == "bot"), role, weight, joined)
        personas.append(p)
        return p

    for i in range(2):
        add("admin", 3.0, start, f"Admin{i + 1}")
    for i in range(4):
        add("helper", 4.0, start, f"Helper{i + 1}")
    for i in range(12):
        add("regular", 1.5, start, f"Regular{i + 1}")
    add("critic", 2.0, start, "Critic1")
    feud_a = add("feuder", 2.5, start, "FeudA")
    feud_b = add("feuder", 2.5, start, "FeudB")
    for i in range(10):
        add("lurker", 0.08, start, f"Lurker{i + 1}")
    add("bot", 0.6, start, "HelperBot")

    # Newcomers arrive throughout the period.
    newcomers: list[Persona] = []
    for i in range(16):
        joined = start + timedelta(days=rng.uniform(3, days - 10), hours=rng.uniform(8, 21))
        newcomers.append(add("newcomer", 1.2, joined, f"Newbie{i + 1}"))

    messages: list[MessageRecord] = []
    msg_id = 0
    by_id: dict[int, MessageRecord] = {}
    hours = _hour_weights()

    def emit(
        ts: datetime,
        sender: Persona,
        text: str,
        reply_to: MessageRecord | None = None,
        mention: Persona | None = None,
    ) -> MessageRecord:
        nonlocal msg_id
        msg_id += 1
        if mention is not None:
            text = f"@{mention.user.username} {text}"
        rec = MessageRecord(
            group_id=group.group_id,
            message_id=msg_id,
            timestamp=ts,
            sender_id=sender.user.user_id,
            text=text,
            reply_to_msg_id=reply_to.message_id if reply_to else None,
            views=rng.randint(20, 400),
            mention_usernames=[mention.user.username] if mention else [],
        )
        if rng.random() < 0.08:
            rec.has_media, rec.media_type = True, rng.choice(["photo", "video", "sticker"])
            if rec.media_type == "sticker" and mention is None:
                rec.text = None  # media-only message, as in real chats
        messages.append(rec)
        by_id[msg_id] = rec
        return rec

    def join(ts: datetime, p: Persona) -> None:
        nonlocal msg_id
        msg_id += 1
        messages.append(
            MessageRecord(
                group_id=group.group_id,
                message_id=msg_id,
                timestamp=ts,
                sender_id=p.user.user_id,
                is_service=True,
                service_action="join",
                action_user_ids=[p.user.user_id],
            )
        )

    helpers = [p for p in personas if p.role in ("helper", "admin")]
    unanswerable: set[int] = set()
    pending_newcomers = sorted(newcomers, key=lambda p: p.joined)

    for day in range(days):
        day_start = start + timedelta(days=day)
        weekend = day_start.weekday() >= 5
        n = rng.randint(40, 70) if not weekend else rng.randint(20, 40)
        times = sorted(
            day_start
            + timedelta(hours=rng.choices(range(24), hours)[0], minutes=rng.uniform(0, 59))
            for _ in range(n)
        )

        for ts in times:
            # Newcomer arrival: join, ask a question, and get welcomed (or ignored).
            while pending_newcomers and pending_newcomers[0].joined <= ts:
                nc = pending_newcomers.pop(0)
                join(nc.joined, nc)
                q = emit(nc.joined + timedelta(minutes=2), nc, rng.choice(QUESTIONS))
                if rng.random() < 0.7:
                    h = rng.choice(helpers)
                    emit(
                        q.timestamp + timedelta(minutes=rng.uniform(1, 30)),
                        h,
                        rng.choice(POSITIVE[:3]),
                        reply_to=q,
                    )
                    if rng.random() < 0.6:
                        emit(q.timestamp + timedelta(minutes=45), nc, POSITIVE[0], reply_to=q)
                else:
                    # Ignored newcomers go quiet within days, and nobody ever answers them.
                    nc.churn_after = nc.joined + timedelta(days=rng.uniform(1, 5))
                    unanswerable.add(q.message_id)

            active = [
                p
                for p in personas
                if p.joined <= ts and (p.churn_after is None or ts < p.churn_after)
            ]
            sender = rng.choices(active, [p.weight for p in active])[0]
            recent = [
                m
                for m in messages[-30:]
                if not m.is_service
                and m.sender_id != sender.user.user_id
                and m.message_id not in unanswerable
            ]
            target = rng.choice(recent) if recent and rng.random() < 0.55 else None

            if sender.role == "feuder":
                other = feud_b if sender is feud_a else feud_a
                other_msgs = [m for m in messages[-80:] if m.sender_id == other.user.user_id]
                if other_msgs and rng.random() < 0.6:
                    emit(ts, sender, rng.choice(NEGATIVE), reply_to=other_msgs[-1])
                    continue
            if sender.role == "bot":
                emit(ts, sender, "Reminder: please read the pinned rules.")
                continue

            tone = {
                "helper": (0.65, 0.33, 0.02),
                "admin": (0.45, 0.5, 0.05),
                "critic": (0.1, 0.35, 0.55),
                "feuder": (0.15, 0.45, 0.4),
            }.get(sender.role, (0.3, 0.6, 0.1))
            pool = rng.choices([POSITIVE, NEUTRAL, NEGATIVE], tone)[0]
            mention = None
            if target is None and rng.random() < 0.06:
                mention = rng.choice([p for p in active if p is not sender])
            emit(ts, sender, rng.choice(pool), reply_to=target, mention=mention)

    # Replies and welcomes are emitted slightly ahead of the main timeline; renumber so ids
    # increase with time, as they do in real Telegram groups.
    messages.sort(key=lambda m: (m.timestamp, m.message_id))
    new_id = {m.message_id: i for i, m in enumerate(messages, start=1)}
    for m in messages:
        m.message_id = new_id[m.message_id]
        if m.reply_to_msg_id is not None:
            m.reply_to_msg_id = new_id[m.reply_to_msg_id]

    return SyntheticCommunity(
        group=group,
        users=[p.user for p in personas],
        messages=messages,
        roles={p.user.user_id: p.role for p in personas},
        reactions=_reactions(messages, personas, feud_a, feud_b, seed),
    )


def _reactions(
    messages: list[MessageRecord],
    personas: list[Persona],
    feud_a: Persona,
    feud_b: Persona,
    seed: int,
) -> list[ReactionSummary]:
    # A separate random stream, so adding reactions leaves the messages unchanged.
    rng = random.Random(seed + 1)
    role = {p.user.user_id: p.role for p in personas}
    fans = [p.user.user_id for p in personas if p.role in ("admin", "helper", "regular")]
    rival = {feud_a.user.user_id: feud_b.user.user_id, feud_b.user.user_id: feud_a.user.user_id}
    out = []
    for m in messages:
        if m.is_service or m.sender_id is None:
            continue
        reactors: list[ReactionRecord] = []
        when = m.timestamp + timedelta(minutes=rng.uniform(1, 90))
        if role.get(m.sender_id) in ("helper", "admin") and rng.random() < 0.35:
            for uid in rng.sample([u for u in fans if u != m.sender_id], rng.randint(1, 3)):
                reactors.append(ReactionRecord(uid, rng.choice(["👍", "❤️", "🔥"]), when))
        elif m.sender_id in rival and rng.random() < 0.4:
            reactors.append(ReactionRecord(rival[m.sender_id], rng.choice(["🤡", "👎"]), when))
        elif rng.random() < 0.05:
            reactors.append(ReactionRecord(rng.choice(fans), "👍", when))
        reactors = [r for r in reactors if r.user_id != m.sender_id]
        if reactors:
            out.append(ReactionSummary(m.message_id, len(reactors), reactors))
    return out


def load(session, community: SyntheticCommunity) -> None:
    """Write a synthetic community through the same repository path as real ingestion."""
    from tg_analyst.storage import repository as repo

    users = {u.user_id: u for u in community.users}
    repo.upsert_group(session, community.group)
    repo.upsert_users(
        session,
        ((users[m.sender_id], m.timestamp) for m in community.messages if m.sender_id in users),
    )
    repo.upsert_messages(session, community.messages)
    repo.advance_high_water_mark(
        session, community.group.group_id, max(m.message_id for m in community.messages)
    )
    repo.store_reactions(session, community.group.group_id, community.reactions)
    repo.build_interactions(session, community.group.group_id)
    session.commit()
