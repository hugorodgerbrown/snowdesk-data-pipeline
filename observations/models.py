"""
observations/models.py — Database models for the observations application.

Defines ``FieldObservation``: a GPS-gated field report submitted by an
authenticated subscriber from the map page.  Each row captures the single
observation type selected (e.g. whumpfing, pinwheels), the GPS fix
(latitude, longitude, accuracy), the wall-clock time, and a best-effort
resolution to the MicroRegion containing the point.

Business logic (point→region resolution, rate limiting) lives in
``observations/views.py`` and ``regions/services/point_match.py``.
"""

from __future__ import annotations

import collections
import datetime
import logging
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

from core.models import BaseModel

if TYPE_CHECKING:
    from regions.models import MicroRegion

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QuerySet / Manager
# ---------------------------------------------------------------------------


class FieldObservationQuerySet(models.QuerySet["FieldObservation"]):
    """Custom queryset for FieldObservation."""

    def for_region_day(
        self,
        region: "MicroRegion",
        day: datetime.date,
    ) -> "FieldObservationQuerySet":
        """Return observations for the given region on the given calendar day.

        Args:
            region: The MicroRegion to filter by.
            day: The calendar day to filter by.

        Returns:
            Filtered queryset.

        """
        return self.filter(region=region, observed_at__date=day)

    def counts_for_region_day(
        self,
        region: "MicroRegion",
        day: datetime.date,
    ) -> dict[str, int]:
        """Tally per-type observation counts for a region on a calendar day.

        Each row contributes exactly one type (the singular ``observation_type``
        field).  Uses ``collections.Counter`` over a flat values_list query —
        no JSON-path DB function portability concerns.

        Args:
            region: The MicroRegion to filter by.
            day: The calendar day to filter by.

        Returns:
            Mapping from observation-type value string to count. Only types
            that appear at least once are included; zero-count types are
            omitted.

        """
        types = self.filter(region=region, observed_at__date=day).values_list(
            "observation_type", flat=True
        )
        return dict(collections.Counter(types))


class FieldObservationManager(models.Manager["FieldObservation"]):
    """Manager for FieldObservation exposing the custom queryset."""

    def get_queryset(self) -> FieldObservationQuerySet:
        """Return the custom queryset."""
        return FieldObservationQuerySet(self.model, using=self._db)

    def counts_for_region_day(
        self,
        region: "MicroRegion",
        day: datetime.date,
    ) -> dict[str, int]:
        """Delegate to the queryset's counts_for_region_day method.

        Args:
            region: The MicroRegion to filter by.
            day: The calendar day to filter by.

        Returns:
            Mapping from observation-type value string to count.

        """
        return self.get_queryset().counts_for_region_day(region, day)


# ---------------------------------------------------------------------------
# FieldObservation
# ---------------------------------------------------------------------------


class FieldObservation(BaseModel):
    """A GPS-gated field observation submitted by a subscriber from the map.

    One row per report submission.  The ``observation_type`` CharField holds
    one ``OBSERVATION_TYPE`` value from the one-tap report form.
    The ``region`` FK is best-effort — it may be null when the GPS fix
    cannot be matched to a known MicroRegion boundary.

    Ordering is newest-first so the admin list and queryset slices show
    recent reports at the top.
    """

    class OBSERVATION_TYPE(models.TextChoices):
        """Recognised field-observation signal types.

        Values are UPPER_CASE identifiers; labels are in British English.
        Extend this list to add new observation types — the CharField
        constraint enforces only the values present at row-creation time;
        adding new choices does not require a migration.
        """

        WHUMPFING = "WHUMPFING", "Whumpfing"
        PINWHEELS = "PINWHEELS", "Pinwheels"
        WIND_STRIATIONS = "WIND_STRIATIONS", "Wind striations"
        FRACTURES = "FRACTURES", "Fractures"
        SHOOTING_CRACKS = "SHOOTING_CRACKS", "Shooting cracks"

    subscriber = models.ForeignKey(
        "subscriptions.Subscriber",
        on_delete=models.CASCADE,
        related_name="field_observations",
        help_text="Subscriber who submitted this report.",
    )
    region = models.ForeignKey(
        "regions.MicroRegion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="field_observations",
        help_text=(
            "Best-effort MicroRegion resolved from the GPS fix. "
            "Null when the point cannot be matched to a known boundary."
        ),
    )

    # GPS fix — required; the view 400s if absent (the GPS gate).
    latitude = models.FloatField(
        help_text="WGS-84 latitude of the GPS fix at report time.",
    )
    longitude = models.FloatField(
        help_text="WGS-84 longitude of the GPS fix at report time.",
    )
    accuracy_radius_km = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Browser-reported GPS accuracy radius in kilometres "
            "(converted from metres at the view layer)."
        ),
    )

    observed_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="Wall-clock time when the report was submitted.",
    )
    observation_type = models.CharField(
        max_length=32,
        choices=OBSERVATION_TYPE.choices,
        help_text=(
            "Single OBSERVATION_TYPE value reported by the subscriber "
            "(e.g. WHUMPFING).  To report two problems, submit two reports."
        ),
    )

    objects = FieldObservationManager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-observed_at"]

    def to_string(self) -> str:
        """Return a concise human-readable description of this observation.

        Format: ``"{subscriber} @ {region} on {date}: {type label}"``
        """
        region_label = self.region.name if self.region is not None else "unknown region"
        date_label = self.observed_at.strftime("%Y-%m-%d")
        type_label = self.get_observation_type_display()
        return f"{self.subscriber} @ {region_label} on {date_label}: {type_label}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
