"""Parse the many ways people refer to a Telegram group into something Telethon resolves.

Accepted forms:
- ``@name``, ``name``, ``t.me/name``, ``https://t.me/name/123`` (message link)
- ``t.me/c/1774464434/55`` (private-group message link)
- ``web.telegram.org/k/#-1774464434``, ``web.telegram.org/a/#-1001774464434``,
  ``web.telegram.org/k/#@name``
- numeric ids: ``-1001774464434`` (Telethon "marked" id), ``-1774464434``, ``1774464434``
"""

import re

_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_NUMERIC = re.compile(r"^-?\d+$")


class GroupRefError(ValueError):
    """The text cannot be turned into a group reference."""


def parse_group_ref(raw: str) -> str:
    """Return a canonical reference: a username (no @) or a numeric id as a string."""
    ref = (raw or "").strip()
    ref = re.sub(r"^https?://", "", ref)
    ref = ref.removeprefix("www.")

    if ref.startswith("web.telegram.org"):
        if "#" not in ref:
            raise GroupRefError("Open the group in Telegram Web first: the link needs a '#…' part.")
        ref = ref.split("#", 1)[1]
    elif ref.startswith(("t.me/", "telegram.me/")):
        path = ref.split("/", 1)[1].strip("/").split("?")[0]
        parts = path.split("/")
        if parts[0].startswith("+") or parts[0] == "joinchat":
            raise GroupRefError(
                "Invite links can't be resolved. Join the group with your Telegram account, "
                "then use its @username or numeric id."
            )
        if parts[0] == "c" and len(parts) > 1 and parts[1].isdigit():
            return f"-100{parts[1]}"
        ref = parts[0]

    ref = ref.strip("/ ").removeprefix("@")
    if _NUMERIC.match(ref) or _USERNAME.match(ref):
        return ref
    raise GroupRefError(f"Not a Telegram group username, link or id: {raw!r}")


def candidate_ids(ref: str) -> list[int]:
    """Possible Telethon ids for a numeric reference, most likely first.

    Telegram Web shows supergroups as ``-<id>`` while Telethon expects ``-100<id>``;
    small legacy groups really are ``-<id>``. Try both.
    """
    if not _NUMERIC.match(ref):
        return []
    n = int(ref)
    if str(n).startswith("-100"):
        return [n]
    raw = abs(n)
    return list(dict.fromkeys([int(f"-100{raw}"), -raw, n]))
