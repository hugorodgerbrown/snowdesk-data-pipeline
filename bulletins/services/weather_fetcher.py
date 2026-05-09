"""
bulletins/services/weather_fetcher.py — Fetching and persisting Open-Meteo weather data.

Contains four functions:

  fetch_weather_for_region(region, target_date, *, commit)
      Fetches today's (or any single day's) weather for one region from the
      Open-Meteo forecast endpoint. Returns ``(WeatherSnapshot, created)`` when
      ``commit=True``, or ``None`` when ``commit=False``.

  fetch_all_regions(target_date, *, commit)
      Calls fetch_weather_for_region for every Region that has a centre
      coordinate; returns summary counters {created, updated, failed, skipped}.

  fetch_archive_for_region(region, start_date, end_date, *, commit)
      Fetches historical weather for a date range from the Open-Meteo archive
      endpoint. Returns a list of ``(WeatherSnapshot, created)`` tuples when
      ``commit=True``, or an empty list when ``commit=False``.

  backfill_all_regions(start_date, end_date, *, commit)
      Calls fetch_archive_for_region for every Region that has a centre
      coordinate; returns summary counters {created, updated, failed, skipped}.

Uses ``requests`` with a 30-second timeout (matching data_fetcher.py's pattern).
Per-region HTTP failures bubble up from the single-region functions; the wrapper
functions catch them, log a warning, and continue so that one bad region does
not abort the entire batch.

When ``commit=False``, the HTTP requests still execute (real API probe) but no
rows are written.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from typing import Any, cast

import requests
from django.utils import timezone as django_timezone

from bulletins.models import WeatherSnapshot
from regions.models import Region

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT = 30  # seconds


def _parse_dt(value: str) -> datetime:
    """
    Parse an ISO-8601 datetime string (with timezone offset) into an aware datetime.

    Open-Meteo returns sunrise/sunset as ISO-8601 strings with a UTC offset
    when ``timezone=auto`` is specified — e.g. ``"2026-05-01T05:32+02:00"``.
    We preserve that offset (local-time tz-aware) rather than converting to
    UTC, because the consumer (SNOW-98 render model) wants the local time for
    sunrise/sunset comparison.

    Naive inputs are assumed to be UTC and tagged accordingly.

    Args:
        value: An ISO-8601 formatted datetime string.

    Returns:
        A tz-aware datetime object (local-time if the input carries an offset,
        UTC if it was naive).

    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _build_snapshot_defaults(
    weather_code: int,
    sunrise_str: str,
    sunset_str: str,
) -> dict[str, Any]:
    """
    Build the ``defaults`` dict for a WeatherSnapshot update_or_create call.

    Args:
        weather_code: WMO weather interpretation code (0–99).
        sunrise_str: ISO-8601 sunrise datetime string from Open-Meteo.
        sunset_str: ISO-8601 sunset datetime string from Open-Meteo.

    Returns:
        A dict suitable for passing as ``defaults=`` to update_or_create.

    """
    return {
        "weather_code": weather_code,
        "sunrise": _parse_dt(sunrise_str),
        "sunset": _parse_dt(sunset_str),
        "fetched_at": django_timezone.now(),
    }


def fetch_weather_for_region(
    region: Region,
    target_date: date,
    *,
    commit: bool,
) -> tuple[WeatherSnapshot, bool] | None:
    """
    Fetch and optionally persist today's weather snapshot for one region.

    Calls the Open-Meteo forecast endpoint, extracts the weather code and
    sunrise/sunset for ``target_date`` (index ``[0]`` of the daily arrays),
    then either persists a WeatherSnapshot via update_or_create or returns
    None if ``commit=False``.

    Args:
        region: The Region to fetch weather for. Must have a non-None ``centre``
            field with shape ``{"lon": float, "lat": float}``.
        target_date: The calendar date to fetch weather for.
        commit: If True, write the snapshot to the database. If False, the
            HTTP request still executes (real API probe) but no rows are
            written and None is returned.

    Returns:
        A ``(WeatherSnapshot, created)`` tuple when ``commit=True``, where
        ``created`` is True for a new row or False for an update. Returns
        None when ``commit=False``.

    Raises:
        requests.HTTPError: If the Open-Meteo API returns a non-2xx status.
        KeyError: If the expected fields are absent from the API response.

    """
    centre: dict[str, float] = cast(dict[str, float], region.centre)
    logger.debug(
        "Fetching forecast weather for region=%s date=%s commit=%s",
        region.region_id,
        target_date,
        commit,
    )

    params: dict[str, str] = {
        "latitude": str(centre["lat"]),
        "longitude": str(centre["lon"]),
        "daily": "weather_code,sunrise,sunset",
        "timezone": "auto",
        # HRB: forecast_days cannot be used with start/end dates.
        # "forecast_days": "1",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
    }
    response = requests.get(FORECAST_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data: dict[str, Any] = response.json()

    daily = data["daily"]
    weather_code: int = daily["weather_code"][0]
    sunrise_str: str = daily["sunrise"][0]
    sunset_str: str = daily["sunset"][0]

    logger.debug(
        "Open-Meteo forecast: region=%s date=%s code=%d sunrise=%s sunset=%s",
        region.region_id,
        target_date,
        weather_code,
        sunrise_str,
        sunset_str,
    )

    if not commit:
        return None

    defaults = _build_snapshot_defaults(weather_code, sunrise_str, sunset_str)
    snapshot, created = WeatherSnapshot.objects.update_or_create(
        region=region,
        valid_for_date=target_date,
        defaults=defaults,
    )
    action = "Created" if created else "Updated"
    logger.debug(
        "%s WeatherSnapshot: region=%s date=%s code=%d",
        action,
        region.region_id,
        target_date,
        weather_code,
    )
    return snapshot, created


def fetch_all_regions(
    target_date: date,
    *,
    commit: bool,
) -> dict[str, int]:
    """
    Fetch weather snapshots for every Region that has a centre coordinate.

    Iterates all Region rows. Regions without a ``centre`` value are skipped
    (counter: ``skipped``). Per-region HTTP failures are caught, logged, and
    counted (counter: ``failed``) — they do not abort the batch.

    Args:
        target_date: The calendar date to fetch weather for.
        commit: If True, write snapshots to the database.

    Returns:
        A dict with integer counters:
          ``created``  — new WeatherSnapshot rows written.
          ``updated``  — existing WeatherSnapshot rows updated.
          ``failed``   — regions where the HTTP call raised an exception.
          ``skipped``  — regions without a centre coordinate.

    """
    counts: dict[str, int] = {
        "created": 0,
        "updated": 0,
        "failed": 0,
        "skipped": 0,
    }

    # Materialise once so we can use len() without a second DB round-trip.
    regions = list(Region.objects.order_by("region_id"))
    logger.info(
        "fetch_all_regions: date=%s regions=%d commit=%s",
        target_date,
        len(regions),
        commit,
    )

    for region in regions:
        if not region.centre:
            logger.debug("Skipping region=%s — no centre coordinate", region.region_id)
            counts["skipped"] += 1
            continue

        try:
            result = fetch_weather_for_region(region, target_date, commit=commit)
            if commit and result is not None:
                _, created = result
                if created:
                    counts["created"] += 1
                else:
                    counts["updated"] += 1
        except Exception:  # noqa: BLE001 — broad catch intentional: per-region failure must not abort the batch
            logger.warning(
                "Failed to fetch weather for region=%s date=%s",
                region.region_id,
                target_date,
                exc_info=True,
            )
            counts["failed"] += 1

    logger.info(
        "fetch_all_regions done: created=%d updated=%d failed=%d skipped=%d",
        counts["created"],
        counts["updated"],
        counts["failed"],
        counts["skipped"],
    )
    return counts


def fetch_archive_for_region(
    region: Region,
    start_date: date,
    end_date: date,
    *,
    commit: bool,
) -> list[tuple[WeatherSnapshot, bool]]:
    """
    Fetch historical weather for a date range for one region.

    Calls the Open-Meteo archive endpoint (which supports multi-day ranges)
    and iterates the ``daily.time`` array, pairing each date with its
    weather code and sunrise/sunset. Persists a WeatherSnapshot per date
    via update_or_create when ``commit=True``.

    Args:
        region: The Region to fetch historical weather for. Must have a
            non-None ``centre`` field.
        start_date: First date in the range (inclusive).
        end_date: Last date in the range (inclusive).
        commit: If True, persist snapshots to the database.

    Returns:
        A list of ``(WeatherSnapshot, created)`` tuples — one per day — when
        ``commit=True``. Returns an empty list when ``commit=False``.

    Raises:
        requests.HTTPError: If the Open-Meteo archive API returns a non-2xx
            status.
        KeyError: If the expected fields are absent from the API response.

    """
    centre: dict[str, float] = cast(dict[str, float], region.centre)
    logger.debug(
        "Fetching archive weather for region=%s start=%s end=%s commit=%s",
        region.region_id,
        start_date,
        end_date,
        commit,
    )

    archive_params: dict[str, str] = {
        "latitude": str(centre["lat"]),
        "longitude": str(centre["lon"]),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": "weather_code,sunrise,sunset",
        "timezone": "auto",
    }
    response = requests.get(ARCHIVE_URL, params=archive_params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data: dict[str, Any] = response.json()

    daily = data["daily"]
    dates: list[str] = daily["time"]
    weather_codes: list[int] = daily["weather_code"]
    sunrises: list[str] = daily["sunrise"]
    sunsets: list[str] = daily["sunset"]

    if not commit:
        logger.debug(
            "Dry run — would create/update %d snapshot(s) for region=%s",
            len(dates),
            region.region_id,
        )
        return []

    snapshots: list[tuple[WeatherSnapshot, bool]] = []
    for date_str, code, sunrise_str, sunset_str in zip(
        dates, weather_codes, sunrises, sunsets
    ):
        day = date.fromisoformat(date_str)
        defaults = _build_snapshot_defaults(code, sunrise_str, sunset_str)
        snapshot, created = WeatherSnapshot.objects.update_or_create(
            region=region,
            valid_for_date=day,
            defaults=defaults,
        )
        action = "Created" if created else "Updated"
        logger.debug(
            "%s WeatherSnapshot: region=%s date=%s code=%d",
            action,
            region.region_id,
            day,
            code,
        )
        snapshots.append((snapshot, created))

    return snapshots


def backfill_all_regions(
    start_date: date,
    end_date: date,
    *,
    commit: bool,
    delay: float = 0.0,
) -> dict[str, int]:
    """
    Backfill historical weather snapshots for every Region with a centre.

    Iterates all Region rows. Regions without a ``centre`` value are logged
    and counted as ``skipped``. Per-region archive failures are caught,
    logged, and counted as ``failed`` — they do not abort the batch.

    Args:
        start_date: First date in the backfill range (inclusive).
        end_date: Last date in the backfill range (inclusive).
        commit: If True, write snapshots to the database.
        delay: Seconds to sleep between successive per-region archive
            calls. ``0.0`` (default) is a no-op; positive values pace the
            API to stay inside Open-Meteo's free-tier rate limit. The
            sleep happens between regions only — never before the first
            or after the last.

    Returns:
        A dict with integer counters:
          ``created``  — new WeatherSnapshot rows written.
          ``updated``  — existing WeatherSnapshot rows updated.
          ``failed``   — regions where the HTTP call raised an exception.
          ``skipped``  — regions without a centre coordinate.

    """
    counts: dict[str, int] = {
        "created": 0,
        "updated": 0,
        "failed": 0,
        "skipped": 0,
    }

    # Materialise once so we can use len() without a second DB round-trip.
    regions = list(Region.objects.order_by("region_id"))
    logger.info(
        "backfill_all_regions: start=%s end=%s regions=%d commit=%s delay=%s",
        start_date,
        end_date,
        len(regions),
        commit,
        delay,
    )

    for idx, region in enumerate(regions):
        if not region.centre:
            logger.debug("Skipping region=%s — no centre coordinate", region.region_id)
            counts["skipped"] += 1
            continue

        try:
            results = fetch_archive_for_region(
                region, start_date, end_date, commit=commit
            )

            if commit:
                for _snapshot, created in results:
                    if created:
                        counts["created"] += 1
                    else:
                        counts["updated"] += 1

        except Exception:  # noqa: BLE001 — broad catch intentional: per-region failure must not abort the batch
            logger.warning(
                "Failed to backfill weather for region=%s start=%s end=%s",
                region.region_id,
                start_date,
                end_date,
                exc_info=True,
            )
            counts["failed"] += 1

        # Pace the API: sleep between regions, but not after the last one.
        if delay > 0 and idx < len(regions) - 1:
            time.sleep(delay)

    logger.info(
        "backfill_all_regions done: created=%d updated=%d failed=%d skipped=%d",
        counts["created"],
        counts["updated"],
        counts["failed"],
        counts["skipped"],
    )
    return counts
