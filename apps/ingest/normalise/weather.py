"""
apps/ingest/normalise/weather.py — Pure parsing of Open-Meteo forecast responses.

Moved out of ``apps/weather/services/fetch.py`` (SNOW-1248), which keeps the
HTTP and the write. Everything here takes the ``daily`` and ``hourly`` blocks
of an Open-Meteo response and returns plain values; none of it reads a
location, a setting or the database. ``apps/weather/services/backfill.py``
reads the historical host's responses through the same functions, because
the two endpoints answer in the same shape.

Also the two shapes the parsing produces, which are the contract for the
``Weather.hourly`` and ``Weather.forecast`` JSON columns:

  HourlyRow    one hour of one day — the shape of every entry in
               ``Weather.hourly`` and in a ``ForecastDay["hourly"]``.
  ForecastDay  one forward day — the shape of every entry in
               ``Weather.forecast``.

**``ForecastDay.hourly`` is optional.** The stored day carries its own hourly
series in the ``hourly`` column; the forward days carry theirs nested in their
``forecast[]`` entry, but only for the first ``HOURLY_DAYS`` of them (see
``apps.weather.services.fetch``). Beyond that the key is **absent**, keeping
the stored JSON bounded. Every consumer must test for it —
``day.get("hourly")`` — rather than assuming it is there. ``NotRequired`` on
the key is what makes that a type error to forget.

Two behaviours here each have their own test, because each was a bug found the
hard way: rows key off the provider's own ``daily.time`` (SNOW-466,
``daily_dates``), and a day past the model's horizon is dropped rather than
stored as null (SNOW-628, ``day_is_complete``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, NotRequired, TypedDict, cast


class HourlyRow(TypedDict):
    """One hour of one day, as stored in an hourly series.

    ``time`` is the provider's own ISO-8601 string with its UTC offset
    preserved — local time, not normalised to UTC, because the consumer
    wants the hour as it is experienced at the location. Every other key
    is ``None`` when Open-Meteo omitted that variable for the hour.
    """

    time: str
    temperature_2m: float | None
    snowfall: float | None
    precipitation: float | None
    wind_speed_10m: float | None
    wind_gusts_10m: float | None
    wind_direction_10m: float | None
    freezing_level_height: float | None


class ForecastDay(TypedDict):
    """One forward day, as known on the parent row's ``observed_on``.

    The daily scalars mirror the model's own columns, so a forward day and
    a stored day read the same way. ``date`` is an ISO-8601 date string —
    JSON has no date type — and comes from the provider's own ``daily.time``
    array rather than being derived by offset (SNOW-466).

    ``sunrise`` and ``sunset`` are carried per entry so a forward day can
    pick a day or night icon without a second lookup; they are already
    fetched for every day in the window, so including them is free.

    ``hourly`` is present only on the first ``HOURLY_DAYS`` entries — read
    it with ``.get()``.
    """

    date: str
    weather_code: int
    sunrise: str
    sunset: str
    temperature_2m_max: float | None
    temperature_2m_min: float | None
    apparent_temperature_max: float | None
    apparent_temperature_min: float | None
    precipitation_sum: float | None
    snowfall_sum: float | None
    precipitation_probability_max: int | None
    precipitation_hours: float | None
    wind_speed_10m_max: float | None
    wind_gusts_10m_max: float | None
    wind_direction_10m_dominant: float | None
    uv_index_max: float | None
    daylight_duration: float | None
    sunshine_duration: float | None
    freezing_level_height: float | None
    hourly: NotRequired[list[HourlyRow]]


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
    "wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
    "freezing_level_height"
)

# The keys written onto each HourlyRow, in order. Must match ``HourlyRow``
# above.
_HOURLY_FIELDS = (
    "temperature_2m",
    "snowfall",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
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


def parse_dt_preserve_offset(value: str) -> datetime:
    """
    Parse an ISO-8601 datetime, preserving the original UTC offset.

    Open-Meteo returns sunrise/sunset and hourly timestamps with the
    location's own offset when ``timezone=auto`` is requested — e.g.
    ``"2026-05-01T05:32+02:00"``. The offset is deliberately **not**
    normalised to UTC: a consumer comparing "is it light yet" wants the
    hour as it is experienced at the location, not in Greenwich.

    This is the opposite of ``apps.ingest.normalise.slf.parse_dt``, which always
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


def daily_dates(daily: dict[str, Any], location_pk: int) -> list[date]:
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


def day_is_complete(daily: dict[str, Any], idx: int) -> bool:
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


def hourly_rows_for_day(hourly: dict[str, Any] | None, day: date) -> list[HourlyRow]:
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
        if parse_dt_preserve_offset(time_str).date() != day:
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


def daily_max_freezing_level(hourly: dict[str, Any] | None, day: date) -> float | None:
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
        for row in hourly_rows_for_day(hourly, day)
        if row["freezing_level_height"] is not None
    ]
    return max(values) if values else None


def daily_fields(daily: dict[str, Any], idx: int) -> dict[str, Any]:
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
        "sunrise": parse_dt_preserve_offset(daily["sunrise"][idx]),
        "sunset": parse_dt_preserve_offset(daily["sunset"][idx]),
    }
    for key in _OPTIONAL_DAILY_FIELDS:
        fields[key] = _optional(key)
    return fields


def forecast_entry(
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
    fields = daily_fields(daily, idx)
    entry: dict[str, Any] = {
        "date": day.isoformat(),
        **fields,
        # Back to ISO strings: these came out of daily_fields as datetimes
        # for the model's columns, and this one is going into JSON.
        "sunrise": daily["sunrise"][idx],
        "sunset": daily["sunset"][idx],
        "freezing_level_height": daily_max_freezing_level(hourly, day),
    }
    if with_hourly:
        rows = hourly_rows_for_day(hourly, day)
        if rows:
            entry["hourly"] = rows
    return cast(ForecastDay, entry)
