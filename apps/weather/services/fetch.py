"""
apps/weather/services/fetch.py — Fetch Open-Meteo weather for the location estate.

Two functions, one walk:

  fetch_weather_for_location(location, observed_on, *, commit, base_url)
      One Open-Meteo forecast request for one location, covering
      ``FORECAST_DAYS`` days. Writes one ``Weather`` row — the day
      ``observed_on`` — carrying that day's scalars and hourly series, plus
      the days after it in the ``forecast`` column.

  fetch_all_locations(observed_on, *, commit, base_url, on_location)
      Streams ``Location.objects.active()`` and calls the above for each,
      catching per-location failures so one bad location cannot abort the
      batch. Returns counters.

**One pass over one anchor.** This replaces two passes over two anchors —
a region-centroid pass writing ``WeatherSnapshot`` and a quantised-cell pass
writing ``ForecastCellWeather``. Both anchors are now ``Location``, so there
is one variable set, one request shape and one row per location per run.

**No fetch-time dedup.** The quantisation the old ``ForecastCell`` grid did
was measured on 2026-08-29: 240 real places produced 240 distinct cells,
zero sharing, in a test rigged in its favour. It existed to keep a *table*
small, and there is no longer a table for it to keep small. If
``Location.objects.active()`` ever widens to every favourite the estate
could reach thousands with genuine clustering — re-measure then.

**Three behaviours carried across from the fetcher SNOW-762 stripped.**
Each is a bug that was found the hard way and each has its own test:

* **SNOW-466** — rows key off the provider's own ``daily.time`` array,
  never ``observed_on + idx``. A shifted or gapped response is rejected
  rather than silently attaching every day's weather to the wrong date.
* **SNOW-628** — a day past the backing model's horizon comes back with a
  null ``weather_code`` while ``sunrise``/``sunset`` (astronomical, not
  modelled) stay populated. Those days are **dropped before the write**,
  not raised on. Filtering first is what stops the unusable tail rolling
  back the near-term days a skier actually reads; point forecasts once
  wrote zero rows because it did not.
* **SNOW-546** — the write is wrapped in ``transaction.atomic()`` per
  location, so a malformed timestamp partway through cannot leave a
  half-written row behind while the batch counts the location as failed.

Addressing reuses ``apps.locations.services.open_meteo`` — ``request_url``
takes any endpoint segment, so weather supplies its own ``FORECAST``
constant and shares the host and customer-key resolution rather than
duplicating it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import requests
from django.db import transaction

from apps.locations.models import Location
from apps.locations.services import open_meteo
from apps.weather.models import Weather
from apps.weather.services.upsert import upsert_weather
from apps.weather.types import ForecastDay, HourlyRow

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds

# Endpoint path segment, appended to the resolved Open-Meteo base URL.
# Weather owns its own endpoint constants; the locations service owns the
# host and key resolution that ``request_url`` applies to them.
FORECAST = "forecast"

# How many days each request covers: the day itself plus a week-ahead
# outlook. ``observed_on`` becomes the row; the rest become ``forecast``.
FORECAST_DAYS = 7

# How many days carry an hourly series. ``observed_on`` always does, in its
# own column; the next day carries one nested in its ``forecast`` entry.
# Bounded because the series is the bulk of the stored JSON and the
# near-term days are the ones anyone plans an hour-by-hour day around.
HOURLY_DAYS = 2

# The daily variables requested. Ordered as the model declares them so the
# two lists can be read against each other.
DAILY_VARIABLES = (
    "weather_code,sunrise,sunset,"
    "temperature_2m_max,temperature_2m_min,"
    "apparent_temperature_max,apparent_temperature_min,"
    "precipitation_sum,snowfall_sum,"
    "precipitation_probability_max,precipitation_hours,"
    "wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,"
    "uv_index_max,daylight_duration,sunshine_duration"
)

# Ski-relevant hourly variables. Open-Meteo publishes no daily
# freezing-level aggregate, so the daily column is derived from this block.
HOURLY_VARIABLES = (
    "temperature_2m,snowfall,precipitation,"
    "wind_speed_10m,wind_gusts_10m,freezing_level_height"
)

# The keys written onto each HourlyRow, in order. Must match
# ``apps.weather.types.HourlyRow``.
_HOURLY_FIELDS = (
    "temperature_2m",
    "snowfall",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "freezing_level_height",
)

# The daily scalars read via a degrade-to-None accessor. ``weather_code``,
# ``sunrise`` and ``sunset`` are absent because they are required — they are
# indexed directly and a day missing one is dropped, not stored as null.
_OPTIONAL_DAILY_FIELDS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_sum",
    "snowfall_sum",
    "precipitation_probability_max",
    "precipitation_hours",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "uv_index_max",
    "daylight_duration",
    "sunshine_duration",
)

# The fields required for a day to be storable at all. All three are
# non-null on the model, and all three are what a day past the model's
# horizon comes back missing (SNOW-628).
_REQUIRED_DAILY_FIELDS = ("weather_code", "sunrise", "sunset")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_dt_preserve_offset(value: str) -> datetime:
    """
    Parse an ISO-8601 datetime, preserving the original UTC offset.

    Open-Meteo returns sunrise/sunset and hourly timestamps with the
    location's own offset when ``timezone=auto`` is requested — e.g.
    ``"2026-05-01T05:32+02:00"``. The offset is deliberately **not**
    normalised to UTC: a consumer comparing "is it light yet" wants the
    hour as it is experienced at the location, not in Greenwich.

    This is the opposite of ``slf_fetcher._parse_dt``, which always
    normalises. Naive input is assumed UTC.

    Args:
        value: An ISO-8601 formatted datetime string.

    Returns:
        A tz-aware datetime, in the input's own offset.

    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _daily_dates(daily: dict[str, Any], location_pk: int) -> list[date]:
    """
    Parse and validate the provider's ``daily.time`` array.

    The returned dates are what every stored day is keyed off. Deriving
    them as ``observed_on + idx`` instead would attach weather to the wrong
    calendar day the moment a response came back shifted, gapped or
    reordered, and would do it silently (SNOW-466).

    Args:
        daily: The ``"daily"`` block of an Open-Meteo forecast response.
        location_pk: The location's pk, for error messages only.

    Returns:
        The provider-supplied dates, in response order.

    Raises:
        ValueError: ``time`` is empty, or a required per-day array does not
            align 1:1 with it — a misaligned batch is rejected whole.
        KeyError: A required array is absent entirely.

    """
    dates = [date.fromisoformat(d) for d in daily["time"]]
    if not dates:
        raise ValueError(
            f"Open-Meteo forecast for location={location_pk}: empty 'time' array."
        )
    for name in _REQUIRED_DAILY_FIELDS:
        values = daily[name]
        if len(values) != len(dates):
            raise ValueError(
                f"Open-Meteo forecast for location={location_pk}: '{name}' has "
                f"{len(values)} entries but 'time' has {len(dates)} — refusing "
                f"to store misaligned data."
            )
    return dates


def _day_is_complete(daily: dict[str, Any], idx: int) -> bool:
    """
    Return whether every required field carries a value for one day.

    A day beyond the backing model's horizon returns a null
    ``weather_code`` while ``sunrise``/``sunset`` stay populated, because
    those are astronomical rather than modelled. Such a day cannot be
    stored against three non-null columns, so callers drop it (SNOW-628).

    Args:
        daily: The ``"daily"`` block of an Open-Meteo forecast response.
        idx: Index of the day to check.

    Returns:
        True when all three required fields are present and non-null.

    """
    for key in _REQUIRED_DAILY_FIELDS:
        values = daily.get(key)
        if not values or idx >= len(values) or values[idx] is None:
            return False
    return True


def _hourly_rows_for_day(hourly: dict[str, Any] | None, day: date) -> list[HourlyRow]:
    """
    Return one day's hourly rows from an Open-Meteo hourly block.

    Args:
        hourly: The ``"hourly"`` block — a dict of parallel arrays keyed by
            variable name, plus ``"time"`` — or None when the response
            carried none.
        day: The calendar date to filter to.

    Returns:
        One ``HourlyRow`` per matching hour, each variable None where
        Open-Meteo omitted it. Empty when there is no hourly block or no
        hour falls on ``day``.

    """
    if not hourly:
        return []
    times: list[str] = hourly.get("time", [])
    rows: list[HourlyRow] = []
    for idx, time_str in enumerate(times):
        if _parse_dt_preserve_offset(time_str).date() != day:
            continue
        row: dict[str, Any] = {"time": time_str}
        for field in _HOURLY_FIELDS:
            values = hourly.get(field)
            row[field] = (
                values[idx] if values is not None and idx < len(values) else None
            )
        # Safe by construction: the dict is built from _HOURLY_FIELDS, which
        # is the same tuple HourlyRow declares, so there is nothing a
        # runtime check could catch that a drift in that tuple would not
        # already break at the model.
        rows.append(cast(HourlyRow, row))
    return rows


def _daily_max_freezing_level(hourly: dict[str, Any] | None, day: date) -> float | None:
    """
    Derive a day's freezing level from its hourly values.

    Open-Meteo publishes no daily freezing-level aggregate, so the daily
    column is the maximum of the day's hourly readings — the height the
    freezing level reached, which is the figure that matters for whether
    the snowpack got wet.

    Args:
        hourly: The ``"hourly"`` block, or None.
        day: The calendar date to derive for.

    Returns:
        The day's maximum hourly freezing level in metres, or None when no
        hourly value is available for it.

    """
    values = [
        row["freezing_level_height"]
        for row in _hourly_rows_for_day(hourly, day)
        if row["freezing_level_height"] is not None
    ]
    return max(values) if values else None


def _daily_fields(daily: dict[str, Any], idx: int) -> dict[str, Any]:
    """
    Read one day's scalars out of the daily block.

    ``weather_code``, ``sunrise`` and ``sunset`` are indexed directly — a
    ``KeyError`` there is a genuine API-shape problem, and the caller has
    already dropped any day where they are null. Every other variable is
    read through a degrade-to-None accessor, because Open-Meteo omits some
    depending on which model backs the coordinates.

    Args:
        daily: The ``"daily"`` block of an Open-Meteo forecast response.
        idx: Index into each daily array.

    Returns:
        A mapping of column name to value.

    """

    def _optional(key: str) -> Any:
        """Return the variable at idx, or None when omitted or short."""
        values = daily.get(key)
        if values is None or idx >= len(values):
            return None
        return values[idx]

    fields: dict[str, Any] = {
        "weather_code": daily["weather_code"][idx],
        "sunrise": _parse_dt_preserve_offset(daily["sunrise"][idx]),
        "sunset": _parse_dt_preserve_offset(daily["sunset"][idx]),
    }
    for key in _OPTIONAL_DAILY_FIELDS:
        fields[key] = _optional(key)
    return fields


def _forecast_entry(
    daily: dict[str, Any],
    hourly: dict[str, Any] | None,
    idx: int,
    day: date,
    *,
    with_hourly: bool,
) -> ForecastDay:
    """
    Build one ``forecast[]`` entry for a forward day.

    Mirrors the model's own columns so a forward day reads the same way as
    a stored one, with ``sunrise``/``sunset`` kept as ISO strings (JSON has
    no datetime) so a consumer can pick a day or night icon without a
    second lookup.

    Args:
        daily: The ``"daily"`` block.
        hourly: The ``"hourly"`` block, or None.
        idx: Index of this day in the daily arrays.
        day: The provider date for this day.
        with_hourly: Whether to nest this day's hourly series. True only
            for the first ``HOURLY_DAYS`` days of the window.

    Returns:
        The entry, ready to append to ``Weather.forecast``.

    """
    fields = _daily_fields(daily, idx)
    entry: dict[str, Any] = {
        "date": day.isoformat(),
        **fields,
        # Back to ISO strings: these came out of _daily_fields as datetimes
        # for the model's columns, and this one is going into JSON.
        "sunrise": daily["sunrise"][idx],
        "sunset": daily["sunset"][idx],
        "freezing_level_height": _daily_max_freezing_level(hourly, day),
    }
    if with_hourly:
        rows = _hourly_rows_for_day(hourly, day)
        if rows:
            entry["hourly"] = rows
    return cast(ForecastDay, entry)


def _get_forecast(url: str, params: dict[str, str]) -> dict[str, Any]:
    """Issue one Open-Meteo forecast request and return the parsed payload.

    Args:
        url: The resolved request URL.
        params: The query parameters, key already applied.

    Returns:
        The parsed JSON body.

    Raises:
        requests.HTTPError: The API returned a non-2xx status.

    """
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_weather_for_location(
    location: Location,
    observed_on: date,
    *,
    commit: bool,
    base_url: str | None = None,
) -> tuple[Weather, bool] | None:
    """
    Fetch and optionally persist one location's weather for one day.

    Makes one Open-Meteo forecast request covering ``FORECAST_DAYS`` days
    from ``observed_on``. The first storable day becomes the ``Weather``
    row; the days after it become its ``forecast`` column, with the first
    ``HOURLY_DAYS`` of the window also carrying an hourly series.

    ``location.elevation_m`` is passed to the API when set, so the forecast
    is statistically downscaled to the point's own altitude rather than the
    model cell's mean terrain. A location with no elevation is still
    fetched — the forecast is simply the cell's.

    Days the backing model did not resolve are dropped before anything is
    written (SNOW-628), so a short window is the normal outcome rather than
    a failure. The write is wrapped in one transaction (SNOW-546).

    Args:
        location: The location to fetch for.
        observed_on: The first day of the window, and the day the written
            row is of.
        commit: When False the request still executes — it is a real API
            probe — but nothing is written and None is returned.
        base_url: Overrides the configured Open-Meteo host. Defaults to
            None, which uses the configured host and sends the customer key.

    Returns:
        A ``(weather, created)`` tuple when ``commit`` is True, or None
        when it is False.

    Raises:
        requests.HTTPError: The API returned a non-2xx status.
        ValueError: The response's daily arrays are misaligned (SNOW-466),
            or no day in the window is storable — which is a malformed
            payload rather than a short horizon (SNOW-628).
        KeyError: A required array is absent from the response.
        ImmutableWeatherRowError: A row already exists for this location and
            day, and that day has passed.

    """
    url = open_meteo.request_url(FORECAST, base_url)
    end_date = observed_on + timedelta(days=FORECAST_DAYS - 1)

    params: dict[str, str] = {
        "latitude": str(location.latitude),
        "longitude": str(location.longitude),
        "daily": DAILY_VARIABLES,
        "hourly": HOURLY_VARIABLES,
        "timezone": "auto",
        "start_date": observed_on.isoformat(),
        "end_date": end_date.isoformat(),
    }
    if location.elevation_m is not None:
        params["elevation"] = str(location.elevation_m)

    logger.debug(
        "Fetching weather for location=%s start=%s end=%s commit=%s url=%s",
        location.pk,
        observed_on,
        end_date,
        commit,
        url,
    )

    data = _get_forecast(url, open_meteo.with_api_key(params, url))

    daily = data["daily"]
    hourly: dict[str, Any] | None = data.get("hourly")

    # Provider dates, validated to align with the per-day arrays (SNOW-466).
    dates = _daily_dates(daily, location.pk)

    # Drop the days the model did not resolve, BEFORE the transaction below
    # opens. Filtering here is what stops an unusable tail rolling back the
    # near-term days the surfaces actually read (SNOW-628).
    storable = [idx for idx in range(len(dates)) if _day_is_complete(daily, idx)]
    if not storable:
        raise ValueError(
            f"Open-Meteo forecast for location={location.pk}: no day carries a "
            f"complete weather_code/sunrise/sunset — refusing to store."
        )
    if len(storable) < len(dates):
        logger.info(
            "Open-Meteo forecast for location=%s: %d of %d day(s) resolved — "
            "the rest fall beyond the model's horizon",
            location.pk,
            len(storable),
            len(dates),
        )

    first = storable[0]
    if first != 0:
        # Losing the tail is routine; losing day 0 is not, since that is the
        # day the row is OF. Worth a warning, but not worth discarding the
        # days that did resolve — which is what raising here would do.
        logger.warning(
            "Open-Meteo forecast for location=%s: day 0 (%s) carries no usable "
            "data — recording %s instead",
            location.pk,
            dates[0],
            dates[first],
        )

    row_date = dates[first]
    fields = _daily_fields(daily, first)
    fields["freezing_level_height"] = _daily_max_freezing_level(hourly, row_date)
    fields["hourly"] = _hourly_rows_for_day(hourly, row_date) or None
    fields["forecast"] = [
        _forecast_entry(
            daily,
            hourly,
            idx,
            dates[idx],
            with_hourly=position < HOURLY_DAYS,
        )
        for position, idx in enumerate(storable[1:], start=1)
    ]

    if not commit:
        return None

    # One transaction for the row and nothing else — the whole unit of work
    # for this location, so a failure here cannot leave a half-written row
    # while the batch counts the location as failed (SNOW-546).
    with transaction.atomic():
        return upsert_weather(location, row_date, **fields)


def fetch_all_locations(
    observed_on: date,
    *,
    commit: bool,
    base_url: str | None = None,
    locations: Iterable[Location] | None = None,
    on_location: Callable[[Location], None] | None = None,
) -> dict[str, int]:
    """
    Fetch weather for every active location.

    Walks ``Location.objects.active()`` — a location reachable from a
    resort, a region centroid or a favourite. A per-location failure is
    caught, logged and counted; it never aborts the batch, so one region
    whose coordinates Open-Meteo dislikes cannot cost every other location
    its daily row.

    Args:
        observed_on: The day to fetch, and the first day of each window.
        commit: When False, the requests still execute but nothing is
            written.
        base_url: Overrides the configured Open-Meteo host.
        locations: The locations to walk. Defaults to None, which resolves
            to ``Location.objects.active()``. The command passes its own
            streamed, countdown-printing iterable through here rather than
            materialising one.
        on_location: Called once per location before its request, for
            progress reporting. Defaults to None.

    Returns:
        A mapping with integer counters: ``created`` (new rows),
        ``updated`` (rows refined in place) and ``failed`` (locations whose
        fetch raised).

    """
    counts: dict[str, int] = {"created": 0, "updated": 0, "failed": 0}

    if locations is None:
        locations = Location.objects.active().iterator()

    for location in locations:
        if on_location is not None:
            on_location(location)
        try:
            result = fetch_weather_for_location(
                location,
                observed_on,
                commit=commit,
                base_url=base_url,
            )
        except Exception:  # noqa: BLE001 — broad catch intentional: one location must not abort the batch
            logger.exception(
                "Failed to fetch weather for location=%s observed_on=%s",
                location.pk,
                observed_on,
            )
            counts["failed"] += 1
            continue
        if result is not None:
            _, created = result
            counts["created" if created else "updated"] += 1

    logger.info(
        "fetch_all_locations done: observed_on=%s created=%d updated=%d failed=%d "
        "commit=%s",
        observed_on,
        counts["created"],
        counts["updated"],
        counts["failed"],
        commit,
    )
    return counts
