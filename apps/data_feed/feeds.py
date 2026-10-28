"""
apps/data_feed/feeds.py — What each data feed serves, and how it is paged.

One ``Feed`` per endpoint under ``/api/data/v1/``: the queryset it reads
and how one row becomes one item. The ``Feed`` type and ``page()``, the
paging rule all four share, live in ``apps/ingest/api/paging.py``; the
``bulletins`` feed itself is ingest's (``apps/ingest/api/bulletins.py``,
SNOW-1250).

**What is served is an allowlist, not the database.** The four querysets
below are the only reads, and none of them touches a user-owned table:

* ``bulletins`` — every ``SourceBulletin`` ingest has stored: the
  provider's CAAML as normalised, plus its PDF link. Nothing derived
  (region links, day ratings, groupings, the render model) is sent; a
  client rebuilds those with its own ``upsert_bulletin``, so they are
  always its own code's output.
* ``weather`` — ``Weather`` rows for ``Location.objects.public()`` only.
  A favourite's pin is an active location too (it is fetched), but it is
  one person's saved place, so its weather never leaves production.
* ``locations`` — ``Location.objects.public()``: the curated estate a
  resort, a resort pin or a region centroid reaches. Never an anonymous
  point minted from a favourite or a field report.
* ``resorts`` — every ``Resort`` with a slug, with its region, pin, passes
  and location links as natural keys.

Paging is a keyset on ``(updated_at, id)``; see ``apps/ingest/api/paging.py``.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Prefetch, QuerySet

from apps.data_feed import contract
from apps.ingest.public import (
    BULLETINS_FEED,
    DAY_RATINGS_FEED,
    LOCATIONS_FEED,
    PASSES_FEED,
    RESORTS_FEED,
    WEATHER_FEED_V2,
    Feed,
)
from apps.locations.models import Location, ResortLocation
from apps.regions.models import MicroRegion, Pass, Resort
from apps.weather.models import Weather

# ---------------------------------------------------------------------------
# Serialisers — one row, one item
# ---------------------------------------------------------------------------


def serialise_weather(weather: Weather) -> dict[str, Any]:
    """Return one location-day of weather, keyed by location uuid and day.

    Args:
        weather: A row with its ``location`` selected.

    Returns:
        The wire item.

    """
    return {
        "location": str(weather.location.uuid),
        "observed_on": contract.to_wire(weather.observed_on),
        "updated_at": contract.to_wire(weather.updated_at),
        "fields": contract.dump_fields(weather, contract.WEATHER_FIELDS),
    }


def serialise_location(location: Location) -> dict[str, Any]:
    """Return a curated location, with the regions whose centroid it is.

    Args:
        location: A row with ``micro_regions`` prefetched (slim).

    Returns:
        The wire item.

    """
    return {
        "uuid": str(location.uuid),
        "updated_at": contract.to_wire(location.updated_at),
        "fields": contract.dump_fields(location, contract.LOCATION_FIELDS),
        "centroid_of": sorted(r.region_id for r in location.micro_regions.all()),
    }


def serialise_resort(resort: Resort) -> dict[str, Any]:
    """Return a resort with its relations as natural keys.

    Args:
        resort: A row with ``region`` and ``location`` selected and
            ``passes`` and ``resort_locations`` prefetched.

    Returns:
        The wire item.

    """
    return {
        "slug": resort.slug,
        "updated_at": contract.to_wire(resort.updated_at),
        "fields": contract.dump_fields(resort, contract.RESORT_FIELDS),
        "region": resort.region.region_id,
        "location": str(resort.location.uuid) if resort.location else None,
        "passes": [
            {"slug": p.slug, "name": p.name, "website": p.website}
            for p in resort.passes.all()
        ],
        "locations": [
            {
                "location": str(link.location.uuid),
                "role": link.role,
                "is_primary": link.is_primary,
            }
            for link in resort.resort_locations.all()
        ],
    }


# ---------------------------------------------------------------------------
# Querysets — the allowlist
# ---------------------------------------------------------------------------


def _weather() -> QuerySet[Weather]:
    """Return weather for the public (curated) locations only — never a favourite's."""
    return Weather.objects.filter(
        location__in=Location.objects.public().values("pk")
    ).select_related("location")


def _locations() -> QuerySet[Location]:
    """Return the curated estate, with the regions each is the centroid of."""
    return Location.objects.public().prefetch_related(
        # The FK must be loaded: a prefetch matches each region back to its
        # location through it, and a deferred one is fetched row by row.
        Prefetch(
            "micro_regions",
            queryset=MicroRegion.objects.only("pk", "region_id", "centroid_location"),
        )
    )


def _resorts() -> QuerySet[Resort]:
    """Return every resort with a slug — a slug is the resort's key on the wire."""
    # The region and pin are prefetched slim rather than joined: a
    # MicroRegion row carries its boundary polygon, and the item needs only
    # the region's code.
    return Resort.objects.exclude(slug__isnull=True).prefetch_related(
        Prefetch("region", queryset=MicroRegion.objects.only("pk", "region_id")),
        Prefetch("location", queryset=Location.objects.only("pk", "uuid")),
        Prefetch("passes", queryset=Pass.objects.only("pk", "slug", "name", "website")),
        Prefetch(
            "resort_locations",
            queryset=ResortLocation.objects.select_related("location").only(
                "pk", "resort_id", "role", "is_primary", "location__uuid"
            ),
        ),
    )


FEEDS: dict[str, Feed] = {
    "bulletins": BULLETINS_FEED,
    "weather": Feed("weather", _weather, serialise_weather),
    "locations": Feed("locations", _locations, serialise_location),
    "resorts": Feed("resorts", _resorts, serialise_resort),
}

# /api/data/v2/: the reference-data service's own tables, every item keyed
# by the service's uuid (a client's source_uuid) rather than production's
# own ids, tombstones included. ``weather`` is SNOW-1253's; ``locations``,
# ``passes`` and ``resorts`` SNOW-1271's; ``day-ratings`` SNOW-1270's, keyed
# by region code and day. v1 stays served until staging's pull has moved
# (SNOW-1272).
FEEDS_V2: dict[str, Feed] = {
    "weather": WEATHER_FEED_V2,
    "locations": LOCATIONS_FEED,
    "passes": PASSES_FEED,
    "resorts": RESORTS_FEED,
    "day-ratings": DAY_RATINGS_FEED,
}
