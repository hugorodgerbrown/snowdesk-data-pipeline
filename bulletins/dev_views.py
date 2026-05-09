"""
bulletins/dev_views.py — Development-only mirror views for bulletin sources.

Contains two mirrors:

``slf_mirror``
    Replays ``sample_data/slf_archive.ndjson`` with the same
    ``limit``/``offset`` paging contract as the upstream SLF CAAML API:
    reverse-chronological by ``publicationTime``, paginated by offset,
    fewer-than-``limit`` items signals the last page.

``openmeteo_mirror``
    Replays ``sample_data/openmeteo_archive.ndjson`` in an Open-Meteo-
    compatible response shape, resolved by latitude/longitude to a Region.
    Returns 404 (JSON body) if the requested region or date range is not
    fully present in the archive — fail loudly so missing fixtures surface
    in tests.

Both views are wired up only when ``settings.DEBUG`` is true (see
``config/urls.py``); production never imports this module. Companion
commands ``fetch_bulletins --source local-mirror`` and ``fetch_weather
--source local-mirror`` use these views to replay committed sample data
end-to-end through the production fetch paths.
"""

import datetime
import logging
from typing import Literal

from django.conf import settings
from django.http import HttpRequest, JsonResponse

from bulletins.services.data_fetcher import PAGE_SIZE
from bulletins.services.openmeteo_archive import read_archive as read_openmeteo_archive
from bulletins.services.slf_archive import read_archive
from regions.models import Region

logger = logging.getLogger(__name__)


def slf_mirror(request: HttpRequest, lang: str) -> JsonResponse:
    """
    Serve a slice of the on-disk SLF archive in upstream-compatible shape.

    Args:
        request: The incoming Django request; ``?limit`` and ``?offset``
            query params are honoured with the same semantics as the
            upstream SLF API.
        lang: Accepted for URL-shape parity with upstream but ignored
            (the archive only stores English bulletins).

    Returns:
        A ``JsonResponse`` containing the requested page as a flat
        JSON list, descending by ``publicationTime``.

    """
    try:
        limit = int(request.GET.get("limit", PAGE_SIZE))
        offset = int(request.GET.get("offset", 0))
    except ValueError:
        return JsonResponse({"error": "limit and offset must be integers"}, status=400)

    records = list(read_archive(settings.SLF_ARCHIVE_PATH))
    records.sort(key=lambda r: r["publicationTime"], reverse=True)
    page = records[offset : offset + limit]

    logger.debug(
        "slf_mirror serving lang=%s limit=%d offset=%d -> %d record(s) "
        "(archive total=%d)",
        lang,
        limit,
        offset,
        len(page),
        len(records),
    )
    return JsonResponse(page, safe=False)


def openmeteo_mirror(
    request: HttpRequest,
    kind: Literal["forecast", "archive"],
) -> JsonResponse:
    """
    Replay sample_data/openmeteo_archive.ndjson in upstream-compatible shape.

    Accepts ``latitude``, ``longitude``, ``start_date``, and ``end_date``
    query parameters. Extra query parameters (``daily``, ``timezone``, etc.)
    are accepted and ignored — the mirror always returns
    ``weather_code``, ``sunrise``, and ``sunset``.

    Resolves ``(latitude, longitude)`` to a Region by matching
    ``str(region.centre["lat"])`` and ``str(region.centre["lon"])`` against
    the query string values. This is the exact stringification used by
    ``weather_fetcher.fetch_weather_for_region`` (line 144), so the round-trip
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

    # Resolve lat/lon → Region using the same str() stringification as the fetcher.
    matched_region: Region | None = None
    for region in Region.objects.exclude(centre__isnull=True):
        if (
            str(region.centre["lat"]) == latitude  # type: ignore[index]
            and str(region.centre["lon"]) == longitude  # type: ignore[index]
        ):
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

    # Synthesise the Open-Meteo response shape.
    payload = {
        "daily": {
            "time": requested_dates,
            "weather_code": [
                archive_by_date[d]["weather_code"] for d in requested_dates
            ],
            "sunrise": [archive_by_date[d]["sunrise"] for d in requested_dates],
            "sunset": [archive_by_date[d]["sunset"] for d in requested_dates],
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
