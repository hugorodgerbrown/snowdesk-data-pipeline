"""
apps/weather/dev_views.py — Development-only Open-Meteo mirror view.

``openmeteo_mirror``
    Replays ``apps/weather/local_mirrors/openmeteo_archive.ndjson`` in an
    Open-Meteo-compatible response shape, resolved by latitude/longitude
    to a Region.
    Returns 404 (JSON body) if the requested region or date range is not
    fully present in the archive — fail loudly so missing fixtures surface
    in tests.

The view is wired up only when ``settings.DEBUG`` is true (see
``config/urls.py``); production never imports this module. The companion
command ``fetch_weather --source local-mirror`` uses it to replay
committed sample data end-to-end through the production fetch path. The
SLF and ALBINA mirrors live in ``apps.bulletins.dev_views``.
"""

import datetime
import logging
from typing import Literal, cast

from django.conf import settings
from django.http import HttpRequest, JsonResponse

from apps.regions.models import Centre, MicroRegion
from apps.weather.services.openmeteo_archive import (
    read_archive as read_openmeteo_archive,
)

logger = logging.getLogger(__name__)


def openmeteo_mirror(
    request: HttpRequest,
    kind: Literal["forecast", "archive"],
) -> JsonResponse:
    """
    Replay apps/weather/local_mirrors/openmeteo_archive.ndjson in Open-Meteo shape.

    Accepts ``latitude``, ``longitude``, ``start_date``, and ``end_date``
    query parameters. Extra query parameters (``daily``, ``timezone``, etc.)
    are accepted and ignored — the mirror always returns
    ``weather_code``, ``sunrise``, and ``sunset``, plus
    ``temperature_2m_max``, ``temperature_2m_min``, and ``snowfall_sum``
    when the underlying archive record carries them (``None`` — replayed as
    JSON ``null`` — for older records written before SNOW-571).

    Resolves ``(latitude, longitude)`` to a Region by matching
    ``str(region.centre["lat"])`` and ``str(region.centre["lon"])`` against
    the query string values. This is the exact stringification used by
    ``weather_fetcher.fetch_weather_for_region``, so the round-trip
    is bit-exact.

    Returns a 404 JSON response if:
    - No region matches the lat/lon pair.
    - The archive has no records for the resolved region.
    - Any date in the requested range is missing from the archive.

    Args:
        request: The incoming Django request.
        kind: Either ``"forecast"`` or ``"archive"``; bound by the URL pattern.
            Both URL variants serve the same archive — the distinction mirrors
            the upstream Open-Meteo URL structure but is not enforced here.

    Returns:
        A ``JsonResponse`` with an Open-Meteo-compatible ``daily`` payload on
        success, or a ``JsonResponse`` with ``status=404`` on failure.

    """
    latitude = request.GET.get("latitude", "")
    longitude = request.GET.get("longitude", "")
    start_date_str = request.GET.get("start_date", "")
    end_date_str = request.GET.get("end_date", "")

    # Resolve lat/lon → MicroRegion using the same str() stringification as the fetcher.
    matched_region: MicroRegion | None = None
    for region in MicroRegion.objects.exclude(centre__isnull=True):
        centre = cast(Centre, region.centre)
        if str(centre["lat"]) == latitude and str(centre["lon"]) == longitude:
            matched_region = region
            break

    if matched_region is None:
        logger.debug(
            "openmeteo_mirror: no region found for lat=%s lon=%s kind=%s",
            latitude,
            longitude,
            kind,
        )
        return JsonResponse(
            {"error": f"No region found for latitude={latitude} longitude={longitude}"},
            status=404,
        )

    try:
        start_date = datetime.date.fromisoformat(start_date_str)
        end_date = datetime.date.fromisoformat(end_date_str)
    except ValueError:
        return JsonResponse(
            {"error": "start_date and end_date must be YYYY-MM-DD"},
            status=400,
        )

    if (end_date - start_date).days > 366:
        return JsonResponse(
            {"error": "date range exceeds 366 days"},
            status=400,
        )

    # Build a lookup of date → record for this region.
    archive_by_date: dict[str, dict] = {
        record["date"]: record
        for record in read_openmeteo_archive(settings.OPENMETEO_ARCHIVE_PATH)
        if record["region_id"] == matched_region.region_id
    }

    # Enumerate the requested date range and check coverage.
    requested_dates: list[str] = []
    current = start_date
    while current <= end_date:
        requested_dates.append(current.isoformat())
        current += datetime.timedelta(days=1)

    missing = [d for d in requested_dates if d not in archive_by_date]
    if missing:
        logger.debug(
            "openmeteo_mirror: missing %d date(s) for region=%s kind=%s",
            len(missing),
            matched_region.region_id,
            kind,
        )
        return JsonResponse(
            {
                "error": (
                    f"Archive does not contain data for region "
                    f"{matched_region.region_id} on {len(missing)} date(s): "
                    f"{missing[:5]}"
                )
            },
            status=404,
        )

    # Synthesise the Open-Meteo response shape. temperature_2m_max/min and
    # snowfall_sum are read via .get() rather than direct indexing — a
    # record from an older archive written before SNOW-571 carries no such
    # keys, and the mirror must replay that as JSON null, not KeyError.
    payload = {
        "daily": {
            "time": requested_dates,
            "weather_code": [
                archive_by_date[d]["weather_code"] for d in requested_dates
            ],
            "sunrise": [archive_by_date[d]["sunrise"] for d in requested_dates],
            "sunset": [archive_by_date[d]["sunset"] for d in requested_dates],
            "temperature_2m_max": [
                archive_by_date[d].get("temperature_2m_max") for d in requested_dates
            ],
            "temperature_2m_min": [
                archive_by_date[d].get("temperature_2m_min") for d in requested_dates
            ],
            "snowfall_sum": [
                archive_by_date[d].get("snowfall_sum") for d in requested_dates
            ],
        }
    }

    logger.debug(
        "openmeteo_mirror: serving region=%s kind=%s start=%s end=%s (%d day(s))",
        matched_region.region_id,
        kind,
        start_date_str,
        end_date_str,
        len(requested_dates),
    )
    return JsonResponse(payload)
