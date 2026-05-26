"""
bulletins/schema.py — CAAML v6.0 schema types for SLF avalanche bulletins.

Provides:
  - TextChoices enums for the constrained string fields used in the
    CAAML schema (danger rating, valid time period, avalanche problem
    type). Using TextChoices keeps a single source of truth for the
    allowed values and exposes them to Django forms/admin.
  - Frozen dataclasses (Elevation, DangerRating, AvalancheProblem) for
    structured access to the relevant slices of a bulletin's raw_data
    payload, with from_dict() classmethods that map the camelCase JSON
    keys to snake_case attributes.

These types are intentionally read-only views over the raw CAAML JSON.
They do not validate the input — fields that are absent become None or
an empty tuple.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# TextChoices
# ---------------------------------------------------------------------------


class DangerRatingValue(models.TextChoices):
    """EAWS five-level danger rating, plus the two non-numeric states."""

    LOW = "low", _("Low (1)")
    MODERATE = "moderate", _("Moderate (2)")
    CONSIDERABLE = "considerable", _("Considerable (3)")
    HIGH = "high", _("High (4)")
    VERY_HIGH = "very_high", _("Very high (5)")
    NO_SNOW = "no_snow", _("No snow")
    NO_RATING = "no_rating", _("No rating")


class ValidTimePeriod(models.TextChoices):
    """Time-of-day qualifier used to scope a rating or problem."""

    ALL_DAY = "all_day", _("All day")
    EARLIER = "earlier", _("Earlier")
    LATER = "later", _("Later")


class AvalancheProblemType(models.TextChoices):
    """The eight EAWS avalanche problem types."""

    NEW_SNOW = "new_snow", _("New snow")
    WIND_SLAB = "wind_slab", _("Wind slab")
    PERSISTENT_WEAK_LAYERS = "persistent_weak_layers", _("Persistent weak layers")
    WET_SNOW = "wet_snow", _("Wet snow")
    GLIDING_SNOW = "gliding_snow", _("Gliding snow")
    CORNICES = "cornices", _("Cornices")
    NO_DISTINCT_AVALANCHE_PROBLEM = (
        "no_distinct_avalanche_problem",
        _("No distinct avalanche problem"),
    )
    FAVOURABLE_SITUATION = "favourable_situation", _("Favourable situation")


# Problem types driven by liquid water in the snowpack (melt, rain, warming).
# The complement is the dry-snow regime (new snow, wind slab, persistent weak
# layers). Stored as plain strings so membership tests against the plain-string
# ``problem_type`` attribute are unambiguous.
WET_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        AvalancheProblemType.WET_SNOW.value,
        AvalancheProblemType.GLIDING_SNOW.value,
    }
)

# Time periods that scope a problem to part of the day rather than all of it —
# the qualifier issuing services use for problems that ramp up or ease with the
# daily warming cycle.
DIURNAL_TIME_PERIODS: frozenset[str] = frozenset(
    {
        ValidTimePeriod.EARLIER.value,
        ValidTimePeriod.LATER.value,
    }
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Elevation:
    """
    An elevation constraint as defined by the CAAML schema.

    Either bound may be None. A non-None ``lower_bound`` alone means
    "above this elevation"; a non-None ``upper_bound`` alone means
    "below this elevation"; both set means an elevation band. Values
    are strings because the schema permits the literal "treeline" in
    addition to numeric metres.
    """

    lower_bound: str | None = None
    upper_bound: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Elevation | None:
        """
        Build an Elevation from a CAAML elevation dict.

        Returns None if no elevation data is present.
        """
        if not data:
            return None
        return cls(
            lower_bound=data.get("lowerBound"),
            upper_bound=data.get("upperBound"),
        )


@dataclass(frozen=True)
class DangerRating:
    """
    A single danger-rating entry from a bulletin's ``dangerRatings`` array.

    ``main_value`` is one of the values in :class:`DangerRatingValue`.
    The remaining fields are optional qualifiers that constrain when
    and where the rating applies.
    """

    main_value: str
    valid_time_period: str | None = None
    elevation: Elevation | None = None
    aspects: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DangerRating:
        """Build a DangerRating from a single CAAML dangerRating dict."""
        return cls(
            main_value=data["mainValue"],
            valid_time_period=data.get("validTimePeriod"),
            elevation=Elevation.from_dict(data.get("elevation")),
            aspects=tuple(data.get("aspects") or ()),
        )


@dataclass(frozen=True)
class AvalancheProblem:
    """
    A single problem entry from a bulletin's ``avalancheProblems`` array.

    ``problem_type`` is one of the values in :class:`AvalancheProblemType`.
    All other fields are optional and reflect what the issuing AWS chose
    to publish for the problem.

    The two providers publish opposite ends of the EAWS model: SLF emits the
    matrix *output* (``danger_rating_value``, ``comment``, and the ``"+"/"="/"-"``
    sub-level in ``subdivision``); ALBINA emits the matrix *inputs*
    (``avalanche_size``, ``frequency``, ``snowpack_stability``) plus the
    ``avalanche_type`` axis (slab/loose/glide), which is orthogonal to
    ``problem_type``. Any given field is None when the issuing service didn't
    publish it.
    """

    problem_type: str
    comment: str | None = None
    danger_rating_value: str | None = None
    valid_time_period: str | None = None
    elevation: Elevation | None = None
    aspects: tuple[str, ...] = ()
    avalanche_size: int | None = None
    snowpack_stability: str | None = None
    frequency: str | None = None
    # subdivision: SLF customData.CH ("minus"/"neutral"/"plus" — the 3-/3/3+ sub-level).
    subdivision: str | None = None
    # avalanche_type: ALBINA customData.ALBINA ("slab"/"loose"/"glide").
    avalanche_type: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvalancheProblem:
        """Build an AvalancheProblem from a single CAAML avalancheProblem dict."""
        custom_data = data.get("customData") or {}
        ch = custom_data.get("CH") or {}
        albina = custom_data.get("ALBINA") or {}
        return cls(
            problem_type=data["problemType"],
            comment=data.get("comment"),
            danger_rating_value=data.get("dangerRatingValue"),
            valid_time_period=data.get("validTimePeriod"),
            elevation=Elevation.from_dict(data.get("elevation")),
            aspects=tuple(data.get("aspects") or ()),
            avalanche_size=data.get("avalancheSize"),
            snowpack_stability=data.get("snowpackStability"),
            frequency=data.get("frequency"),
            subdivision=ch.get("subdivision"),
            avalanche_type=albina.get("avalancheType"),
        )

    @property
    def is_wet(self) -> bool:
        """
        Whether this is a wet-snow regime problem.

        True for ``wet_snow`` and ``gliding_snow`` — the problem types driven
        by liquid water in the snowpack. The complement is the dry-snow regime.
        """
        return self.problem_type in WET_PROBLEM_TYPES

    @property
    def is_diurnal(self) -> bool:
        """
        Whether the problem is scoped to part of the day rather than all of it.

        True when ``valid_time_period`` is ``earlier`` or ``later`` — the
        qualifier that tracks the daily warming cycle (predominantly attached
        to wet-snow problems). ``all_day`` and a missing period are both False.
        """
        return self.valid_time_period in DIURNAL_TIME_PERIODS
