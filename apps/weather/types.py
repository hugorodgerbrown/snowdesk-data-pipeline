"""
apps/weather/types.py — Documented shapes for the two Weather JSON columns.

``Weather.hourly`` and ``Weather.forecast`` are JSON, which means their
shape is a contract with no schema behind it. These ``TypedDict``s are that
contract written down, so a consumer reads the shape here rather than
inferring it from a sample row.

  HourlyRow    one hour of one day — the shape of every entry in
               ``Weather.hourly`` and in a ``ForecastDay["hourly"]``.
  ForecastDay  one forward day — the shape of every entry in
               ``Weather.forecast``.

**``ForecastDay.hourly`` is optional.** ``Weather.observed_on`` carries its
own hourly series in the ``hourly`` column; the forward days carry theirs
nested in their ``forecast[]`` entry, but only for the first
``HOURLY_DAYS`` of them (see ``apps.weather.services.fetch``). Beyond that
the key is **absent**, keeping the stored JSON bounded. Every consumer must
test for it — ``day.get("hourly")`` — rather than assuming it is there.
``total=False`` on the class is what makes that a type error to forget.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


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
