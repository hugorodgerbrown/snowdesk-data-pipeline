"""
apps/ingest/normalise/bulletin_state.py — What an alert compares about a bulletin.

The reduction behind bulletin alerts (SNOW-938), moved here from
``apps/bulletins/services/bulletin_change.py`` by SNOW-1341 so the service
can store it in every render model's ``derived`` block: it is a pure
reading of the render model, so it is the service's, like the period
transition and the terrain rules. ``bulletin_change.py`` re-exports every
name and keeps the comparison (``summarise_change``) that the wording reads.

- :func:`bulletin_state` reduces a bulletin's render model to the facts an
  alert compares: the headline rating and its Swiss sub-rating, each named
  problem's aspects, elevation band and own rating, and whether the issue
  was published off-schedule.

A state round-trips through JSON (:meth:`BulletinState.to_json` /
:meth:`BulletinState.from_json`) because the "previous" side of every
comparison is the state the send log (``BulletinAlert``) recorded when the
account was last told about that region and day, not a second snapshot
table that could disagree with it. :meth:`BulletinState.fingerprint` is the
send log's idempotency key — so the move kept it byte-identical, which
``tests/ingest/normalise/test_bulletin_state.py`` pins per sentinel.

Problems are keyed by type alone. A problem that appears in both the
earlier and later windows of a split day is one problem to a reader ("wet
snow on S above 2,400 m"), so its windows are merged: aspects united, the
widest band kept, the highest own rating kept. Placeholder problems ("no
distinct problem", "favourable situation") name no hazard and are dropped.

No I/O and no Django models.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

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

# Placeholder entries that name no hazard: an alert drops them, and the day
# summary never names them in %(problems)s.
NEUTRAL_PROBLEMS: frozenset[str] = frozenset(
    {"no_distinct_avalanche_problem", "favourable_situation"}
)

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
            data: The stored dict, or None. Keys :meth:`to_json` does not
                write (the render model's stored ``fingerprint``) are
                ignored.

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
