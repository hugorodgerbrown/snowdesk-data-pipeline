"""
favourites/models.py — Database models for the favourites application.

Defines ``Favourite``: a saved map pin created by an authenticated user.
Each row records the user's chosen location (latitude, longitude, optional
name), the shared ``bulletins.ForecastPoint`` it was resolved to (so the
pin reuses that point's Open-Meteo weather sampling), and a best-effort
``MicroRegion`` resolution.

Business logic (forecast-point resolution, region resolution, per-user
favourite caps) lives in ``favourites/services.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from core.models import BaseModel

if TYPE_CHECKING:
    from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# QuerySet / Manager
# ---------------------------------------------------------------------------


class FavouriteQuerySet(models.QuerySet["Favourite"]):
    """Custom queryset for Favourite."""

    def for_user(self, user: "User") -> "FavouriteQuerySet":
        """Return favourites owned by the given user.

        Args:
            user: The user to filter by.

        Returns:
            Filtered queryset.

        """
        return self.filter(user=user)


# ---------------------------------------------------------------------------
# Favourite
# ---------------------------------------------------------------------------


class Favourite(BaseModel):
    """A saved map pin created by an authenticated user.

    ``name`` is an optional user-supplied label — when blank, the view
    layer falls back to a coordinate-derived display string.

    ``forecast_point`` is the shared ``bulletins.ForecastPoint`` the pin's
    coordinates were resolved to (see
    ``bulletins.services.forecast_points.resolve_forecast_point``);
    ``on_delete=PROTECT`` because the point may be shared by other users'
    favourites and by weather-fetch bookkeeping — a Favourite row is
    deleted long before its ForecastPoint ever could be.

    ``region`` is a best-effort MicroRegion resolution, mirroring
    ``observations.models.FieldObservation.region`` — it may be null when
    the pin falls outside every known boundary.

    ``resort`` (SNOW-499) is set when this favourite was created from a
    public ``regions.Resort`` pin rather than a dropped map pin — in that
    case ``region`` is taken authoritatively from ``resort.region`` rather
    than the best-effort ``region_for_point`` match above.
    ``on_delete=SET_NULL`` degrades a resort favourite to a plain pin (the
    snapshotted name/coordinates/region survive) if the resort row is ever
    deleted; the partial-unique constraint below stops a user favouriting
    the same resort twice.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favourites",
        help_text="User who saved this favourite.",
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Optional user-supplied label for this favourite.",
    )
    latitude = models.FloatField(
        help_text="WGS-84 latitude of the saved pin.",
    )
    longitude = models.FloatField(
        help_text="WGS-84 longitude of the saved pin.",
    )
    elevation = models.FloatField(
        help_text=(
            "Elevation in metres, taken from the resolved ForecastPoint's "
            "sampling elevation."
        ),
    )
    forecast_point = models.ForeignKey(
        "bulletins.ForecastPoint",
        on_delete=models.PROTECT,
        related_name="favourites",
        help_text="Shared ForecastPoint this pin's coordinates resolved to.",
    )
    region = models.ForeignKey(
        "regions.MicroRegion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="favourites",
        help_text=(
            "Best-effort MicroRegion resolved from the pin location. "
            "Null when the point cannot be matched to a known boundary."
        ),
    )
    resort = models.ForeignKey(
        "regions.Resort",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="favourites",
        help_text=(
            "The public Resort this favourite was created from, when "
            "created via the resort-pin popup rather than a dropped pin."
        ),
    )

    objects = FavouriteQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        # WGS-84 range guards (SNOW-464). A range check also rejects NaN/±Inf,
        # so a bad value can't land via a path that bypasses the view-layer
        # validators (admin, shell).
        constraints = [
            models.CheckConstraint(
                condition=models.Q(latitude__gte=-90, latitude__lte=90),
                name="favourite_latitude_within_wgs84",
            ),
            models.CheckConstraint(
                condition=models.Q(longitude__gte=-180, longitude__lte=180),
                name="favourite_longitude_within_wgs84",
            ),
            # SNOW-499: a user may favourite a given resort at most once.
            # Partial (condition=...) so multiple plain (resort=NULL) pins
            # never collide with one another.
            models.UniqueConstraint(
                fields=["user", "resort"],
                condition=models.Q(resort__isnull=False),
                name="favourite_unique_user_resort",
            ),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this favourite.

        Format: ``"{user} @ {name or lat,lon}"``
        """
        label = self.name or f"{self.latitude:.5f},{self.longitude:.5f}"
        return f"{self.user} @ {label}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
