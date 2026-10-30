"""
apps/bulletins/services/bulletin_change.py — What moved between two issues.

The change signal behind bulletin alerts (SNOW-938). Two pure functions:

- :func:`bulletin_state` reduces a bulletin's render model to the facts an
  alert compares: the headline rating and its Swiss sub-rating, each named
  problem's aspects, elevation band and own rating, and whether the issue
  was published off-schedule.
- :func:`summarise_change` compares two states and returns a
  :class:`BulletinChange` of discrete movements. The wording is A3's
  (``alert_wording.py``); this module says only *what* moved.

A state round-trips through JSON (:meth:`BulletinState.to_json` /
:meth:`BulletinState.from_json`) because the "previous" side of every
comparison is the state the send log (``BulletinAlert``) recorded when the
account was last told about that region and day, not a second snapshot
table that could disagree with it. :meth:`BulletinState.fingerprint` is the
send log's idempotency key.

Why problem-aware: on CH-4115 across 2025/26 the headline rating moved on
13.8% of days, while on 56.9% it held and a problem was added, removed or
moved. A rating-only diff would be silent on most of the days something
changed.

Problems are keyed by type alone. A problem that appears in both the
earlier and later windows of a split day is one problem to a reader ("wet
snow on S above 2,400 m"), so its windows are merged: aspects united, the
widest band kept, the highest own rating kept. Placeholder problems ("no
distinct problem", "favourable situation") name no hazard and are dropped.

No I/O and no Django models: the module is importable by the replay
harness (A7) and the send command (A4) alike.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from apps.bulletins.services.day_summary import NEUTRAL_PROBLEMS

logger = logging.getLogger(__name__)

# Bump when the state's shape or the fingerprint's inputs change: a send-log
# row written under another version is never compared field by field.
BULLETIN_STATE_VERSION: int = 1

DANGER_ORDER: tuple[str, ...] = (
    "low",
    "moderate",
    "considerable",
    "high",
    "very_high",
)

DANGER_NUMBER: dict[str, int] = {key: i + 1 for i, key in enumerate(DANGER_ORDER)}

# SLF's sub-rating within a level, lowest first. ``None`` (no sub-rating,
# every ALBINA and Météo-France bulletin) sorts as the middle.
_SUBDIVISION_RANK: dict[str | None, int] = {"-": 0, "=": 1, None: 1, "+": 2}

CLOCKWISE_ASPECTS: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _sort_aspects(aspects: set[str]) -> tuple[str, ...]:
    """
    Return aspects in clockwise compass order, unknown tokens last.

    Args:
        aspects: Aspect tokens in any order.

    Returns:
        The tokens as a tuple ordered N, NE, …, NW, then any others sorted.

    """
    order = {a: i for i, a in enumerate(CLOCKWISE_ASPECTS)}
    return tuple(sorted(aspects, key=lambda a: (order.get(a, len(order)), a)))


@dataclass(frozen=True)
class ProblemState:
    """One named avalanche problem as an alert reads it."""

    problem_type: str
    aspects: tuple[str, ...] = ()
    lower: int | None = None
    upper: int | None = None
    # Which bound the treeline token stood for: "lower" reads "above the
    # treeline", "upper" reads "below the treeline"; None when no bound was
    # the treeline.
    treeline: str | None = None
    rating: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Return the problem as a JSON-ready dict."""
        return {
            "problem_type": self.problem_type,
            "aspects": list(self.aspects),
            "lower": self.lower,
            "upper": self.upper,
            "treeline": self.treeline,
            "rating": self.rating,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ProblemState:
        """
        Rebuild a problem from :meth:`to_json` output.

        Args:
            data: The stored dict.

        Returns:
            The problem.

        """
        return cls(
            problem_type=str(data.get("problem_type", "")),
            aspects=tuple(data.get("aspects") or ()),
            lower=data.get("lower"),
            upper=data.get("upper"),
            treeline=data.get("treeline"),
            rating=data.get("rating"),
        )

    @property
    def band(self) -> tuple[int | None, int | None, str | None]:
        """Return the elevation band as one comparable value."""
        return (self.lower, self.upper, self.treeline)


@dataclass(frozen=True)
class BulletinState:
    """What an alert compares about one region's bulletin for one day."""

    rating: str
    subdivision: str | None = None
    problems: tuple[ProblemState, ...] = ()
    unscheduled: bool = False

    @property
    def level(self) -> int:
        """Return the headline rating as 1–5 (0 when unrated)."""
        return DANGER_NUMBER.get(self.rating, 0)

    @property
    def rank(self) -> tuple[int, int]:
        """Return a sortable (level, sub-rating) pair."""
        return (self.level, _SUBDIVISION_RANK.get(self.subdivision, 1))

    def problem(self, problem_type: str) -> ProblemState | None:
        """
        Return the named problem, or None when this state does not name it.

        Args:
            problem_type: An EAWS problem type token.

        Returns:
            The problem or None.

        """
        for p in self.problems:
            if p.problem_type == problem_type:
                return p
        return None

    def to_json(self) -> dict[str, Any]:
        """Return the state as a JSON-ready dict for the send log."""
        return {
            "version": BULLETIN_STATE_VERSION,
            "rating": self.rating,
            "subdivision": self.subdivision,
            "problems": [p.to_json() for p in self.problems],
            "unscheduled": self.unscheduled,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> BulletinState | None:
        """
        Rebuild a state from :meth:`to_json` output.

        Args:
            data: The stored dict, or None.

        Returns:
            The state, or None when ``data`` is empty or was written under
            another :data:`BULLETIN_STATE_VERSION`.

        """
        if not data or data.get("version") != BULLETIN_STATE_VERSION:
            return None
        return cls(
            rating=str(data.get("rating", "")),
            subdivision=data.get("subdivision"),
            problems=tuple(
                ProblemState.from_json(p) for p in data.get("problems") or []
            ),
            unscheduled=bool(data.get("unscheduled")),
        )

    def fingerprint(self) -> str:
        """
        Return a stable hash of what a reader would see.

        ``unscheduled`` is left out: a bulletin re-issued off-schedule that
        says the same thing is not news.

        Returns:
            A 16-character hex digest.

        """
        payload = self.to_json()
        payload.pop("unscheduled")
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _merge(a: ProblemState, b: ProblemState) -> ProblemState:
    """
    Merge two windows of the same problem into one.

    Args:
        a: One window's problem.
        b: The other's.

    Returns:
        The problem with aspects united, the widest band and the highest
        own rating.

    """
    lowers = [v for v in (a.lower, b.lower) if v is not None]
    uppers = [v for v in (a.upper, b.upper) if v is not None]
    # A bound missing from either window means that window is unbounded
    # there, so the merged band is too.
    lower = min(lowers) if len(lowers) == 2 else None
    upper = max(uppers) if len(uppers) == 2 else None
    ratings = [r for r in (a.rating, b.rating) if r in DANGER_NUMBER]
    rating = max(ratings, key=DANGER_NUMBER.__getitem__) if ratings else None
    return ProblemState(
        problem_type=a.problem_type,
        aspects=_sort_aspects(set(a.aspects) | set(b.aspects)),
        lower=lower,
        upper=upper,
        treeline=a.treeline or b.treeline,
        rating=rating,
    )


def _height(value: Any) -> int | None:
    """Return a bound as whole metres, or None when it is not a number."""
    try:
        return int(value) if value is not None and value != "" else None
    except TypeError, ValueError:
        return None


def _problem_from_render(problem: dict[str, Any]) -> ProblemState:
    """
    Reduce one render-model problem to its alert state.

    Args:
        problem: A problem dict from ``render_model["traits"][*]["problems"]``.

    Returns:
        The problem's state.

    """
    elevation = problem.get("elevation") or {}
    rating = problem.get("danger_rating_value")
    return ProblemState(
        problem_type=str(problem.get("problem_type", "")),
        aspects=_sort_aspects(set(problem.get("aspects") or [])),
        lower=_height(elevation.get("lower")),
        upper=_height(elevation.get("upper")),
        treeline=(elevation.get("treeline_side") or "lower")
        if elevation.get("treeline")
        else None,
        rating=rating if rating in DANGER_NUMBER else None,
    )


def bulletin_state(render_model: dict[str, Any]) -> BulletinState:
    """
    Reduce a bulletin's render model to what an alert compares.

    Args:
        render_model: ``Bulletin.render_model`` (or ``build_render_model``
            output).

    Returns:
        The bulletin's state. Problems are ordered by type, so two render
        models naming the same facts in another order give equal states.

    """
    danger = render_model.get("danger") or {}
    by_type: dict[str, ProblemState] = {}
    for trait in render_model.get("traits") or []:
        for raw in trait.get("problems") or []:
            problem = _problem_from_render(raw)
            if not problem.problem_type or problem.problem_type in NEUTRAL_PROBLEMS:
                continue
            existing = by_type.get(problem.problem_type)
            by_type[problem.problem_type] = (
                _merge(existing, problem) if existing else problem
            )
    metadata = render_model.get("metadata") or {}
    # SLF's neutral sub-rating reads as the bare level, exactly like no
    # sub-rating at all, so the two are one state: "=" ↔ None is no move.
    subdivision = danger.get("subdivision")
    return BulletinState(
        rating=str(danger.get("key") or ""),
        subdivision=None if subdivision == "=" else subdivision,
        problems=tuple(by_type[k] for k in sorted(by_type)),
        unscheduled=bool(metadata.get("unscheduled")),
    )


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
