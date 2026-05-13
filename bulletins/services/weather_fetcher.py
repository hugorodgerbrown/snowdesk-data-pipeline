"""
bulletins/services/weather_fetcher.py — Fetching and persisting Open-Meteo weather data.

Contains five fetch functions, a source resolver, and a background-thread
dispatcher used by the bulletin page render:

  resolve_weather_source(source)
      Map a ``--source`` choice string (``"live"`` or ``"local-mirror"``) to a
      ``base_url`` suitable for passing to the fetch functions. Returns ``None``
      for the live source (falls back to the module-level URL constants). Raises
      ``CommandError`` for ``"local-mirror"`` when
      ``settings.WEATHER_API_LOCAL_MIRROR_BASE_URL`` is not configured. Imported
      by both ``fetch_weather`` and ``backfill_weather`` commands to avoid
      duplicating the resolver logic.

  fetch_weather_for_region(region, target_date, *, commit, base_url, on_fetched)
      Fetches today's (or any single day's) weather for one region from the
      Open-Meteo forecast endpoint. Returns ``(WeatherSnapshot, created)`` when
      ``commit=True``, or ``None`` when ``commit=False``. ``base_url`` overrides
      the module-level ``FORECAST_URL``; ``on_fetched`` is called once per
      fetched record (for ``--stash`` capture).

  fetch_all_regions(target_date, *, commit, base_url, on_fetched)
      Calls fetch_weather_for_region for every MicroRegion that has a centre
      coordinate; returns summary counters {created, updated, failed, skipped}.

  fetch_archive_for_region(region, start_date, end_date, *, commit, base_url,
  on_fetched)
      Fetches historical weather for a date range from the Open-Meteo archive
      endpoint. Returns a list of ``(WeatherSnapshot, created)`` tuples when
      ``commit=True``, or an empty list when ``commit=False``. ``base_url``
      overrides the module-level ``ARCHIVE_URL``; ``on_fetched`` is called once
      per fetched record.

  backfill_all_regions(start_date, end_date, *, commit, delay, base_url, on_fetched)
      Calls fetch_archive_for_region for every MicroRegion that has a centre
      coordinate; returns summary counters {created, updated, failed, skipped}.

  fetch_weather_async(region, target_date)
      Schedules an idempotent inline fetch on a background daemon thread so
      ``bulletin_detail`` can return immediately on prefetched past-date page
      renders. Routes to the archive or forecast fetcher based on whether
      ``target_date`` is in the past. Mirrors the
      ``subscriptions.services.email._dispatch_async`` pattern: settings toggle
      ``WEATHER_FETCH_ASYNC`` flips the work synchronous for tests, exceptions
      are swallowed at WARNING, and the per-thread DB connection is closed in
      ``finally`` (skipped on the main thread to keep sync-mode tests' own
      transaction connection alive). See ``docs/async-operations.md``.

Uses ``requests`` with a 30-second timeout (matching data_fetcher.py's pattern).
Per-region HTTP failures bubble up from the single-region functions; the wrapper
functions catch them, log a warning, and continue so that one bad region does
not abort the entire batch.

When ``commit=False``, the HTTP requests still execute (real API probe) but no
rows are written.

``base_url`` defaults to ``None`` in every function; when ``None``, the function
falls back to the module-level URL constants so existing callers keep working
without change.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, cast

import requests
from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone as django_timezone

from bulletins.models import WeatherSnapshot
from regions.models import Centre, MicroRegion

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT = 30  # seconds

SOURCE_LIVE = "live"
SOURCE_LOCAL_MIRROR = "local-mirror"


def resolve_weather_source(source: str) -> str | None:
    """
    Map a ``--source`` choice to a base URL (or ``None`` for live).

    Returning ``None`` for the live source lets callers fall back to the
    module-level ``FORECAST_URL`` / ``ARCHIVE_URL`` constants, keeping the
    live path identical to its pre-flag behaviour.

    Imported by both ``fetch_weather`` and ``backfill_weather`` commands so
    the resolver logic is not duplicated.

    Args:
        source: One of ``SOURCE_LIVE`` or ``SOURCE_LOCAL_MIRROR``.

    Returns:
        ``None`` for the live source, or the configured mirror base URL.

    Raises:
        CommandError: ``--source local-mirror`` was requested but
            ``settings.WEATHER_API_LOCAL_MIRROR_BASE_URL`` is not configured
            (i.e. running outside ``development.py``).

    """
    if source == SOURCE_LIVE:
        return None
    mirror_url: str | None = getattr(
        settings, "WEATHER_API_LOCAL_MIRROR_BASE_URL", None
    )
    if not mirror_url:
        raise CommandError(
            "--source local-mirror requires settings.WEATHER_API_LOCAL_MIRROR_BASE_URL "
            "to be configured. The mirror is only available in development.py."
        )
    return mirror_url


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
    region: MicroRegion,
    target_date: date,
    *,
    commit: bool,
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[WeatherSnapshot, bool] | None:
    """
    Fetch and optionally persist today's weather snapshot for one region.

    Calls the Open-Meteo forecast endpoint (or a mirror when ``base_url``
    is set), extracts the weather code and sunrise/sunset for ``target_date``
    (index ``[0]`` of the daily arrays), then either persists a WeatherSnapshot
    via update_or_create or returns None if ``commit=False``.

    Args:
        region: The MicroRegion to fetch weather for. Must have a non-None ``centre``
            field with shape ``{"lon": float, "lat": float}``.
        target_date: The calendar date to fetch weather for.
        commit: If True, write the snapshot to the database. If False, the
            HTTP request still executes (real API probe) but no rows are
            written and None is returned.
        base_url: When set, overrides ``FORECAST_URL`` as the endpoint base.
            The actual request goes to ``f"{base_url}/forecast"``. Defaults to
            ``None``, which falls back to the module-level ``FORECAST_URL``.
        on_fetched: Optional callback called once after the response is parsed,
            with a NDJSON-shape dict ``{region_id, date, weather_code, sunrise,
            sunset, captured_at}``. Used by ``--stash`` to collect records for
            the on-disk archive. Defaults to ``None`` (no-op).

    Returns:
        A ``(WeatherSnapshot, created)`` tuple when ``commit=True``, where
        ``created`` is True for a new row or False for an update. Returns
        None when ``commit=False``.

    Raises:
        requests.HTTPError: If the Open-Meteo API returns a non-2xx status.
        KeyError: If the expected fields are absent from the API response.

    """
    centre: Centre = cast(Centre, region.centre)
    url = f"{base_url}/forecast" if base_url else FORECAST_URL
    logger.debug(
        "Fetching forecast weather for region=%s date=%s commit=%s url=%s",
        region.region_id,
        target_date,
        commit,
        url,
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
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
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

    if on_fetched is not None:
        on_fetched(
            {
                "region_id": region.region_id,
                "date": target_date.isoformat(),
                "weather_code": weather_code,
                "sunrise": sunrise_str,
                "sunset": sunset_str,
                "captured_at": django_timezone.now().isoformat(),
            }
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
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    """
    Fetch weather snapshots for every MicroRegion that has a centre coordinate.

    Iterates all MicroRegion rows. Regions without a ``centre`` value are skipped
    (counter: ``skipped``). Per-region HTTP failures are caught, logged, and
    counted (counter: ``failed``) — they do not abort the batch.

    Args:
        target_date: The calendar date to fetch weather for.
        commit: If True, write snapshots to the database.
        base_url: When set, overrides ``FORECAST_URL`` for all per-region
            calls. Defaults to ``None`` (fall back to ``FORECAST_URL``).
        on_fetched: Optional callback forwarded to each per-region call.
            Called once per fetched ``(region, date)`` record. Defaults to
            ``None`` (no-op).

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
    regions = list(MicroRegion.objects.order_by("region_id"))
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
            result = fetch_weather_for_region(
                region,
                target_date,
                commit=commit,
                base_url=base_url,
                on_fetched=on_fetched,
            )
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
    region: MicroRegion,
    start_date: date,
    end_date: date,
    *,
    commit: bool,
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
) -> list[tuple[WeatherSnapshot, bool]]:
    """
    Fetch historical weather for a date range for one region.

    Calls the Open-Meteo archive endpoint (or a mirror when ``base_url`` is
    set), iterates the ``daily.time`` array, pairing each date with its
    weather code and sunrise/sunset. Persists a WeatherSnapshot per date via
    update_or_create when ``commit=True``.

    Args:
        region: The MicroRegion to fetch historical weather for. Must have a
            non-None ``centre`` field.
        start_date: First date in the range (inclusive).
        end_date: Last date in the range (inclusive).
        commit: If True, persist snapshots to the database.
        base_url: When set, overrides ``ARCHIVE_URL`` as the endpoint base.
            The actual request goes to ``f"{base_url}/archive"``. Defaults to
            ``None``, which falls back to the module-level ``ARCHIVE_URL``.
        on_fetched: Optional callback called once per ``(region, date)`` record
            in the response, with a NDJSON-shape dict ``{region_id, date,
            weather_code, sunrise, sunset, captured_at}``. Used by ``--stash``.
            Defaults to ``None`` (no-op).

    Returns:
        A list of ``(WeatherSnapshot, created)`` tuples — one per day — when
        ``commit=True``. Returns an empty list when ``commit=False``.

    Raises:
        requests.HTTPError: If the Open-Meteo archive API returns a non-2xx
            status.
        KeyError: If the expected fields are absent from the API response.

    """
    centre: Centre = cast(Centre, region.centre)
    url = f"{base_url}/archive" if base_url else ARCHIVE_URL
    logger.debug(
        "Fetching archive weather for region=%s start=%s end=%s commit=%s url=%s",
        region.region_id,
        start_date,
        end_date,
        commit,
        url,
    )

    archive_params: dict[str, str] = {
        "latitude": str(centre["lat"]),
        "longitude": str(centre["lon"]),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": "weather_code,sunrise,sunset",
        "timezone": "auto",
    }
    response = requests.get(url, params=archive_params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data: dict[str, Any] = response.json()

    daily = data["daily"]
    dates: list[str] = daily["time"]
    weather_codes: list[int] = daily["weather_code"]
    sunrises: list[str] = daily["sunrise"]
    sunsets: list[str] = daily["sunset"]

    captured_at = django_timezone.now().isoformat()

    if on_fetched is not None:
        for date_str, code, sunrise_str, sunset_str in zip(
            dates, weather_codes, sunrises, sunsets
        ):
            on_fetched(
                {
                    "region_id": region.region_id,
                    "date": date_str,
                    "weather_code": code,
                    "sunrise": sunrise_str,
                    "sunset": sunset_str,
                    "captured_at": captured_at,
                }
            )

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
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    """
    Backfill historical weather snapshots for every MicroRegion with a centre.

    Iterates all MicroRegion rows. Regions without a ``centre`` value are logged
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
        base_url: When set, overrides ``ARCHIVE_URL`` for all per-region
            calls. Defaults to ``None`` (fall back to ``ARCHIVE_URL``).
        on_fetched: Optional callback forwarded to each per-region call.
            Called once per ``(region, date)`` record. Defaults to ``None``
            (no-op).

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
    regions = list(MicroRegion.objects.order_by("region_id"))
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
                region,
                start_date,
                end_date,
                commit=commit,
                base_url=base_url,
                on_fetched=on_fetched,
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


def fetch_weather_async(region: MicroRegion, target_date: date) -> None:
    """
    Schedule an inline weather fetch on a background daemon thread.

    Used by ``bulletin_detail`` on past-date renders when no snapshot exists:
    the page returns immediately; the worker thread checks the DB (idempotent
    guard against thundering herd), then calls the archive or forecast
    fetcher and persists the snapshot. By the time the user clicks the
    prefetched link the snapshot is almost always in the DB and the fresh
    render bakes weather inline — no HTMX swap, no flash.

    Runs synchronously when ``settings.WEATHER_FETCH_ASYNC`` is ``False``
    (tests pin this in tests/conftest.py) so the fetch outcome is
    deterministic in tests.

    Failures inside the worker are caught and logged at WARNING; they never
    propagate to the caller (the response has already been sent). The
    ``finally`` clause closes the per-thread DB connection so the connection
    pool does not leak entries on long-running gunicorn workers. The close
    is skipped on the main thread (e.g. sync-mode tests) to avoid closing
    the test's transaction connection mid-test.

    Args:
        region: MicroRegion the bulletin page is for.
        target_date: Calendar day the page represents.

    """

    def _worker() -> None:
        try:
            # Re-check DB inside the worker — another request may have
            # scheduled (and completed) the same fetch in the meantime.
            if (
                WeatherSnapshot.objects.for_date(target_date)
                .filter(region=region)
                .exists()
            ):
                return
            today = django_timezone.localdate()
            if target_date < today:
                fetch_archive_for_region(region, target_date, target_date, commit=True)
            else:
                fetch_weather_for_region(region, target_date, commit=True)
        except Exception:  # noqa: BLE001 — broad catch intentional: async failure must not surface to caller
            logger.warning(
                "fetch_weather_async failed: region=%s date=%s",
                region.region_id,
                target_date,
                exc_info=True,
            )
        finally:
            # Each background thread opens its own DB connection lazily;
            # close it before exit so the pool does not accumulate idle
            # connections under sustained traffic. Skip on the main thread
            # (sync mode in tests) to avoid closing the test's transaction
            # connection mid-test.
            if threading.current_thread() is not threading.main_thread():
                from django.db import connections

                connections.close_all()

    if not getattr(settings, "WEATHER_FETCH_ASYNC", True):
        _worker()
        return

    threading.Thread(
        target=_worker,
        daemon=True,
        name=f"weather-{region.region_id}-{target_date.isoformat()}",
    ).start()
