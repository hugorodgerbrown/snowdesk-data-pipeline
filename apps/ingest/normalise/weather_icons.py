"""
apps/ingest/normalise/weather_icons.py — WMO weather code → icon bucket.

The weather service's reading of a WMO weather interpretation code (0–99):
which of the twelve Yr / MET Norway drawings stands for it. Moved here from
``apps/weather/services/weather_display.py`` (SNOW-1343) so the service and
every reader — the
server-rendered panels and the map, which used to carry its own mirror of
this table in ``static/js/map_weather_core.js`` — reads one answer instead of
deriving its own.

The bucket names one drawing, not one file: ``clear`` and ``partly_cloudy``
ship a day and a night variant (``WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT``), and
which of the two a surface shows is decided where it is read, against the
day's stored sunrise and sunset — never here, because the hour the reader
looks is not a property of the forecast.

Pure: no Django, no settings, no other ``apps.*`` package
(``tests/ingest/test_boundary.py``).

WMO code reference:
  0           Clear sky
  1, 2        Mainly clear, partly cloudy
  3           Overcast
  45, 48      Fog
  51–57       Drizzle (incl. freezing)
  61–67       Rain (incl. freezing)
  80–82       Rain showers
  71–77       Snowfall and snow grains
  85, 86      Snow showers
  95          Thunderstorm
  96, 99      Thunderstorm with hail
"""

from __future__ import annotations

# The neutral default for an unrecognised code — a renderable bucket rather
# than a missing-data sentinel, so one rogue code can never take a page out.
_DEFAULT_ICON_BUCKET = "cloudy"

# Rain splits into drizzle / light / moderate / heavy and snow into light /
# moderate / heavy, so the icon tells the reader more than a colour band.
WMO_CODE_TO_ICON_BUCKET: dict[int, str] = {
    0: "clear",
    1: "partly_cloudy",
    2: "partly_cloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "drizzle",
    57: "drizzle",
    61: "light_rain",
    63: "moderate_rain",
    65: "heavy_rain",
    66: "light_rain",
    67: "heavy_rain",
    71: "light_snow",
    73: "moderate_snow",
    75: "heavy_snow",
    77: "light_snow",
    80: "light_rain",
    81: "moderate_rain",
    82: "heavy_rain",
    85: "light_snow",
    86: "heavy_snow",
    95: "thunder",
    96: "thunder",
    99: "thunder",
}

# Every icon bucket that ships separate day/night SVG variants — the two the
# icon set draws a sun or a moon in. Everything else is weather in front of a
# cloud, which looks the same by either light, so it ships as one file
# (SNOW-791). Enumerated rather than derived by subtraction: the members are
# a property of the drawings on disk, not of the bucket list, and a new
# precipitation bucket must not silently acquire a day/night pair that does
# not exist.
WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT: frozenset[str] = frozenset(
    {"clear", "partly_cloudy"}
)


def icon_bucket_for_code(code: int | None) -> str:
    """Return the icon bucket for a WMO weather interpretation code.

    Unknown or missing codes resolve to ``"cloudy"`` — the same neutral
    default every surface has always used.

    Args:
        code: A WMO weather interpretation code (0–99), or ``None``.

    Returns:
        One of the twelve icon bucket identifiers, e.g. ``"light_snow"``.

    """
    if code is None:
        return _DEFAULT_ICON_BUCKET
    return WMO_CODE_TO_ICON_BUCKET.get(code, _DEFAULT_ICON_BUCKET)
