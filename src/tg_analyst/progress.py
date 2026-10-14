"""Progress callbacks for long-running work (back-fill, re-check, enrichment).

Library code calls ``report(fraction, detail)``; the job worker stores it on the job row
so the Jobs page can show a progress bar. ``fraction`` is 0..1, or None when unknown.
"""

from collections.abc import Callable

Progress = Callable[[float | None, str], None]


def no_progress(fraction: float | None, detail: str) -> None:
    """The default: report nothing."""


def ratio(done: float, total: float) -> float | None:
    """done / total clamped to 0..1, or None when the total is unknown."""
    if not total or total <= 0:
        return None
    return min(max(done / total, 0.0), 1.0)
