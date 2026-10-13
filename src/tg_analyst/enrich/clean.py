"""Text normalization shared by all enrichment steps."""

import re

# Bump when cleaning changes in a way that should trigger re-scoring.
CLEAN_VERSION = "1"

_URL = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+)", re.IGNORECASE)
_MENTION = re.compile(r"(?<!\w)@\w{2,}")
_WS = re.compile(r"\s+")
_LETTER = re.compile(r"[^\W\d_]")


def clean_text(text: str | None) -> str:
    """Replace URLs and @mentions with placeholders and collapse whitespace.

    Placeholders follow the convention of Twitter-trained sentiment models (cardiffnlp).
    Emoji are kept: they carry much of the tone in chat messages.
    """
    if not text:
        return ""
    text = _URL.sub("http", text)
    text = _MENTION.sub("@user", text)
    return _WS.sub(" ", text).strip()


def is_analyzable(cleaned: str) -> bool:
    """True if the text has real content beyond placeholders, digits and punctuation."""
    stripped = cleaned.replace("@user", "").replace("http", "")
    return bool(_LETTER.search(stripped)) or any(ord(c) > 0x2000 for c in stripped)
