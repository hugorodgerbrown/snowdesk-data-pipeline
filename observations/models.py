"""
observations/models.py — Database models for the observations application.

Defines ``FieldObservation``: a field report submitted by an authenticated
user from the map page.  Each row captures the single observation type
selected (e.g. whumpfing, pinwheels), the report location (latitude,
longitude, accuracy), the raw device GPS fix (when available), how the
location was determined (``location_source``), the wall-clock time, and a
best-effort resolution to the MicroRegion containing the point.

Business logic (point→region resolution, rate limiting) lives in
``observations/views.py`` and ``regions/services/point_match.py``.
"""

from __future__ import annotations

import collections
import datetime
import logging
import math
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import BaseModel

if TYPE_CHECKING:
    from regions.models import MicroRegion

logger = logging.getLogger(__name__)

# Mean earth radius in kilometres (IUGG). Mirrors
# ``bulletins/services/forecast_points.py::_haversine_m`` and
# ``mcp_server/resolvers.py::_haversine_km`` — kept as an independent copy
# per ``docs/decisions/forecast-point-quantisation.md`` (a shared ``core``
# extraction is deferred until a third distinct caller shape emerges).
_EARTH_RADIUS_KM = 6371.0088


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Return the great-circle distance in kilometres between two lat/lon pairs.

    Pure-Python haversine, mirroring ``bulletins/services/forecast_points.py::
    _haversine_m`` and ``mcp_server/resolvers.py::_haversine_km`` (same earth
    radius constant, kept as an independent copy — see
    ``docs/decisions/forecast-point-quantisation.md``).

    Args:
        lat1: Latitude of point one, in degrees.
        lon1: Longitude of point one, in degrees.
        lat2: Latitude of point two, in degrees.
        lon2: Longitude of point two, in degrees.

    Returns:
        The great-circle distance in kilometres.

    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


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

    def near_point_for_day(
        self,
        latitude: float,
        longitude: float,
        radius_km: float,
        day: datetime.date,
    ) -> "FieldObservationQuerySet":
        """Return observations within ``radius_km`` of a point on a calendar day.

        Filters to the given calendar day with a SQL ``observed_at__date``
        filter, then loads ``(pk, latitude, longitude)`` for that day and
        runs a pure-Python haversine pass (no PostGIS dependency) to find
        rows within range. The boundary is **inclusive** — a point exactly
        ``radius_km`` away is included.

        Args:
            latitude: WGS-84 latitude of the centre point, in degrees.
            longitude: WGS-84 longitude of the centre point, in degrees.
            radius_km: Maximum great-circle distance from the centre point,
                in kilometres. Points at exactly this distance are included.
            day: The calendar day to filter by.

        Returns:
            Filtered, chainable queryset restricted to matching rows.

        """
        candidates = self.filter(observed_at__date=day).values_list(
            "pk", "latitude", "longitude"
        )
        near_pks = [
            pk
            for pk, obs_lat, obs_lon in candidates
            if _haversine_km(latitude, longitude, obs_lat, obs_lon) <= radius_km
        ]
        return self.filter(pk__in=near_pks)

    def counts_near_point_for_day(
        self,
        latitude: float,
        longitude: float,
        radius_km: float,
        day: datetime.date,
    ) -> dict[str, int]:
        """Tally per-type observation counts within ``radius_km`` of a point.

        Reuses ``near_point_for_day`` then tallies with
        ``collections.Counter``, mirroring ``counts_for_region_day``.

        Args:
            latitude: WGS-84 latitude of the centre point, in degrees.
            longitude: WGS-84 longitude of the centre point, in degrees.
            radius_km: Maximum great-circle distance from the centre point,
                in kilometres. Points at exactly this distance are included.
            day: The calendar day to filter by.

        Returns:
            Mapping from observation-type value string to count. Only types
            that appear at least once are included; zero-count types are
            omitted.

        """
        types = self.near_point_for_day(
            latitude, longitude, radius_km, day
        ).values_list("observation_type", flat=True)
        return dict(collections.Counter(types))

    def user_located_exists_for_region_day(
        self,
        region: "MicroRegion",
        day: datetime.date,
    ) -> bool:
        """Return True if any user-located report exists for region on day.

        A user-located report is one with ``location_source`` set to
        ``MANUAL`` or ``GPS_REFINED`` — i.e. the user actively chose or
        adjusted the pin rather than accepting a raw GPS fix.

        Used by the bulletin page to show a public footnote when any report
        for today's region was manually placed.

        Args:
            region: The MicroRegion to filter by.
            day: The calendar day to filter by.

        Returns:
            True when at least one matching row exists; False otherwise.

        """
        user_located_sources = [
            FieldObservation.LOCATION_SOURCE.MANUAL,
            FieldObservation.LOCATION_SOURCE.GPS_REFINED,
        ]
        return self.filter(
            region=region,
            observed_at__date=day,
            location_source__in=user_located_sources,
        ).exists()

    def recent(self, since: datetime.datetime) -> "FieldObservationQuerySet":
        """Return observations reported at or after ``since``.

        Filters on ``observed_at``, which is ``db_index=True``. Used by the
        SNOW-419 community-reports map overlay to fetch the last 48 hours
        of reports.

        Args:
            since: Timezone-aware cutoff — rows with ``observed_at`` before
                this instant are excluded.

        Returns:
            Filtered queryset.

        """
        return self.filter(observed_at__gte=since)


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

    def counts_near_point_for_day(
        self,
        latitude: float,
        longitude: float,
        radius_km: float,
        day: datetime.date,
    ) -> dict[str, int]:
        """Delegate to the queryset's counts_near_point_for_day method.

        Args:
            latitude: WGS-84 latitude of the centre point, in degrees.
            longitude: WGS-84 longitude of the centre point, in degrees.
            radius_km: Maximum great-circle distance from the centre point,
                in kilometres. Points at exactly this distance are included.
            day: The calendar day to filter by.

        Returns:
            Mapping from observation-type value string to count.

        """
        return self.get_queryset().counts_near_point_for_day(
            latitude, longitude, radius_km, day
        )

    def user_located_exists_for_region_day(
        self,
        region: "MicroRegion",
        day: datetime.date,
    ) -> bool:
        """Delegate to the queryset's user_located_exists_for_region_day method.

        Args:
            region: The MicroRegion to filter by.
            day: The calendar day to filter by.

        Returns:
            True when at least one user-located report exists for the region
            on the given day; False otherwise.

        """
        return self.get_queryset().user_located_exists_for_region_day(region, day)

    def recent(self, since: datetime.datetime) -> FieldObservationQuerySet:
        """Delegate to the queryset's recent method.

        Args:
            since: Timezone-aware cutoff — rows with ``observed_at`` before
                this instant are excluded.

        Returns:
            Filtered queryset.

        """
        return self.get_queryset().recent(since)


# ---------------------------------------------------------------------------
# FieldObservation
# ---------------------------------------------------------------------------


class FieldObservation(BaseModel):
    """A field observation submitted by an authenticated user from the map.

    One row per report submission.  The ``observation_type`` CharField holds
    one ``OBSERVATION_TYPE`` value from the one-tap report form.
    The ``region`` FK is best-effort — it may be null when the report
    location cannot be matched to a known MicroRegion boundary.

    ``location_source`` records how the report coordinate was obtained:
    raw GPS fix (``GPS``), GPS fix manually refined by dragging the pin
    (``GPS_REFINED``), or entirely manual placement with no GPS involved
    (``MANUAL``).  When a raw GPS fix was available, it is stored in
    ``gps_latitude``/``gps_longitude``; the report location (which may
    differ after refinement) is in ``latitude``/``longitude``.

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

    class LOCATION_SOURCE(models.TextChoices):
        """How the report coordinate was determined.

        Values are UPPER_CASE identifiers; labels are in British English.
        ``GPS``         — raw device fix accepted without adjustment.
        ``GPS_REFINED`` — device fix obtained, then pin dragged by the user.
        ``MANUAL``      — no GPS fix; user tapped a point on the map.
        """

        GPS = "GPS", "GPS"
        GPS_REFINED = "GPS_REFINED", "GPS (refined)"
        MANUAL = "MANUAL", "Manual"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="field_observations",
        help_text="User who submitted this report.",
    )
    region = models.ForeignKey(
        "regions.MicroRegion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="field_observations",
        help_text=(
            "Best-effort MicroRegion resolved from the report location. "
            "Null when the point cannot be matched to a known boundary."
        ),
    )

    # Report location — the point attributed to this observation.
    # Populated from the GPS fix (GPS path) or from a pin placed/dragged
    # by the user (GPS_REFINED / MANUAL paths).
    latitude = models.FloatField(
        help_text=(
            "WGS-84 latitude of the report location "
            "(GPS fix, refined pin, or manually-placed pin)."
        ),
    )
    longitude = models.FloatField(
        help_text=(
            "WGS-84 longitude of the report location "
            "(GPS fix, refined pin, or manually-placed pin)."
        ),
    )
    accuracy_radius_km = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Browser-reported GPS accuracy radius in kilometres "
            "(converted from metres at the view layer)."
        ),
    )

    # Raw device GPS fix — null when no GPS fix was obtained (MANUAL path).
    # When present alongside a GPS_REFINED location, the pin-vs-GPS distance
    # is recoverable from the difference between these and latitude/longitude.
    gps_latitude = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "WGS-84 latitude of the raw device GPS fix at report time. "
            "Null when location_source is MANUAL (no GPS fix obtained)."
        ),
    )
    gps_longitude = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "WGS-84 longitude of the raw device GPS fix at report time. "
            "Null when location_source is MANUAL (no GPS fix obtained)."
        ),
    )

    location_source = models.CharField(
        max_length=16,
        choices=LOCATION_SOURCE.choices,
        help_text=(
            "How the report coordinate was determined: GPS (raw fix), "
            "GPS_REFINED (fix then pin drag), or MANUAL (pin only, no fix)."
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
            "Single OBSERVATION_TYPE value reported by the user "
            "(e.g. WHUMPFING).  To report two problems, submit two reports."
        ),
    )

    objects = FieldObservationManager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-observed_at"]
        # WGS-84 range guards (SNOW-464). A range check also rejects NaN/±Inf
        # (which compare false against the bounds), so a bad value can't land
        # via a path that bypasses the view-layer validators (admin, shell).
        constraints = [
            models.CheckConstraint(
                condition=models.Q(latitude__gte=-90, latitude__lte=90),
                name="fieldobservation_latitude_within_wgs84",
            ),
            models.CheckConstraint(
                condition=models.Q(longitude__gte=-180, longitude__lte=180),
                name="fieldobservation_longitude_within_wgs84",
            ),
            models.CheckConstraint(
                condition=models.Q(gps_latitude__isnull=True)
                | models.Q(gps_latitude__gte=-90, gps_latitude__lte=90),
                name="fieldobservation_gps_latitude_within_wgs84",
            ),
            models.CheckConstraint(
                condition=models.Q(gps_longitude__isnull=True)
                | models.Q(gps_longitude__gte=-180, gps_longitude__lte=180),
                name="fieldobservation_gps_longitude_within_wgs84",
            ),
            models.CheckConstraint(
                condition=models.Q(accuracy_radius_km__isnull=True)
                | models.Q(accuracy_radius_km__gte=0),
                name="fieldobservation_accuracy_radius_km_non_negative",
            ),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this observation.

        Format: ``"{user} @ {region} on {date}: {type label}"``
        """
        region_label = self.region.name if self.region is not None else "unknown region"
        date_label = self.observed_at.strftime("%Y-%m-%d")
        type_label = self.get_observation_type_display()
        return f"{self.user} @ {region_label} on {date_label}: {type_label}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
