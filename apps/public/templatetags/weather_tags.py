"""
apps/public/templatetags/weather_tags.py — Template filters for wind direction.

Turns the raw bearing Open-Meteo returns — ``wind_direction_10m`` on an
:class:`apps.weather.types.HourlyRow`, in degrees — into the two values the
hourly forecast row needs to draw it: a rotation for the arrow glyph and a
compass point for the text label.

**The bearing is the direction the wind blows FROM.** That is the
meteorological convention Open-Meteo follows, and it is the one that matters
here: a wind *from* the west loads east-facing slopes, so the aspect under
suspicion is what the number already names. **Both filters below report the
source**, and neither transforms the bearing:

``wind_arrow_rotation``
    Rotates the glyph to point **at the source** — a bearing of 270 (from
    the west) yields 270, and the arrow points west, at the weather coming
    towards the reader.

``compass_point``
    Names the same source in words — 270 is ``W``. This goes in the visible
    text and the accessible label, so a screen reader announces "from W"
    without depending on a glyph's rotation.

**They agreed only after SNOW-785.** ``wind_arrow_rotation`` used to add
180° so the glyph flew downwind, on the argument that travel is what
mainstream forecast UIs draw — leaving the arrow and the "from W" beside it
pointing opposite ways, and disagreeing outright with the hourly chart
(``apps.weather.services.hourly_chart``), which had always drawn the source
per its design handoff. Two arrows on one page meaning opposite things is
worse than either convention alone, and on an avalanche site the source is
the actionable fact: it names the aspect being loaded.
"""

import logging
from typing import Any

from django import template
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

register = template.Library()

# The eight-point compass, indexed by bearing / 45. Eight rather than
# sixteen because the label sits beside a glyph in a narrow cell, where
# "WNW" costs width without telling a reader anything the arrow has not
# already shown. Translated because the letters are initials of localised
# words — German uses O for east and NO for north-east.
_COMPASS_POINTS = (
    _("N"),
    _("NE"),
    _("E"),
    _("SE"),
    _("S"),
    _("SW"),
    _("W"),
    _("NW"),
)


def _as_bearing(value: Any) -> float | None:
    """
    Coerce a stored ``wind_direction_10m`` into a bearing in [0, 360).

    Open-Meteo returns whole degrees, but the value arrives from a JSON
    column rather than a typed field, so it is normalised here rather than
    trusted. Out-of-range values wrap instead of being rejected: 360 is a
    legitimate way to say north, and a bearing is a circular quantity where
    wrapping is the meaningful reading.

    Args:
        value: The raw ``wind_direction_10m`` entry — a number, a numeric
            string, or ``None`` when Open-Meteo omitted the variable.

    Returns:
        The bearing in degrees, or ``None`` when the value is missing or
        not numeric.

    """
    if value is None:
        return None
    try:
        return float(value) % 360
    except TypeError, ValueError:
        logger.warning("Unreadable wind_direction_10m value: %r", value)
        return None


@register.filter
def wind_arrow_rotation(value: Any) -> float | None:
    """
    Return the CSS rotation, in degrees, for a source-pointing arrow glyph.

    Assumes a base glyph pointing **up** (north) at zero rotation. The
    rotation applied is the bearing itself, so the arrow points at where
    the wind comes from — the same convention
    :func:`compass_point` names in words and
    ``apps.weather.services.hourly_chart`` draws (SNOW-785).

    Usage::

        {% with rot=hour.wind_direction_10m|wind_arrow_rotation %}
        <span style="transform: rotate({{ rot }}deg)">

    Args:
        value: The raw ``wind_direction_10m`` entry, or ``None``.

    Returns:
        The rotation in degrees, or ``None`` when the bearing is unreadable
        — in which case the caller must not draw an arrow at all.

    """
    return _as_bearing(value)


@register.filter
def compass_point(value: Any) -> str | None:
    """
    Return the eight-point compass abbreviation the wind blows FROM.

    Rounds to the nearest 45° sector, wrapping 337.5–360 back onto ``N``.

    Usage::

        {{ hour.wind_direction_10m|compass_point }}

    Args:
        value: The raw ``wind_direction_10m`` entry, or ``None``.

    Returns:
        A localised abbreviation such as ``"NW"``, or ``None`` when the
        bearing is unreadable.

    """
    bearing = _as_bearing(value)
    if bearing is None:
        return None
    return str(_COMPASS_POINTS[round(bearing / 45) % 8])
