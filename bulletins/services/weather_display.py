"""
bulletins/services/weather_display.py — Render-time helpers for WeatherSnapshot.

Maps the Open-Meteo WMO weather interpretation code (0–99) onto a small set
of display buckets, and computes whether a given moment falls inside the
snapshot's day window. Lifted out of ``public/views.py`` so the bucket
table and the day/night logic can be unit-tested in isolation, and so the
view doesn't grow another lookup constant.

The bucket vocabulary is intentionally short — the visual design needs
distinct backgrounds, not a 1:1 mapping of WMO codes. Codes that don't
appear in the table fall back to ``cloudy`` (a safe, neutral-looking band
rather than a missing-data sentinel).

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

import datetime
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from bulletins.models import WeatherSnapshot


# Bucket identifiers — kept short and dash-free so they sit cleanly inside
# CSS class / data-attribute selectors. Exposed as a tuple so call sites can
# enumerate them (e.g. for tests or admin help text) without importing the
# private dict below.
WEATHER_BUCKETS: tuple[str, ...] = (
    "clear",
    "partly_cloudy",
    "cloudy",
    "fog",
    "rain",
    "snow",
    "thunder",
)

DEFAULT_BUCKET: str = "cloudy"

# Map every WMO code we expect to see onto one of the buckets above. Codes
# that aren't listed (rare or vendor-specific) fall back to DEFAULT_BUCKET
# in :func:`weather_code_bucket` rather than raising, so a single rogue code
# can never take the page out.
_WMO_CODE_TO_BUCKET: dict[int, str] = {
    0: "clear",
    1: "partly_cloudy",
    2: "partly_cloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "rain",
    53: "rain",
    55: "rain",
    56: "rain",
    57: "rain",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "rain",
    67: "rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    80: "rain",
    81: "rain",
    82: "rain",
    85: "snow",
    86: "snow",
    95: "thunder",
    96: "thunder",
    99: "thunder",
}


def weather_code_bucket(code: int) -> str:
    """
    Return the display bucket for a WMO weather interpretation code.

    Unknown codes resolve to :data:`DEFAULT_BUCKET` so the header always
    has a renderable bucket — there is no "unknown" visual state.

    Args:
        code: A WMO weather interpretation code (0–99).

    Returns:
        One of the bucket identifiers in :data:`WEATHER_BUCKETS`.

    """
    return _WMO_CODE_TO_BUCKET.get(code, DEFAULT_BUCKET)


def is_day(weather: "WeatherSnapshot", now: datetime.datetime) -> bool:
    """
    Return ``True`` if the wall-clock ``now`` sits inside the day window.

    Compares **time-of-day only**, not full instants. The user's current
    wall-clock time is projected onto the snapshot's day — so at 11:00
    local, every past or future date the user navigates to renders as
    daytime; at 23:00 local, every date renders as night. This matches
    user expectation for a calendar that is dominated by historical
    pages: the sun rose and set on those days too, and the visual
    should track the time the user is *looking* at the page, not the
    real-world instant the snapshot was taken.

    Comparison is done in the snapshot's local timezone (Open-Meteo
    returns sunrise/sunset with the region's offset, e.g. ``+02:00``),
    so a viewer browsing from a different timezone still sees a
    visual that lines up with the bulletin region's daylight.

    Daylight is sunrise-inclusive and sunset-exclusive, so the boundary
    instants land in night only on the sunset side.

    Args:
        weather: The :class:`bulletins.models.WeatherSnapshot` to evaluate.
        now: The reference instant (typically ``timezone.now()``).

    Returns:
        ``True`` for daytime, ``False`` for night.

    """
    local_now = now.astimezone(weather.sunrise.tzinfo)
    return weather.sunrise.time() <= local_now.time() < weather.sunset.time()


class WeatherDisplay(TypedDict):
    """Context dict consumed by ``includes/bulletin_weather_header.html``."""

    weather: "WeatherSnapshot"
    bucket: str
    is_day: bool
    time_of_day: str  # "day" or "night" — pre-computed for template clarity.
    sunrise_local: str  # "HH:MM" in the snapshot's local tz (debug overlay).
    sunset_local: str  # "HH:MM" in the snapshot's local tz (debug overlay).


def build_weather_display(
    weather: "WeatherSnapshot | None", now: datetime.datetime
) -> WeatherDisplay | None:
    """
    Build the template context for the weather header partial.

    Returns ``None`` when no snapshot is available so the template can
    short-circuit to its safe fallback. Pre-computes ``bucket`` and
    ``time_of_day`` here (rather than via template tags) to keep the
    partial dumb — it only emits attributes it is handed.

    Args:
        weather: The snapshot for the bulletin's calendar day, or ``None``
            when none has been fetched yet.
        now: The reference instant for the day/night decision.

    Returns:
        A :class:`WeatherDisplay` dict, or ``None`` when ``weather`` is
        ``None``.

    """
    if weather is None:
        return None
    daytime = is_day(weather, now)
    return WeatherDisplay(
        weather=weather,
        bucket=weather_code_bucket(weather.weather_code),
        is_day=daytime,
        time_of_day="day" if daytime else "night",
        # Format in the snapshot's stored offset (e.g. +02:00 for Switzerland)
        # so the debug overlay shows the wall-clock time of sunrise/sunset
        # *at the bulletin region*, not the Django-active TIME_ZONE.
        sunrise_local=weather.sunrise.strftime("%H:%M"),
        sunset_local=weather.sunset.strftime("%H:%M"),
    )
