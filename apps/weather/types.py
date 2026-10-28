"""
apps/weather/types.py — Documented shapes for the two Weather JSON columns.

``Weather.hourly`` and ``Weather.forecast`` are JSON, which means their
shape is a contract with no schema behind it. The two ``TypedDict``s that
write it down — ``HourlyRow`` and ``ForecastDay`` — are defined beside the
parsing that produces them, in ``apps.ingest.normalise.weather`` (SNOW-1248),
and imported here so the weather app's consumers keep one name to read.
Read that module's docstring for the shape, and above all for why
``ForecastDay["hourly"]`` must be read with ``.get()``.
"""

from apps.ingest.public import ForecastDay, HourlyRow

__all__ = ["ForecastDay", "HourlyRow"]
