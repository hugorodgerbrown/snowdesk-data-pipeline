"""
apps/weather/services/reference.py — The app's side of the weather service.

SNOW-1253 moved the fetch of every reference place into ingest
(``apps.ingest.public.fetch_source_weather``). This module is what the app
does around it:

  mirror_reference_locations()
      Until the service has its own editor, production's curated estate
      (``Location.objects.public()``) IS the station list. Each public row
      is given a ``source_uuid`` — its own ``uuid``, on the instance that is
      the source — and the list is handed to ``sync_source_locations``,
      which creates, updates and tombstones to match. Each place carries
      a ``document`` of what else a client needs to rebuild the row (its
      kind, short id, what3words address and the regions it is the centre
      of, SNOW-1271), which the service stores and never reads.

  apply_source_weather(row)
      Copy one ``SourceWeather`` row into the ``Weather`` row of the
      location carrying its uuid, through ``upsert_weather`` and so under
      the same past-day rule. Production calls it inline after its own
      fetch, exactly as staging will on a pull (SNOW-1272), so both write
      ``Weather`` from the same rows.

User points — favourites, trips' meeting points, route starts — are never
fetched: they borrow the nearest curated forecast (SNOW-1265), so nothing
about them reaches the service.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from django.db import transaction

from apps.data_feed.contract import dump_fields
from apps.ingest.public import (
    WEATHER_COLUMNS,
    LocationRecord,
    SourceWeather,
    sync_source_locations,
)
from apps.locations.models import Location
from apps.weather.models import Weather
from apps.weather.services.upsert import upsert_weather

logger = logging.getLogger(__name__)

# The Location columns a place's document carries, beyond the four the
# weather fetch reads (name, latitude, longitude, elevation_m).
DOCUMENT_FIELDS: tuple[str, ...] = (
    "short_id",
    "kind",
    "what3words",
    "what3words_fetched_at",
)


def _centroids() -> dict[int, list[str]]:
    """Return ``{location pk: [region codes]}`` for every region centre.

    One query over the join, reading only the region code: a region row
    carries its boundary polygon, which a prefetch would load whole.

    Returns:
        Each centre location's regions, sorted.

    """
    regions: defaultdict[int, list[str]] = defaultdict(list)
    # A region's centre is public by definition, so no public() join.
    for pk, region_id in Location.objects.filter(
        micro_regions__isnull=False
    ).values_list("pk", "micro_regions__region_id"):
        regions[pk].append(region_id)
    return {pk: sorted(codes) for pk, codes in regions.items()}


def location_document(location: Location, centroid_of: list[str]) -> dict[str, Any]:
    """Return what a client needs of ``location`` beyond the four fetch columns.

    Args:
        location: A public location, with ``DOCUMENT_FIELDS`` loaded.
        centroid_of: The codes of the regions whose centre it is.

    Returns:
        The document.

    """
    return {**dump_fields(location, DOCUMENT_FIELDS), "centroid_of": centroid_of}


def mirror_reference_locations() -> dict[str, int]:
    """Make ingest's place list equal this instance's curated estate.

    A public location with no ``source_uuid`` is given its own ``uuid`` —
    this instance is where the place is curated, so its id is the
    service's id. The whole public set is then handed to
    ``sync_source_locations``.

    Returns:
        ``sync_source_locations``' counts, plus ``linked``: how many
        locations were given a ``source_uuid`` by this call.

    """
    linked = 0
    records: list[LocationRecord] = []
    with transaction.atomic():
        centroids = _centroids()
        for location in Location.objects.public().only(
            "uuid",
            "source_uuid",
            "name",
            "latitude",
            "longitude",
            "elevation_m",
            *DOCUMENT_FIELDS,
        ):
            if location.source_uuid is None:
                location.source_uuid = location.uuid
                location.save(update_fields=["source_uuid", "updated_at"])
                linked += 1
            records.append(
                LocationRecord(
                    uuid=location.source_uuid,
                    name=location.name,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    elevation_m=location.elevation_m,
                    document=location_document(
                        location, centroids.get(location.pk, [])
                    ),
                )
            )
        counts = sync_source_locations(records)
    counts["linked"] = linked
    return counts


def apply_source_weather(row: SourceWeather) -> tuple[Weather, bool] | None:
    """Write one service row into the ``Weather`` of the location it describes.

    Args:
        row: A ``SourceWeather`` row, with its ``location`` loaded.

    Returns:
        ``upsert_weather``'s ``(weather, created)``, or None when no
        location here carries the row's uuid — a place this instance has
        not synced yet, which is logged and skipped.

    Raises:
        ImmutableWeatherRowError: The ``Weather`` row exists and its day
            has passed.

    """
    location = Location.objects.filter(source_uuid=row.location.uuid).first()
    if location is None:
        logger.info(
            "apply_source_weather: no location carries source uuid %s; skipped",
            row.location.uuid,
        )
        return None
    fields = {name: getattr(row, name) for name in WEATHER_COLUMNS}
    return upsert_weather(location, row.observed_on, **fields)
