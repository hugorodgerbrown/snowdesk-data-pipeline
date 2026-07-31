"""
apps/bulletins/services/weather_fetcher.py — Fetch/persist Open-Meteo weather data.

Contains seven fetch functions, a source resolver, and a background-thread
dispatcher used by the bulletin page render:

  resolve_weather_source(source)
      Map a ``--source`` choice string (``"live"`` or ``"local-mirror"``) to a
      ``base_url`` suitable for passing to the fetch functions. Returns ``None``
      for the live source (falls back to the configured host). Raises
      ``CommandError`` for ``"local-mirror"`` when
      ``settings.WEATHER_API_LOCAL_MIRROR_BASE_URL`` is not configured. Imported
      by the ``fetch_weather`` command to resolve the upstream URL.

  fetch_weather_for_region(region, target_date, *, commit, base_url, on_fetched)
      Fetches today's (or any single day's) weather for one region from the
      Open-Meteo forecast endpoint. Returns ``(WeatherSnapshot, created)`` when
      ``commit=True``, or ``None`` when ``commit=False``. ``base_url`` overrides
      the configured forecast host; ``on_fetched`` is called once per
      fetched record (for ``--stash`` capture).

  fetch_all_regions(target_date, *, commit, base_url, on_fetched)
      Calls fetch_weather_for_region for every MicroRegion that has a centre
      coordinate; returns summary counters {created, updated, failed, skipped}.

  fetch_archive_for_region(region, start_date, end_date, *, commit, base_url,
  on_fetched)
      Fetches historical weather for a date range from the Open-Meteo archive
      endpoint. Returns a list of ``(WeatherSnapshot, created)`` tuples when
      ``commit=True``, or an empty list when ``commit=False``. ``base_url``
      overrides the configured archive host; ``on_fetched`` is called once
      per fetched record.

  backfill_all_regions(start_date, end_date, *, commit, delay, base_url, on_fetched)
      Calls fetch_archive_for_region for every MicroRegion that has a centre
      coordinate; returns summary counters {created, updated, failed, skipped}.

  fetch_weather_async(region, target_date)
      Schedules an idempotent inline fetch on a background daemon thread so
      ``bulletin_detail`` can return immediately on prefetched past-date page
      renders. Routes to the archive or forecast fetcher based on whether
      ``target_date`` is in the past. Uses a daemon-thread dispatch pattern:
      settings toggle ``WEATHER_FETCH_ASYNC`` flips the work synchronous for
      tests, exceptions are swallowed at WARNING, and the per-thread DB
      connection is closed in ``finally`` (skipped on the main thread to keep
      sync-mode tests' own transaction connection alive). See
      ``docs/async-operations.md``.

  fetch_weather_for_point(point, target_date, *, commit, base_url, on_fetched)
      Fetches a POINT_FORECAST_DAYS-day (7-day) window of comprehensive daily
      forecast data, plus a POINT_HOURLY_DAYS-day (2-day) near-term hourly
      series of ski-relevant variables, for one ForecastPoint from the
      Open-Meteo forecast endpoint, passing the point's ``elevation``
      explicitly so the forecast is statistically downscaled to the pin's
      altitude. Persists one ``ForecastPointWeather`` row per day. Returns a
      list of ``(ForecastPointWeather, created)`` tuples — one per day —
      when ``commit=True``, or an empty list when ``commit=False``. Points
      are forecast-only — there is no archive/backfill equivalent (SNOW-416,
      SNOW-417).

  fetch_all_points(target_date, *, commit, base_url, on_fetched)
      Calls fetch_weather_for_point for every ``ForecastPoint.objects.active()``
      row (points referenced by at least one Favourite); returns summary
      counters {created, updated, failed, skipped}.

Uses ``requests`` with a 30-second timeout (matching data_fetcher.py's pattern).
Per-region HTTP failures bubble up from the single-region functions; the wrapper
functions catch them, log a warning, and continue so that one bad region does
not abort the entire batch.

When ``commit=False``, the HTTP requests still execute (real API probe) but no
rows are written.

``base_url`` defaults to ``None`` in every function; when ``None``, the function
falls back to the configured Open-Meteo host (``OPEN_METEO_API_BASE_URL`` /
``OPEN_METEO_ARCHIVE_BASE_URL``) resolved by
``apps.bulletins.services.open_meteo``, so existing callers keep working
without change.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import requests
from django.conf import settings
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone as django_timezone

from apps.bulletins.models import (
    ForecastPoint,
    ForecastPointWeather,
    ForecastPointWeatherHistory,
    WeatherSnapshot,
)
from apps.bulletins.services import open_meteo
from apps.regions.models import Centre, MicroRegion

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds

SOURCE_LIVE = "live"
SOURCE_LOCAL_MIRROR = "local-mirror"

# Comprehensive daily variable set requested for ForecastPoint forecasts — a
# favourited point is rendered as a personal detail card, richer than the
# region bulletin header (which only needs weather_code/sunrise/sunset).
POINT_DAILY_VARIABLES = (
    "weather_code,sunrise,sunset,"
    "temperature_2m_max,temperature_2m_min,"
    "apparent_temperature_max,apparent_temperature_min,"
    "precipitation_sum,snowfall_sum,"
    "precipitation_probability_max,precipitation_hours,"
    "wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,"
    "uv_index_max,daylight_duration,sunshine_duration"
)

# How many days of daily forecast to fetch per point (SNOW-417) — a
# favourited pin gets a week-ahead outlook, not just today.
POINT_FORECAST_DAYS = 7

# How many of those days also carry an hourly series — bounded to keep the
# per-row JSON payload small; the near-term days are the ones a skier
# actually plans an hour-by-hour day around.
POINT_HOURLY_DAYS = 2

# Ski-relevant hourly variables for the near-term hourly series. Open-Meteo
# has no daily freezing-level aggregate, so freezing_level_height is only
# available hourly — the daily column on ForecastPointWeather is derived
# from this block (see _daily_max_freezing_level below).
POINT_HOURLY_VARIABLES = (
    "temperature_2m,snowfall,precipitation,"
    "wind_speed_10m,wind_gusts_10m,freezing_level_height"
)

# SNOW-443: MeteoSwiss ICON-CH model selection for Alpine points.
#
# Open-Meteo's default chain is a blend picked for global coverage; over the
# Alps its cells are coarse enough to smear exactly the detail a point
# forecast exists to provide — precipitation, snowfall and wind at a specific
# pin. MeteoSwiss's ICON-CH runs at 1–2 km over the same ground.
#
# The exact identifier matters: the `icon_ch1` / `icon_ch2` names read
# naturally but return a 400 ("Cannot initialize MultiDomains"). The working
# values carry the `meteoswiss_` prefix. ch2 (~5-day range at 2 km) is chosen
# over ch1 (~33-45h, 1 km) because daily and hourly share one HTTP call:
# ch1 would cover the 2-day hourly block better but leave most of the 7-day
# daily window empty, and splitting into two model-specific calls per point
# doubles the request count for a marginal gain.
ICON_CH_MODEL = "meteoswiss_icon_ch2"

# Bounding box for the ICON-CH domain, as (min_lat, max_lat, min_lon, max_lon).
# Deliberately an over-generous superset rather than a precise polygon — the
# domain is noticeably wider than the Swiss border, and Snowdesk serves the
# wider Alpine arc via ALBINA (AT-07, IT-32-BZ/TN) and Météo-France too. A
# point that is inside the box but outside the real domain costs one wasted
# request and falls back; a box drawn tight to Switzerland would silently
# deny Chamonix and the Dolomites the better model.
# See docs/decisions/icon-ch-domain-bounding-box.md.
ICON_CH_BOUNDS = (44.5, 48.5, 5.0, 11.5)

# The hourly variables persisted onto each hourly_series row, in the order
# they appear on each dict.
_HOURLY_SERIES_FIELDS = (
    "temperature_2m",
    "snowfall",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "freezing_level_height",
)


def resolve_weather_source(source: str) -> str | None:
    """
    Map a ``--source`` choice to a base URL (or ``None`` for live).

    Returning ``None`` for the live source lets callers fall back to the
    configured Open-Meteo host, keeping the live path identical to its
    pre-flag behaviour.

    Imported by the ``fetch_weather`` command to resolve the upstream URL.

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


def _parse_dt_preserve_offset(value: str) -> datetime:
    """
    Parse an ISO-8601 datetime string, preserving the original timezone offset.

    Open-Meteo returns sunrise/sunset as ISO-8601 strings with a UTC offset
    when ``timezone=auto`` is specified — e.g. ``"2026-05-01T05:32+02:00"``.
    We deliberately do NOT convert to UTC; the consumer (SNOW-98 render model)
    wants local time for sunrise/sunset comparison.

    This differs from ``slf_fetcher._parse_dt`` which always normalises to UTC.
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
        "sunrise": _parse_dt_preserve_offset(sunrise_str),
        "sunset": _parse_dt_preserve_offset(sunset_str),
        "fetched_at": django_timezone.now(),
    }


def _build_point_defaults(daily: dict[str, Any], idx: int) -> dict[str, Any]:
    """
    Build the ``defaults`` dict for a ForecastPointWeather update_or_create call.

    Only ``weather_code``, ``sunrise``, and ``sunset`` are treated as
    required (a KeyError there is a genuine API-shape problem). Every
    extended daily variable is read via ``.get(key, [None])[idx]`` so an
    omitted array (Open-Meteo drops some variables depending on the backing
    weather model) degrades to ``None`` rather than raising.

    Args:
        daily: The ``"daily"`` block of an Open-Meteo forecast response.
        idx: Index into each daily array for the target date (``0`` for the
            first day of a multi-day forecast request).

    Returns:
        A dict suitable for passing as ``defaults=`` to update_or_create.

    """

    def _extended(key: str) -> Any:
        """Return the extended variable at idx, or None if omitted/short."""
        values = daily.get(key)
        if values is None or idx >= len(values):
            return None
        return values[idx]

    return {
        "weather_code": daily["weather_code"][idx],
        "sunrise": _parse_dt_preserve_offset(daily["sunrise"][idx]),
        "sunset": _parse_dt_preserve_offset(daily["sunset"][idx]),
        "temperature_2m_max": _extended("temperature_2m_max"),
        "temperature_2m_min": _extended("temperature_2m_min"),
        "apparent_temperature_max": _extended("apparent_temperature_max"),
        "apparent_temperature_min": _extended("apparent_temperature_min"),
        "precipitation_sum": _extended("precipitation_sum"),
        "snowfall_sum": _extended("snowfall_sum"),
        "precipitation_probability_max": _extended("precipitation_probability_max"),
        "precipitation_hours": _extended("precipitation_hours"),
        "wind_speed_10m_max": _extended("wind_speed_10m_max"),
        "wind_gusts_10m_max": _extended("wind_gusts_10m_max"),
        "wind_direction_10m_dominant": _extended("wind_direction_10m_dominant"),
        "uv_index_max": _extended("uv_index_max"),
        "daylight_duration": _extended("daylight_duration"),
        "sunshine_duration": _extended("sunshine_duration"),
        "fetched_at": django_timezone.now(),
    }


def _build_history_defaults(point_defaults: dict[str, Any]) -> dict[str, Any]:
    """
    Project a ForecastPointWeather defaults dict onto its history counterpart.

    ``ForecastPointWeatherHistory`` retains a deliberate subset of the
    daily payload: the scalars whose movement between issue dates is the
    convergence signal. ``hourly_series`` is dropped (it exists for only
    the first ``POINT_HOURLY_DAYS`` days of a window, so it cannot form a
    series across lead times) as are ``sunrise``/``sunset`` (astronomical,
    identical on every run for a given day). See SNOW-575.

    Reads from the dict ``_build_point_defaults`` already produced rather
    than re-reading the response, so the history row cannot disagree with
    the row it accompanies.

    Args:
        point_defaults: The defaults dict built for the accompanying
            ForecastPointWeather upsert, after the caller has layered on
            ``freezing_level_height``.

    Returns:
        A dict suitable for passing as ``defaults=`` to update_or_create
        on ForecastPointWeatherHistory.

    """
    return {
        key: point_defaults.get(key)
        for key in (
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "snowfall_sum",
            "wind_speed_10m_max",
            "freezing_level_height",
            "fetched_at",
        )
    }


def _hourly_rows_for_day(
    hourly: dict[str, Any] | None, day: date
) -> list[dict[str, Any]]:
    """
    Return the hourly rows for a single calendar day from an hourly block.

    Args:
        hourly: The ``"hourly"`` block of an Open-Meteo forecast response
            (a dict of parallel arrays keyed by variable name, plus
            ``"time"``), or ``None`` if the response carried no hourly data.
        day: The calendar date to filter hourly rows to.

    Returns:
        A list of dicts, one per matching hour, each with keys ``time`` plus
        every name in ``_HOURLY_SERIES_FIELDS`` (``None`` for an omitted
        variable at that hour). Empty list if ``hourly`` is ``None`` or no
        hour falls on ``day``.

    """
    if not hourly:
        return []
    times: list[str] = hourly.get("time", [])
    rows: list[dict[str, Any]] = []
    for idx, time_str in enumerate(times):
        if _parse_dt_preserve_offset(time_str).date() != day:
            continue
        row: dict[str, Any] = {"time": time_str}
        for field in _HOURLY_SERIES_FIELDS:
            values = hourly.get(field)
            row[field] = (
                values[idx] if values is not None and idx < len(values) else None
            )
        rows.append(row)
    return rows


def _daily_max_freezing_level(hourly: dict[str, Any] | None, day: date) -> float | None:
    """
    Derive a daily-max freezing level height from an hourly block.

    Open-Meteo has no daily freezing-level aggregate, so this rolls up the
    hourly ``freezing_level_height`` values for the day into a single
    representative figure.

    Args:
        hourly: The ``"hourly"`` block of an Open-Meteo forecast response,
            or ``None`` if the response carried no hourly data.
        day: The calendar date to derive the maximum for.

    Returns:
        The maximum hourly freezing level height (metres) for ``day``, or
        ``None`` if no hourly freezing-level values are available for it.

    """
    values = [
        row["freezing_level_height"]
        for row in _hourly_rows_for_day(hourly, day)
        if row["freezing_level_height"] is not None
    ]
    return max(values) if values else None


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
        base_url: When set, overrides ``OPEN_METEO_API_BASE_URL`` as the
            endpoint base. The actual request goes to
            ``f"{base_url}/forecast"``. Defaults to ``None``, which falls
            back to the configured host and sends the ``apikey`` parameter.
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
    url = open_meteo.request_url(open_meteo.FORECAST, base_url)
    logger.debug(
        "Fetching forecast weather for region=%s date=%s commit=%s url=%s",
        region.region_id,
        target_date,
        commit,
        url,
    )

    params: dict[str, str] = open_meteo.with_api_key(
        {
            "latitude": str(centre["lat"]),
            "longitude": str(centre["lon"]),
            "daily": "weather_code,sunrise,sunset",
            "timezone": "auto",
            # HRB: forecast_days cannot be used with start/end dates.
            # "forecast_days": "1",
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
        },
        base_url,
    )
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
        base_url: When set, overrides the configured host for all
            per-region calls. Defaults to ``None``.
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
            logger.exception(
                "Failed to fetch weather for region=%s date=%s",
                region.region_id,
                target_date,
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


def _point_daily_dates(daily: dict[str, Any], point_pk: int) -> list[date]:
    """Parse and validate the ``daily.time`` array for a point forecast.

    Returns the provider-supplied forecast dates as ``date`` objects. The
    caller must key each stored row off these dates rather than inventing
    consecutive dates from ``target_date`` — a shifted, gapped, or reordered
    Open-Meteo response would otherwise attach weather to the wrong calendar
    day (SNOW-466).

    Args:
        daily: The ``"daily"`` block of an Open-Meteo forecast response.
        point_pk: The ForecastPoint pk, for error messages only.

    Returns:
        The parsed provider dates, in response order.

    Raises:
        ValueError: If ``time`` is empty, or a required per-day array
            (``weather_code`` / ``sunrise`` / ``sunset``) does not align 1:1
            with ``time`` — a misaligned batch must not be stored.
        KeyError: If a required array is absent entirely (existing contract).

    """
    dates = [date.fromisoformat(d) for d in daily["time"]]
    if not dates:
        raise ValueError(
            f"Open-Meteo point forecast for point={point_pk}: empty 'time' array."
        )
    for name in ("weather_code", "sunrise", "sunset"):
        arr = daily[name]
        if len(arr) != len(dates):
            raise ValueError(
                f"Open-Meteo point forecast for point={point_pk}: '{name}' has "
                f"{len(arr)} entries but 'time' has {len(dates)} — refusing to "
                f"store misaligned data."
            )
    return dates


def _is_alpine_point(latitude: float, longitude: float) -> bool:
    """
    Return True when a point falls inside the ICON-CH bounding box.

    The gate has to be applied *before* the request, because ICON-CH
    returns a 400 for a location outside its domain rather than quietly
    degrading to something usable. It is a bounding box, not a polygon:
    combined with the day-0 fallback below, a wrong-side miss costs one
    wasted request and never a bad write.

    Args:
        latitude: Point latitude in decimal degrees.
        longitude: Point longitude in decimal degrees.

    Returns:
        True when the point is inside ``ICON_CH_BOUNDS``.

    """
    min_lat, max_lat, min_lon, max_lon = ICON_CH_BOUNDS
    return min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon


def _day_zero_is_degraded(data: dict[str, Any]) -> bool:
    """
    Return True when day 0 of a forecast payload is missing required fields.

    ``_build_point_defaults`` treats ``weather_code``, ``sunrise`` and
    ``sunset`` as required — it indexes them directly rather than using
    ``.get()``. ``ForecastPointWeather.weather_code`` is a
    ``PositiveSmallIntegerField`` with no ``null=True``, so a ``None``
    from a partial ICON-CH payload raises at ``update_or_create`` rather
    than skipping the row. This check is what keeps that from happening:
    a dead day 0 means fall back to the default chain and persist that
    instead.

    Only day 0 is examined. Later days going ``None`` beyond the model's
    horizon (ICON-CH2 runs ~5 days into a 7-day window) is expected and
    already handled by the degrade-to-``None`` pattern in
    ``_build_point_defaults``; falling back for that would throw away four
    good high-resolution days to rescue two the default chain barely
    resolves either.

    Args:
        data: The parsed Open-Meteo JSON response.

    Returns:
        True when any required day-0 field is absent or null.

    """
    daily = data.get("daily") or {}
    for key in ("weather_code", "sunrise", "sunset"):
        values = daily.get(key)
        if not values or values[0] is None:
            return True
    return False


def _get_point_forecast(url: str, params: dict[str, str]) -> dict[str, Any]:
    """Issue one Open-Meteo forecast request and return the parsed payload."""
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def _fetch_point_payload(
    url: str,
    params: dict[str, str],
    point: ForecastPoint,
) -> dict[str, Any]:
    """
    Fetch one point's forecast, preferring ICON-CH where it has skill.

    Points inside ``ICON_CH_BOUNDS`` request ``ICON_CH_MODEL``; points
    outside send no ``models=`` at all and take Open-Meteo's default
    blended chain. An in-domain request that comes back as a 400, or with
    a dead day 0, falls back to the default chain exactly once
    (SNOW-443).

    The fallback lives here rather than in ``fetch_all_points`` so that a
    single point is never counted as failed purely because ICON-CH had no
    near-term skill for it. Only a 400 qualifies: any other status is a
    real failure — an outage, a rate limit — and propagates, so the batch
    counter still sees it.

    Args:
        url: The forecast endpoint.
        params: The request parameters, without ``models=``.
        point: The point being fetched; used for the domain gate and for
            log context.

    Returns:
        The parsed Open-Meteo JSON payload.

    Raises:
        requests.HTTPError: On any non-2xx status other than an
            in-domain 400.

    """
    use_icon_ch = _is_alpine_point(point.latitude, point.longitude)
    if not use_icon_ch:
        return _get_point_forecast(url, params)

    try:
        data = _get_point_forecast(url, {**params, "models": ICON_CH_MODEL})
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status != 400:
            raise
        logger.warning(
            "ICON-CH rejected point=%s (lat=%s lon=%s) with 400 — "
            "retrying on the default model chain",
            point.pk,
            point.latitude,
            point.longitude,
        )
    else:
        if not _day_zero_is_degraded(data):
            return data
        logger.warning(
            "ICON-CH returned no usable day-0 data for point=%s "
            "(lat=%s lon=%s) — retrying on the default model chain",
            point.pk,
            point.latitude,
            point.longitude,
        )

    return _get_point_forecast(url, params)


def fetch_weather_for_point(
    point: ForecastPoint,
    target_date: date,
    *,
    commit: bool,
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
) -> list[tuple[ForecastPointWeather, bool]]:
    """
    Fetch and optionally persist a 7-day comprehensive forecast for one ForecastPoint.

    Calls the Open-Meteo forecast endpoint (or a mirror when ``base_url``
    is set) once, passing the point's ``elevation`` explicitly so the
    forecast is statistically downscaled to the pin's altitude rather than
    the model cell's mean terrain. Requests ``POINT_FORECAST_DAYS`` days of
    the ``POINT_DAILY_VARIABLES`` daily set plus an hourly block
    (``POINT_HOURLY_VARIABLES``) spanning the same window, then persists one
    ForecastPointWeather row per day via update_or_create — ``idx=0`` is
    ``target_date``, ``idx=6`` is ``target_date + 6 days``.

    Each row's ``freezing_level_height`` is derived as the daily maximum of
    that day's hourly values (Open-Meteo has no daily freezing-level
    aggregate). Each row's ``hourly_series`` is populated with that day's raw
    hourly rows only for the first ``POINT_HOURLY_DAYS`` days; beyond that it
    is ``None``, keeping the JSON payload bounded.

    Points inside ``ICON_CH_BOUNDS`` request MeteoSwiss's ICON-CH2 run
    (``models=meteoswiss_icon_ch2``) instead of Open-Meteo's default
    blended chain — 2 km cells over the Alps rather than the blend's
    coarser ones, which is the whole accuracy argument for a per-point
    forecast. Points outside the box omit ``models=`` and keep the
    default chain (SNOW-443).

    The ICON-CH request falls back to the default chain, once, when the
    response is a 400 or when day 0 carries no usable data — see
    ``_day_zero_is_degraded``. The retry happens here rather than in
    ``fetch_all_points`` so a single point is never counted as failed
    purely because ICON-CH had no skill for it.

    Points are forecast-only — there is no archive/backfill equivalent of
    this function.

    Each day also writes a ``ForecastPointWeatherHistory`` row keyed on
    ``(point, day, target_date)``, inside the same transaction, retaining
    this issue's view of the day before a later run overwrites the row
    above (SNOW-575).

    Args:
        point: The ForecastPoint to fetch weather for.
        target_date: The first calendar date of the forecast window, and
            the ``issued_date`` recorded against every history row written
            by this call.
        commit: If True, write the rows to the database. If False, the
            HTTP request still executes (real API probe) but no rows are
            written and an empty list is returned.
        base_url: When set, overrides ``OPEN_METEO_API_BASE_URL`` as the
            endpoint base. The actual request goes to
            ``f"{base_url}/forecast"``. Defaults to ``None``, which falls
            back to the configured host and sends the ``apikey`` parameter.
        on_fetched: Optional callback called once after the response is
            parsed, with a dict for ``target_date`` (day 0) —
            ``{forecast_point_id, date, weather_code, sunrise, sunset,
            captured_at}``. Defaults to ``None`` (no-op).

    Returns:
        A list of ``(ForecastPointWeather, created)`` tuples — one per day
        in the forecast window — when ``commit=True``, where ``created`` is
        True for a new row or False for an update. Returns an empty list
        when ``commit=False``.

    Raises:
        requests.HTTPError: If the Open-Meteo API returns a non-2xx status.
        KeyError: If ``weather_code``, ``sunrise``, or ``sunset`` are absent
            from the API response.
        ValueError: If ``daily.time`` is empty or a required per-day array
            does not align 1:1 with it — the batch is rejected rather than
            stored under invented dates (SNOW-466).

    """
    url = open_meteo.request_url(open_meteo.FORECAST, base_url)
    end_date = target_date + timedelta(days=POINT_FORECAST_DAYS - 1)
    logger.debug(
        "Fetching forecast weather for point=%s start=%s end=%s commit=%s url=%s",
        point.pk,
        target_date,
        end_date,
        commit,
        url,
    )

    params: dict[str, str] = open_meteo.with_api_key(
        {
            "latitude": str(point.latitude),
            "longitude": str(point.longitude),
            "elevation": str(point.elevation),
            "daily": POINT_DAILY_VARIABLES,
            "hourly": POINT_HOURLY_VARIABLES,
            "timezone": "auto",
            "start_date": target_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        base_url,
    )

    data = _fetch_point_payload(url, params, point)

    daily = data["daily"]
    hourly: dict[str, Any] | None = data.get("hourly")
    # Key rows off the provider's own dates, validated to align with the
    # per-day arrays — never target_date + idx (SNOW-466).
    dates = _point_daily_dates(daily, point.pk)
    num_days = len(dates)

    weather_code: int = daily["weather_code"][0]
    sunrise_str: str = daily["sunrise"][0]
    sunset_str: str = daily["sunset"][0]

    logger.debug(
        "Open-Meteo forecast: point=%s date=%s code=%d sunrise=%s sunset=%s days=%d",
        point.pk,
        dates[0],
        weather_code,
        sunrise_str,
        sunset_str,
        num_days,
    )

    if on_fetched is not None:
        on_fetched(
            {
                "forecast_point_id": point.pk,
                "date": dates[0].isoformat(),
                "weather_code": weather_code,
                "sunrise": sunrise_str,
                "sunset": sunset_str,
                "captured_at": django_timezone.now().isoformat(),
            }
        )

    if not commit:
        return []

    results: list[tuple[ForecastPointWeather, bool]] = []
    # One transaction for the whole window so a mid-loop failure can't commit
    # a partial date range (SNOW-546, same idiom as the archive path above).
    # _build_point_defaults parses sunrise/sunset inside this loop, so a single
    # malformed timestamp on day 4 of 7 would otherwise leave days 0-3 written
    # and days 4-6 missing, while fetch_all_points counted the point as failed.
    with transaction.atomic():
        for idx in range(num_days):
            day = dates[idx]
            defaults = _build_point_defaults(daily, idx)
            defaults["freezing_level_height"] = _daily_max_freezing_level(hourly, day)
            defaults["hourly_series"] = (
                _hourly_rows_for_day(hourly, day)
                if hourly and idx < POINT_HOURLY_DAYS
                else None
            )
            weather, created = ForecastPointWeather.objects.update_or_create(
                forecast_point=point,
                valid_for_date=day,
                defaults=defaults,
            )
            action = "Created" if created else "Updated"
            logger.debug(
                "%s ForecastPointWeather: point=%s date=%s code=%d",
                action,
                point.pk,
                day,
                defaults["weather_code"],
            )

            # Retain this issue's view of the day before the next run
            # overwrites the row above (SNOW-575). Keyed on issued_date, so
            # the four runs within a day collapse to one row and a forecast
            # day accrues one row per day of its window.
            ForecastPointWeatherHistory.objects.update_or_create(
                forecast_point=point,
                valid_for_date=day,
                issued_date=target_date,
                defaults={
                    **_build_history_defaults(defaults),
                    "lead_days": (day - target_date).days,
                },
            )

            results.append((weather, created))

    return results


def fetch_all_points(
    target_date: date,
    *,
    commit: bool,
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    """
    Fetch weather for every active ForecastPoint (referenced by a Favourite).

    Iterates ``ForecastPoint.objects.active()`` — points with no favourites
    simply fall out of scope (the eviction mechanism; rows are retained,
    not deleted). Per-point HTTP failures are caught, logged, and counted
    (counter: ``failed``) — they do not abort the batch. All active points
    have non-null coordinates and elevation, so ``skipped`` stays ``0``, but
    the key is kept for counter-shape symmetry with ``fetch_all_regions``.

    Each point now writes ``POINT_FORECAST_DAYS`` (7) rows per call, so
    ``created``/``updated`` sum across every day of every point's window,
    not one count per point.

    Args:
        target_date: The first calendar date of the forecast window.
        commit: If True, write rows to the database.
        base_url: When set, overrides the configured host for all
            per-point calls. Defaults to ``None``.
        on_fetched: Optional callback forwarded to each per-point call.
            Called once per fetched point, for day 0 of its window. Defaults
            to ``None`` (no-op).

    Returns:
        A dict with integer counters:
          ``created``  — new ForecastPointWeather rows written.
          ``updated``  — existing ForecastPointWeather rows updated.
          ``failed``   — points where the HTTP call raised an exception.
          ``skipped``  — kept for counter-shape symmetry; always 0.

    """
    counts: dict[str, int] = {
        "created": 0,
        "updated": 0,
        "failed": 0,
        "skipped": 0,
    }

    # Materialise once so we can use len() without a second DB round-trip.
    points = list(ForecastPoint.objects.active().order_by("id"))
    logger.info(
        "fetch_all_points: date=%s points=%d commit=%s",
        target_date,
        len(points),
        commit,
    )

    for point in points:
        try:
            results = fetch_weather_for_point(
                point,
                target_date,
                commit=commit,
                base_url=base_url,
                on_fetched=on_fetched,
            )
            if commit:
                for _, created in results:
                    if created:
                        counts["created"] += 1
                    else:
                        counts["updated"] += 1
        except Exception:  # noqa: BLE001 — broad catch intentional: per-point failure must not abort the batch
            logger.exception(
                "Failed to fetch weather for point=%s date=%s",
                point.pk,
                target_date,
            )
            counts["failed"] += 1

    logger.info(
        "fetch_all_points done: created=%d updated=%d failed=%d skipped=%d",
        counts["created"],
        counts["updated"],
        counts["failed"],
        counts["skipped"],
    )
    return counts


def _archive_daily_dates(
    dates: list[str],
    weather_codes: list[int],
    sunrises: list[str],
    sunsets: list[str],
    *,
    region_id: str,
    start_date: date,
    end_date: date,
) -> list[date]:
    """Validate the archive ``daily`` arrays and return the parsed dates.

    ``zip()`` over the four parallel arrays silently truncates to the shortest,
    so a response short by one array (e.g. 10 dates but 9 sunsets) would write
    a partial batch and leave an older row for the dropped day as stale
    mixed-generation data. This validates first: the four arrays must share one
    length, and the parsed dates must cover ``[start_date, end_date]``
    inclusive, contiguously and in order (SNOW-467).

    Args:
        dates: The ``daily.time`` array (ISO date strings).
        weather_codes: The ``daily.weather_code`` array.
        sunrises: The ``daily.sunrise`` array.
        sunsets: The ``daily.sunset`` array.
        region_id: The region identifier, for error messages only.
        start_date: First requested date (inclusive).
        end_date: Last requested date (inclusive).

    Returns:
        The parsed provider dates, one per day, in order.

    Raises:
        ValueError: If the arrays differ in length, or the dates do not cover
            the requested range contiguously and in order.

    """
    lengths = {
        "time": len(dates),
        "weather_code": len(weather_codes),
        "sunrise": len(sunrises),
        "sunset": len(sunsets),
    }
    if len(set(lengths.values())) != 1:
        raise ValueError(
            f"Open-Meteo archive for region={region_id}: daily arrays differ in "
            f"length {lengths} — refusing to store a partial batch."
        )

    parsed = [date.fromisoformat(d) for d in dates]
    expected = [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]
    if parsed != expected:
        raise ValueError(
            f"Open-Meteo archive for region={region_id}: daily dates do not "
            f"cover {start_date}..{end_date} contiguously and in order "
            f"(got {len(parsed)} date(s)) — refusing to store."
        )
    return parsed


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
        base_url: When set, overrides ``OPEN_METEO_ARCHIVE_BASE_URL`` as
            the endpoint base. The actual request goes to
            ``f"{base_url}/archive"``. Defaults to ``None``, which falls
            back to the configured host and sends the ``apikey`` parameter.
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
        ValueError: If the daily arrays differ in length or the dates do not
            cover the requested range contiguously and in order — the batch is
            rejected rather than written partially (SNOW-467).

    """
    centre: Centre = cast(Centre, region.centre)
    url = open_meteo.request_url(open_meteo.ARCHIVE, base_url)
    logger.debug(
        "Fetching archive weather for region=%s start=%s end=%s commit=%s url=%s",
        region.region_id,
        start_date,
        end_date,
        commit,
        url,
    )

    archive_params: dict[str, str] = open_meteo.with_api_key(
        {
            "latitude": str(centre["lat"]),
            "longitude": str(centre["lon"]),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": "weather_code,sunrise,sunset",
            "timezone": "auto",
        },
        base_url,
    )
    response = requests.get(url, params=archive_params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data: dict[str, Any] = response.json()

    daily = data["daily"]
    dates: list[str] = daily["time"]
    weather_codes: list[int] = daily["weather_code"]
    sunrises: list[str] = daily["sunrise"]
    sunsets: list[str] = daily["sunset"]

    # Reject a truncated / inconsistent response before emitting or writing
    # anything — never zip() the parallel arrays unvalidated (SNOW-467).
    parsed_dates = _archive_daily_dates(
        dates,
        weather_codes,
        sunrises,
        sunsets,
        region_id=region.region_id,
        start_date=start_date,
        end_date=end_date,
    )

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
    # One transaction for the whole batch so a mid-loop failure can't commit a
    # partial date range (SNOW-467).
    with transaction.atomic():
        for day, code, sunrise_str, sunset_str in zip(
            parsed_dates, weather_codes, sunrises, sunsets
        ):
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
        base_url: When set, overrides the configured host for all
            per-region calls. Defaults to ``None``.
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
            logger.exception(
                "Failed to backfill weather for region=%s start=%s end=%s",
                region.region_id,
                start_date,
                end_date,
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
        """Fetch weather for the region and date in a background thread."""
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
            logger.exception(
                "fetch_weather_async failed: region=%s date=%s",
                region.region_id,
                target_date,
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
