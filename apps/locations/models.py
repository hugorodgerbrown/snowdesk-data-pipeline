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

``what3words`` (SNOW-840) is nullable for a different reason: it is a
CACHE, not a property of the place. The what3words licence forbids holding
a converted address for more than 30 calendar days, so the pair
``what3words`` / ``what3words_fetched_at`` is read back through the
``three_word_address`` property, which returns None once the stamp is
older than ``WHAT3WORDS_MAX_CACHE_AGE``. Nothing outside this module reads
the column directly.

Which coordinate on which model is exact, approximate or derived is written
down in ``docs/locations.md``.
"""

from __future__ import annotations

import datetime
import logging
import secrets
from typing import TYPE_CHECKING

from django.db import models
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser

from apps.core.models import BaseModel

logger = logging.getLogger(__name__)


def generate_short_id() -> str:
    """Mint an eleven-character URL-safe id for a ``Location`` (SNOW-797).

    ``secrets.token_urlsafe(8)`` — 64 bits, the generator and width
    ``BulletinShare`` and ``RouteShare`` already use. It is the field's
    ``default=`` rather than a ``save()`` override or a per-call-site mint,
    following ``BaseModel.uuid``: a default is the one place a creation
    path cannot forget. Over a few hundred rows a collision is vanishingly
    unlikely, and the unique constraint is the backstop.

    Never all digits: ``/weather/<int:location_id>/`` — the legacy redirect
    — and ``/weather/<short_id>/`` share a prefix, and an all-digit token
    would be claimed by the integer route. ``ShortIdConverter`` rejects the
    same shape, so the two routes are disjoint by construction rather than
    by ordering. The re-draw fires about once in 10**9 mints.

    Returns:
        A fresh eleven-character token.

    """
    while True:
        token = secrets.token_urlsafe(8)
        if not token.isdigit():
            return token


# The what3words API licence permits caching a converted address "solely
# for improving the performance of your Product" and "in no event ... more
# than 30 calendar days". This is a CEILING IMPOSED BY THE LICENCE, not a
# tuning knob — it is deliberately NOT a setting, because an env var is an
# invitation to raise it, and raising it is a licence breach rather than a
# performance decision. See
# docs/decisions/what3words-cache-expires-at-thirty-days.md.
WHAT3WORDS_MAX_CACHE_AGE = datetime.timedelta(days=30)


# ---------------------------------------------------------------------------
# QuerySet / Manager
# ---------------------------------------------------------------------------


# The two halves of "which locations matter", written once so ``public()``
# and ``active()`` cannot drift apart. ``active()`` is ``public()`` plus
# favourites; if these were spelled out separately in each method, a clause
# added to one would silently not reach the other — and the direction that
# fails is the dangerous one, since a public feed built from a stale
# predicate leaks private pins.
_CURATED = models.Q(resort_locations__isnull=False) | models.Q(
    micro_regions__isnull=False
)
_FAVOURITED = models.Q(favourites__isnull=False)


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

    def public(self) -> "LocationQuerySet":
        """Return the locations anyone may see — the curated estate.

        A location is public when a ``ResortLocation`` or a
        ``MicroRegion.centroid_location`` reaches it: someone curated it as
        a place, and it is already visible on a resort page or a bulletin.
        **A ``Favourite`` does not make a location public**, and neither
        does a ``FieldObservation``.

        This is the set the map's weather feed renders (SNOW-761). It is
        deliberately narrower than ``active()``: a favourite is one
        person's private pin, and putting it on a public feed would show a
        stranger's saved place — and its coordinates — to everyone. The
        pre-SNOW-762 ``/api/forecast-weather.geojson`` excluded
        favourite-only points for exactly this reason, and that contract
        survives here.

        ``.distinct()`` because a location reached from both sides — a
        resort's village that is also a region centroid — joins twice.

        Returns:
            Filtered queryset of locations reachable from a resort or a
            region centroid.

        """
        return self.filter(_CURATED).distinct()

    def active(self) -> "LocationQuerySet":
        """Return the locations worth spending an upstream call on.

        Every ``public()`` location, plus the ones a ``Favourite`` reaches.
        This is the set ``fetch_weather`` walks, so it is also the set that
        costs money — one Open-Meteo call per row per run, four runs a day.

        **``public()`` and this method are not interchangeable.** This one
        answers "what do we pay to fetch"; ``public()`` answers "what may
        anyone see". A favourite is in the first and not the second, so
        rendering a public surface from ``active()`` would leak a private
        pin. Both assertions live in ``tests/locations/test_models.py``.

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

        Returns:
            Filtered queryset of locations reachable from a resort, a
            region centroid or a favourite.

        """
        return self.filter(_CURATED | _FAVOURITED).distinct()

    def visible_to(
        self, user: "AbstractBaseUser | AnonymousUser"
    ) -> "LocationQuerySet":
        """Return the locations this user may open a page for.

        Every ``public()`` location, plus the ones this user's own
        ``Favourite`` rows reach — and nobody else's (SNOW-783).

        The forecast page is reached from two places with different
        audiences. The map's weather symbol only ever points at
        ``public()``, so that path is unchanged. The favourite card points
        at the pin it is describing, which is private by construction:
        ``public()`` excludes favourite locations precisely so a public
        feed cannot leak one. Refusing the owner their own pin's forecast
        would be reading that contract backwards — the rule is that a
        *stranger* may not see it.

        Anonymous users get ``public()`` exactly, with no favourite branch
        to widen it.

        Args:
            user: The requesting user, authenticated or not.

        Returns:
            Filtered queryset of locations this user may view.

        """
        if not user.is_authenticated:
            return self.public()
        return self.filter(_CURATED | models.Q(favourites__user=user)).distinct()

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

    short_id = models.CharField(
        max_length=16,
        unique=True,
        null=True,
        blank=True,
        editable=False,
        default=generate_short_id,
        help_text=(
            "Eleven-character opaque URL identifier — /weather/<short_id>/ and "
            "the id weather.geojson emits (SNOW-797). Opaque rather than a slug "
            "because most public locations are unnamed region centroids. Null "
            "only on a row backfill_location_short_ids has not reached yet."
        ),
    )
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
    what3words = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text=(
            "Cached three word address, stored WITHOUT the /// prefix — "
            "'filled.count.soap'. Filled lazily by fill_what3words on a "
            "read path, and EXPIRES: read it through three_word_address, "
            "never directly, because the licence caps the cache at 30 days."
        ),
    )
    what3words_fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When what3words above was converted. The expiry clock, not an "
            "audit stamp — a row older than 30 days is re-converted."
        ),
    )
    objects = LocationQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    @property
    def three_word_address(self) -> str | None:
        """Return the cached three word address, or None if it has expired.

        THE LICENCE BOUNDARY, expressed as code. ``what3words`` is a cache
        rather than a fact about the place: the what3words terms permit
        holding a converted address "in no event ... more than 30 calendar
        days", so a stored value stops being ours to show the moment
        ``WHAT3WORDS_MAX_CACHE_AGE`` elapses. Every caller reads the
        address through here and nothing outside this model reads
        ``self.what3words`` directly, so there is exactly one place the cap
        can be enforced and exactly one place it could be broken.

        Expiry is a READ-TIME test rather than a sweep. A cron that deleted
        stale rows would leave a window between the row going stale and the
        sweep running, in which a page could still render it; testing on
        read closes that window by construction, and costs nothing, since
        the row is already loaded. ``fill_what3words`` re-converts what
        this returns None for.

        Returned WITHOUT the ``///`` prefix, which is presentation and
        belongs to the template.

        Returns:
            The address, e.g. ``"filled.count.soap"``, or None when it has
            never been fetched, was fetched with no stamp, or was fetched
            more than 30 days ago.

        """
        if not self.what3words or self.what3words_fetched_at is None:
            return None
        if timezone.now() - self.what3words_fetched_at > WHAT3WORDS_MAX_CACHE_AGE:
            return None
        return self.what3words

    def get_absolute_url(self) -> str:
        """Return the location's weather page — ``/weather/<short_id>/``.

        Document two of the two-document IA (SNOW-795): one location, one
        day. Keyed on the opaque short id, never the primary key.

        Returns ``""`` when ``short_id`` is null — a row that
        ``backfill_location_short_ids`` has not reached yet has no page, and
        saying so is the only answer a caller can use. SNOW-810: reversing
        with ``short_id=None`` raises ``NoReverseMatch``, which is a 500 on
        every surface that renders a link to a location, so an environment
        between the SNOW-797 migration and its backfill served a 500 from
        the favourites list partial and from ``/sitemap.xml``. The window is
        a real state — the field is nullable on purpose until a later
        migration tightens it — so it has to degrade rather than raise.

        Callers that render a LINK need nothing further: the row partials
        already drop an ``href`` they were handed empty (SNOW-800 built that
        path for a favourite whose pin has no location at all). Callers that
        treat the return as a DESTINATION — a redirect target, a sitemap
        entry — must check it, because "" is not one.
        """
        if not self.short_id:
            return ""
        return reverse("public:location_weather", kwargs={"short_id": self.short_id})

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
