"""
apps/weather/types.py — The documented shape of the Weather JSON column.

``Weather.hourly`` is JSON, which means its shape is a contract with no
schema behind it. The ``TypedDict`` that writes it down, ``HourlyRow``, is
defined beside the parsing that produces it, in
``apps.ingest.normalise.weather`` (SNOW-1248), and imported here so the
weather app's consumers keep one name to read. Every row carries its own
hourly series since SNOW-1337; there is no forward-day shape any more.
"""

from apps.ingest.public import HourlyRow

__all__ = ["HourlyRow"]
