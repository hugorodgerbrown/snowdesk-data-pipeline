"""
apps/bulletins/schema.py — CAAML v6.0 schema types for SLF avalanche bulletins.

Provides:
  - TextChoices enums for the constrained string fields used in the
    CAAML schema (danger rating, valid time period). Using TextChoices
    keeps a single source of truth for the allowed values and exposes
    them to Django forms/admin.
  - The frozen ``Elevation`` dataclass, the elevation constraint the
    problem-card icon tag (``card_tags.elevation_icon``) is typed on.

SNOW-1342 removed the ``DangerRating`` and ``AvalancheProblem`` dataclasses
(and the ``AvalancheProblemType`` enum and wet/diurnal sets only they read):
they parsed a bulletin's ``raw_data``, and no read path reads it any more —
the render model is the parsed form (docs/render-model.md).
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
