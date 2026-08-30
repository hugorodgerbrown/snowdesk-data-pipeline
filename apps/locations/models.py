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

``elevation_m`` is nullable and populated out-of-band: resolving it needs an
Open-Meteo elevation call, which cannot ride on a model save.
``link_region_centroid_locations`` fills it for region centroids, and
``apps.favourites.services`` for a location minted from a favourite.

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

        The boundary is load-bearing for the sheets. ``import_locations``
        deletes within it, ``dump_locations_sheets`` emits within it, and
        the in-map editor writes within it — so a ``ResortLocation``
        pointing at a location *outside* it cannot be written to the
        links sheet and will not survive a round trip. The admin's
        inline can still create one; ``dump_locations_sheets`` warns
        rather than dropping it in silence.

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

    def active(self) -> "LocationQuerySet":
        """Return the locations worth spending an upstream call on.

        A location is active when something public or saved reaches it: a
        ``ResortLocation``, a ``MicroRegion.centroid_location``, or a
        ``Favourite``. This is the set ``fetch_weather`` walks, so it is
        also the set that costs money — one Open-Meteo call per row per run,
        four runs a day.

        **A location reached only by a ``FieldObservation`` is excluded, and
        that is the point of the method.** A field report is a user saying
        "this happened here"; it is not a request for a forecast. Including
        one would mint a billable call from a stranger dropping a pin, and
        would let a private report surface a forecast panel on a public
        page. The exclusion is asserted in ``tests/locations/test_models.py``
        rather than left to this docstring.

        ``.distinct()`` because a location referenced from two sides — a
        resort's village that someone has also favourited — joins twice and
        must still be fetched once.

        One definition, called from both the fetch and SNOW-761's map feed:
        both are asking "is this a public place or a private pin", and two
        implementations would drift until a private pin leaked into a
        public feed.

        Returns:
            Filtered queryset of locations reachable from a resort, a
            region centroid or a favourite.

        """
        return self.filter(
            models.Q(resort_locations__isnull=False)
            | models.Q(micro_regions__isnull=False)
            | models.Q(favourites__isnull=False)
        ).distinct()

    def unresolved(self) -> "LocationQuerySet":
        """Return locations still missing their elevation.

        A row lands here when it was minted without an elevation, or when
        the location editor cleared one because the pin moved. Excluding
        rows that already carry an elevation is what makes a second
        resolution pass a no-op.

        Returns:
            Filtered queryset of locations with a null elevation.

        """
        return self.filter(elevation_m__isnull=True)


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


class Location(BaseModel):
    """A point on the map that Snowdesk keeps.

    The locus of the model: everything that is *somewhere* reaches one of
    these. Coordinates are exact WGS-84 and, in normal operation,
    immovable — a location does not drift, nothing in the request path
    moves one, and a place at a different coordinate is a different
    location rather than the same one relocated.

    **Correction is the exception, and it is deliberate.** A mis-placed
    pin is fixed either in the admin, which has always allowed it, or in
    the in-map curation editor (``?edit=locations``, SNOW-755). Both are
    a re-placement of the same row — the place was always where it now
    says it is, and the old coordinate was simply wrong — not a new
    place, so the links pointing at it are still correct and stay.
    ``edit_location_save`` clears ``elevation_m`` when the pin actually
    moves, because it was resolved from where the row used to claim to be.

    A curated place has a ``name`` and usually a ``kind``; a location minted
    from a favourite or an observation has neither, and is an anonymous
    point like any other. Both live in this table.

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
        help_text=(
            "Exact WGS-84 latitude. Corrected only here or in the location "
            "editor — a correction re-places the same row, it does not "
            "make a new place."
        ),
    )
    longitude = models.FloatField(
        help_text=(
            "Exact WGS-84 longitude. Corrected only here or in the location "
            "editor — a correction re-places the same row, it does not "
            "make a new place."
        ),
    )
    elevation_m = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Elevation in metres, resolved once via fetch_elevation. Null "
            "until an out-of-band resolution pass has run."
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
