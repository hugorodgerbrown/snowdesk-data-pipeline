"""
apps/favourites/models.py — Database models for the favourites application.

Defines ``Favourite``: a saved map pin created by an authenticated user.
Each row records the user's chosen location (latitude, longitude, optional
name) and a best-effort ``MicroRegion`` resolution — or, since SNOW-802, a
**region pin**: a row whose subject is the ``MicroRegion`` itself, with no
coordinate, no elevation and no ``Location``. Region pins are what the
retired ``Subscription`` rows became; ``Favourite`` is the one saved-place
model (``docs/decisions/two-documents-and-a-map.md``).

Business logic (forecast-point resolution, region resolution, per-user
favourite caps) lives in ``apps/favourites/services.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

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

    def placed(self) -> "FavouriteQuerySet":
        """Return the favourites that have a coordinate — everything but a region pin.

        What the map can draw. ``favourites_geojson`` is built from this:
        a region pin has no point and no feature.

        Returns:
            Filtered queryset.

        """
        return self.filter(latitude__isnull=False)

    def region_pins(self) -> "FavouriteQuerySet":
        """Return the region pins — rows whose subject is the region itself.

        The same predicate as ``Favourite.is_region_pin`` and the partial
        unique constraint, written once here so the three cannot drift.

        Returns:
            Filtered queryset.

        """
        return self.filter(
            location__isnull=True, latitude__isnull=True, region__isnull=False
        )


# ---------------------------------------------------------------------------
# Favourite
# ---------------------------------------------------------------------------


class Favourite(BaseModel):
    """A saved map pin created by an authenticated user.

    ``name`` is an optional user-supplied label — when blank, the view
    layer falls back to a coordinate-derived display string.

    ``location`` is the ``locations.Location`` this pin **is** (SNOW-704).
    A favourite reaches its coordinates and elevation through it, via
    ``location.latitude`` / ``location.longitude`` / ``location.elevation_m``.
    The row minted for a favourite carries no ``name`` and no ``kind`` —
    naming is a curation act, and a saved pin's label is the user's own
    text, which stays on the favourite.

    ``latitude``, ``longitude`` and ``elevation`` are **retained but no
    longer authoritative** — the pre-SNOW-704 storage. They stay while the
    backfill runs and are dropped in a later ticket once nothing reads
    them. Read ``location`` in new code.

    ``region`` is a best-effort MicroRegion resolution, mirroring
    ``apps.observations.models.FieldObservation.region`` — it may be null when
    the pin falls outside every known boundary.

    ``resort`` (SNOW-499) is set when this favourite was created from a
    public ``regions.Resort`` pin rather than a dropped map pin — in that
    case ``region`` is taken authoritatively from ``resort.region`` rather
    than the best-effort ``region_for_point`` match above.
    ``on_delete=SET_NULL`` degrades a resort favourite to a plain pin (the
    snapshotted name/coordinates/region survive) if the resort row is ever
    deleted; the partial-unique constraint below stops a user favouriting
    the same resort twice.

    A **region pin** (SNOW-802) is the row where ``region`` is the subject
    rather than a resolution: ``location``, ``latitude``, ``longitude``
    and ``elevation`` are all null, and ``region`` is set. It has nothing
    the map can draw and nothing a forecast can be read for — it is a
    bookmark on a bulletin — so it appears in the pins sheet and nowhere
    else. ``is_region_pin`` is the predicate; a second partial-unique
    constraint keeps one per ``(user, region)``. These rows are what the
    ``Subscription`` table's rows became.
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
    location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="favourites",
        help_text=(
            "The Location this pin is. Reaches coordinates, elevation and "
            "weather. Null only for a row the SNOW-704 backfill has not "
            "reached yet."
        ),
    )
    latitude = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "WGS-84 latitude of the saved pin. Superseded by "
            "location.latitude (SNOW-704); dropped once nothing reads it. "
            "Null on a region pin (SNOW-802), which has no coordinate."
        ),
    )
    longitude = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "WGS-84 longitude of the saved pin. Superseded by "
            "location.longitude (SNOW-704); dropped once nothing reads it. "
            "Null on a region pin (SNOW-802), which has no coordinate."
        ),
    )
    elevation = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Elevation in metres. Superseded by location.elevation_m "
            "(SNOW-704); dropped once nothing reads it. Null on a region "
            "pin (SNOW-802), which has no elevation."
        ),
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
            # SNOW-802: one region pin per (user, region). Partial on the
            # region-pin predicate — the same one ``is_region_pin`` and
            # ``FavouriteQuerySet.region_pins`` use — so placed pins that
            # merely RESOLVED to a region never collide with one another,
            # and a placed pin never collides with a region pin.
            models.UniqueConstraint(
                fields=["user", "region"],
                condition=models.Q(
                    location__isnull=True,
                    latitude__isnull=True,
                    region__isnull=False,
                ),
                name="favourite_unique_user_region_pin",
            ),
        ]

    @property
    def is_region_pin(self) -> bool:
        """Whether this row pins a region rather than a place (SNOW-802).

        No location, no coordinate, a region: the row's subject IS the
        region. Everything a placed pin renders — a point on the map, a
        forecast, an altitude-relative problem verdict — is absent here.
        """
        return (
            self.location_id is None
            and self.latitude is None
            and self.region_id is not None
        )

    def to_string(self) -> str:
        """Return a concise human-readable description of this favourite.

        Format: ``"{user} @ {name or lat,lon}"``; a region pin reads
        ``"{user} @ {region name}"``.
        """
        if self.is_region_pin:
            label = self.name or (self.region.name if self.region else "region")
        elif self.latitude is None or self.longitude is None:
            label = self.name or "unplaced pin"
        else:
            label = self.name or f"{self.latitude:.5f},{self.longitude:.5f}"
        return f"{self.user} @ {label}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
