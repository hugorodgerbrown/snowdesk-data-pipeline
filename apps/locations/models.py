"""
apps/locations/models.py — Database models for the locations application.

Defines the two models that make ``Location`` the domain primitive
(``docs/decisions/location-is-the-primitive.md``):

* ``Location`` — a point on the map that we keep. A resort's village, its
  mid-station and its peak; a saved favourite; a field observation; a region
  centroid. One table, because a curated place is simply a ``Location`` that
  has a ``name`` — there is no separate curated-place model, so Mont Fort is
  one row that Verbier, Nendaz, Veysonnaz and Thyon all reference and the
  sharing falls out of the model rather than needing a table to express it.
* ``ResortLocation`` — the explicit through model joining a ``Resort`` to its
  locations, carrying the ``role`` each plays *for that resort*.

**A row exists for a place we keep.** A transient coordinate — a live GPS
fix, a GPX trackpoint — is resolved *against* locations without minting one.
``Route.points`` stays a JSONField of simplified trackpoints; those are
geometry, not places. "Everything is a location" means every *place*, not
every *coordinate*.

``elevation_m`` and ``forecast_cell`` are both nullable and populated
out-of-band: resolving them needs an Open-Meteo call, which cannot ride on a
model save. ``link_location_forecast_cells`` (SNOW-701) fills them.

Which coordinate on which model is exact, approximate or derived is written
down in ``docs/locations.md``.
"""

from __future__ import annotations

import logging

from django.db import models

from apps.core.models import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QuerySet / Manager
# ---------------------------------------------------------------------------


class LocationQuerySet(models.QuerySet["Location"]):
    """Custom queryset for Location."""

    def named(self) -> "LocationQuerySet":
        """Return curated locations — those carrying a name.

        Naming is a curation act: a location minted from a favourite or a
        field observation carries no name and no kind, so this is the
        boundary between the curated estate and the anonymous points that
        exist because a user put something somewhere.

        Returns:
            Filtered queryset of locations with a non-empty name.

        """
        return self.exclude(name="")

    def anonymous(self) -> "LocationQuerySet":
        """Return locations carrying no name — the complement of ``named()``.

        Returns:
            Filtered queryset of locations with an empty name.

        """
        return self.filter(name="")

    def unresolved(self) -> "LocationQuerySet":
        """Return locations still missing their elevation or forecast cell.

        The candidate set for ``link_location_forecast_cells`` (SNOW-701),
        which resolves both in one Open-Meteo round trip. Excluding rows
        that already have both is what makes a second run a no-op.

        Returns:
            Filtered queryset of locations with a null elevation or a null
            forecast cell.

        """
        return self.filter(
            models.Q(elevation_m__isnull=True) | models.Q(forecast_cell__isnull=True)
        )


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


class Location(BaseModel):
    """A point on the map that Snowdesk keeps.

    The locus of the model: everything that is *somewhere* reaches one of
    these. Coordinates are exact WGS-84 and immovable — a location does not
    move, and a place at a different coordinate is a different location.

    A curated place has a ``name`` and usually a ``kind``; a location minted
    from a favourite or an observation has neither, and is an anonymous
    point like any other. Both live in this table.

    ``forecast_cell`` points at the quantised cell at which Open-Meteo is
    called for this location. Many locations share one cell — that is the
    cell's entire purpose (``docs/decisions/forecast-point-quantisation.md``)
    — so the forecast is *for a location* and the cell is only how we avoid
    paying twice for two points 300 m apart.
    """

    class KIND(models.TextChoices):
        """What sort of place this is, independent of any resort.

        Values are UPPER_CASE identifiers; labels are in British English.
        ``VILLAGE`` — the settlement, where someone arrives and sleeps.
        ``MID``     — a mid-mountain point, typically a lift station.
        ``PEAK``    — the top: a summit or the highest lift-served point.

        Describes the *place*, not its relationship to any resort — Mont
        Fort is a peak whoever is looking at it. The per-resort relationship
        is ``ResortLocation.role``, and the two genuinely differ: a point
        can be the top of one linked area and the mid-station of another.
        """

        VILLAGE = "VILLAGE", "Village"
        MID = "MID", "Mid-mountain"
        PEAK = "PEAK", "Peak"

    name = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text=(
            "Curated name, e.g. 'Mont Fort'. Empty for a location minted "
            "from a favourite or an observation — naming is a curation act."
        ),
    )
    kind = models.CharField(
        max_length=16,
        choices=KIND.choices,
        blank=True,
        default="",
        help_text="What sort of place this is. Empty alongside an empty name.",
    )
    latitude = models.FloatField(
        help_text="Exact WGS-84 latitude. Immovable.",
    )
    longitude = models.FloatField(
        help_text="Exact WGS-84 longitude. Immovable.",
    )
    elevation_m = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Elevation in metres, resolved once via fetch_elevation. Null "
            "until link_location_forecast_cells has run."
        ),
    )
    forecast_cell = models.ForeignKey(
        "weather.ForecastPoint",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="locations",
        help_text=(
            "Quantised cell at which Open-Meteo is called for this "
            "location. Shared with every other location in the cell."
        ),
    )

    objects = LocationQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a concise human-readable description of this location.

        Named:     ``Mont Fort (Peak) 46.10361,7.29889 @3328m``
        Anonymous: ``46.09610,7.22860``

        The coordinate is always present because it is the only thing every
        location has — a name, a kind and an elevation are all optional.

        Returns:
            The description.

        """
        coordinates = f"{self.latitude:.5f},{self.longitude:.5f}"
        parts = []
        if self.name:
            parts.append(self.name)
        if self.kind:
            parts.append(f"({self.get_kind_display()})")
        parts.append(coordinates)
        if self.elevation_m is not None:
            parts.append(f"@{self.elevation_m:.0f}m")
        return " ".join(parts)

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# ResortLocation
# ---------------------------------------------------------------------------


class ResortLocationQuerySet(models.QuerySet["ResortLocation"]):
    """Custom queryset for ResortLocation."""

    def primary(self) -> "ResortLocationQuerySet":
        """Return the links a resort leads with.

        The resort page's hero reads this one. It defaults to the ``BASE``
        role, which preserves exactly today's behaviour: the page currently
        shows village weather, and that remains the right thing to lead
        with — it is where someone arrives.

        Returns:
            Filtered queryset of primary links.

        """
        return self.filter(is_primary=True)


class ResortLocation(BaseModel):
    """One resort's link to one location, and the role it plays there.

    An explicit through model rather than a bare M2M because the
    relationship carries data: which role the location plays *for this
    resort*, and whether it is the one the resort leads with.

    ``role`` is not a duplicate of ``Location.kind``. ``kind`` describes the
    place itself; ``role`` describes this relationship. Attelas is plausibly
    the top of a small linked area and the mid-station of Verbier, and the
    same physical point must be able to be both — which is the whole reason
    this is a many-to-many rather than a repeated foreign key.

    ``PROTECT`` on ``location`` and ``CASCADE`` on ``resort``: deleting
    Verbier must not take Mont Fort with it while Nendaz, Veysonnaz and
    Thyon still reference it, but deleting a resort should clear its own
    links.
    """

    class ROLE(models.TextChoices):
        """What this location is to this resort.

        Values are UPPER_CASE identifiers; labels are in British English.
        ``BASE`` — where the resort starts, normally the village.
        ``MID``  — a mid-mountain point on this resort's terrain.
        ``TOP``  — the high point of this resort's terrain.
        """

        BASE = "BASE", "Base"
        MID = "MID", "Mid-mountain"
        TOP = "TOP", "Top"

    resort = models.ForeignKey(
        "regions.Resort",
        on_delete=models.CASCADE,
        related_name="resort_locations",
        help_text="The resort this link belongs to.",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="resort_locations",
        help_text="The location, which other resorts may also reference.",
    )
    role = models.CharField(
        max_length=8,
        choices=ROLE.choices,
        help_text="What this location is to this resort.",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text=(
            "Whether the resort page leads with this location. Normally the "
            "BASE link — the village is where someone arrives."
        ),
    )

    objects = ResortLocationQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        unique_together = [("resort", "location")]

    def to_string(self) -> str:
        """Return a concise human-readable description of this link.

        Format: ``Verbier (CH-1000) -> Mont Fort (Peak) 46.10361,7.29889 @3328m [TOP]``

        Both ends render through their own ``__str__``, so the resort half
        carries its region id.

        Returns:
            The description.

        """
        return f"{self.resort} -> {self.location} [{self.role}]"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
