"""
apps/bulletins/services/bulletin_change.py — What moved between two issues.

The change signal behind bulletin alerts (SNOW-938). Two pure functions:

- :func:`bulletin_state` reduces a bulletin's render model to the facts an
  alert compares. Since SNOW-1341 it is the service's
  (``apps/ingest/normalise/bulletin_state.py``, through
  ``apps.ingest.public``), so every render model carries it in
  ``derived.state``; it and its dataclasses are re-exported here, the
  import surface every caller already uses.
- :func:`summarise_change` compares two states and returns a
  :class:`BulletinChange` of discrete movements. The wording is A3's
  (``alert_wording.py``); this module says only *what* moved.

Why problem-aware: on CH-4115 across 2025/26 the headline rating moved on
13.8% of days, while on 56.9% it held and a problem was added, removed or
moved. A rating-only diff would be silent on most of the days something
changed.

No I/O and no Django models: the module is importable by the replay
harness (A7) and the send command (A4) alike.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from apps.ingest.public import (
    BULLETIN_STATE_VERSION,
    CLOCKWISE_ASPECTS,
    DANGER_NUMBER,
    DANGER_ORDER,
    NEUTRAL_PROBLEMS,
    BulletinState,
    ProblemState,
    bulletin_state,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BULLETIN_STATE_VERSION",
    "CLOCKWISE_ASPECTS",
    "DANGER_NUMBER",
    "DANGER_ORDER",
    "NEUTRAL_PROBLEMS",
    "BulletinChange",
    "BulletinState",
    "ProblemMove",
    "ProblemState",
    "bulletin_state",
    "summarise_change",
]


@dataclass(frozen=True)
class ProblemMove:
    """A problem both states name, and what moved about it."""

    before: ProblemState
    after: ProblemState

    @property
    def band_moved(self) -> bool:
        """Return True when the elevation band moved."""
        return self.before.band != self.after.band

    @property
    def aspects_moved(self) -> bool:
        """Return True when the aspect list changed."""
        return self.before.aspects != self.after.aspects

    @property
    def rating_moved(self) -> bool:
        """Return True when the problem's own rating changed."""
        return self.before.rating != self.after.rating


@dataclass(frozen=True)
class BulletinChange:
    """Every discrete movement between two states of one region's day."""

    previous: BulletinState | None
    current: BulletinState
    added: tuple[ProblemState, ...] = field(default=())
    removed: tuple[ProblemState, ...] = field(default=())
    moved: tuple[ProblemMove, ...] = field(default=())

    @property
    def rating_direction(self) -> int:
        """Return 1 when the headline level rose, -1 when it fell, else 0."""
        if self.previous is None:
            return 0
        before, after = self.previous.level, self.current.level
        return (after > before) - (after < before)

    @property
    def subdivision_moved(self) -> bool:
        """Return True when the level held but its sub-rating moved."""
        if self.previous is None or self.rating_direction:
            return False
        return self.previous.subdivision != self.current.subdivision

    @property
    def is_empty(self) -> bool:
        """
        Return True when nothing a reader would notice moved.

        An unscheduled re-issue saying the same thing is empty, and there
        is nothing to compare against when ``previous`` is None.
        """
        if self.previous is None:
            return True
        return not (
            self.rating_direction
            or self.subdivision_moved
            or self.added
            or self.removed
            or self.moved
        )


def summarise_change(
    previous: BulletinState | None, current: BulletinState
) -> BulletinChange:
    """
    Compare two states of one region's bulletin for the same or adjacent day.

    Args:
        previous: The state the reader last saw, or None when there is none.
        current: The state now authoritative.

    Returns:
        The change. ``moved`` lists only problems where something moved.

    """
    if previous is None:
        return BulletinChange(previous=None, current=current)
    before = {p.problem_type: p for p in previous.problems}
    after = {p.problem_type: p for p in current.problems}
    added = tuple(after[k] for k in sorted(after.keys() - before.keys()))
    removed = tuple(before[k] for k in sorted(before.keys() - after.keys()))
    moved = tuple(
        move
        for k in sorted(before.keys() & after.keys())
        if (move := ProblemMove(before=before[k], after=after[k])).band_moved
        or move.aspects_moved
        or move.rating_moved
    )
    return BulletinChange(
        previous=previous,
        current=current,
        added=added,
        removed=removed,
        moved=moved,
    )
