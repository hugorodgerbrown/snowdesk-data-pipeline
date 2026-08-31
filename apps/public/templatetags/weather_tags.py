"""
apps/public/templatetags/weather_tags.py — Template filters for wind direction.

Turns the raw bearing Open-Meteo returns — ``wind_direction_10m`` on an
:class:`apps.weather.types.HourlyRow`, in degrees — into the two values the
hourly forecast row needs to draw it: a rotation for the arrow glyph and a
compass point for the text label.

**The bearing is the direction the wind blows FROM.** That is the
meteorological convention Open-Meteo follows, and it is the one that matters
here: a wind *from* the west loads east-facing slopes, so the aspect under
suspicion is the opposite of the number. Both filters below are written
around that asymmetry, and they resolve it in opposite directions on
purpose:

``wind_arrow_rotation``
    Rotates the glyph to point **downwind** — the way the air is travelling,
    which is the convention every mainstream forecast UI draws. A bearing of
    270 (from the west) yields 90, pointing the arrow east.

``compass_point``
    Names the **source** — 270 is ``W``, not ``E``. This is what goes in the
    visible text and the accessible label, so the reading a screen reader
    announces is "from W" and never depends on interpreting a glyph's
    rotation.

The two disagreeing is the point, not a bug: the arrow shows travel, the
label names origin, and the word "from" in the template ties them together.
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
    Return the CSS rotation, in degrees, for a downwind arrow glyph.

    Assumes a base glyph pointing **up** (north) at zero rotation, and adds
    180 so the arrow flies with the wind rather than into it — see this
    module's docstring for why that differs from :func:`compass_point`.

    Usage::

        {% with rot=hour.wind_direction_10m|wind_arrow_rotation %}
        <span style="transform: rotate({{ rot }}deg)">

    Args:
        value: The raw ``wind_direction_10m`` entry, or ``None``.

    Returns:
        The rotation in degrees, or ``None`` when the bearing is unreadable
        — in which case the caller must not draw an arrow at all.

    """
    bearing = _as_bearing(value)
    if bearing is None:
        return None
    return (bearing + 180) % 360


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
