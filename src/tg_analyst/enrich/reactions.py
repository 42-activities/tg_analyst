"""The tone a reaction emoji expresses toward the message's author.

A reaction carries no text, so its tone comes from the emoji: 👍 and ❤️ are approval,
🤡 and 👎 are hostile, 😢 or 🤔 are neither. Scores share the -1..+1 scale of message
sentiment so reaction edges can be averaged with reply and mention edges. Emoji that are
not listed (including custom emoji) are neutral.
"""

# Variation selectors and skin tones don't change the meaning.
_MODIFIERS = dict.fromkeys(map(ord, "️\U0001f3fb\U0001f3fc\U0001f3fd\U0001f3fe\U0001f3ff"))

POSITIVE = 0.6
WARM = 0.8
NEGATIVE = -0.5
HOSTILE = -0.8

EMOJI_TONE: dict[str, float] = {
    **dict.fromkeys("👍👌👏🙏🤝💯✅🆒🫡✍", POSITIVE),
    **dict.fromkeys("😁😂🤣😄😃😊🙂😎🤩🎉🔥⚡🏆🍾🥂😇🤓🍓", POSITIVE),
    **dict.fromkeys("❤🥰😍😘🤗💘💋💖💕♥😻🫶", WARM),
    "❤‍🔥": WARM,
    **dict.fromkeys("👎😡🤬😠🙄😒", NEGATIVE),
    **dict.fromkeys("🤡💩🤮🖕🤢", HOSTILE),
}


def emoji_tone(emoji: str | None) -> float:
    """Tone of a reaction: positive approval, negative hostility, 0.0 if neutral/unknown."""
    if not emoji:
        return 0.0
    return EMOJI_TONE.get(emoji.translate(_MODIFIERS), 0.0)
