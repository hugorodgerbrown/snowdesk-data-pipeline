"""
public/templatetags/card_tags.py — Template filters for bulletin cards.

Provides the ``aspect_rose`` filter which renders an inline SVG compass
rose highlighting active avalanche aspect directions. Each of the eight
cardinal/intercardinal segments is drawn as a wedge; active aspects are
filled with a warm amber, inactive ones with a muted grey.

The SVG is generated server-side as a string — no static assets required.
"""

import math

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# Angle of each segment's centre, in degrees.  SVG's coordinate system
# has 0° pointing right; we rotate so that north (top of the circle) is
# at -90°.
_ASPECT_ANGLES: dict[str, int] = {
    "N": -90,
    "NE": -45,
    "E": 0,
    "SE": 45,
    "S": 90,
    "SW": 135,
    "W": 180,
    "NW": 225,
}

_HALF = math.pi / 8  # half a segment width (22.5°)

_FILL_ACTIVE = "#BA7517"
_FILL_INACTIVE = "#E8E6E0"
_FILL_CENTRE = "#FFFFFF"


def _wedge(cx: float, cy: float, r: float, centre_deg: int, active: bool) -> str:
    """
    Return an SVG ``<path>`` string for one compass wedge.

    Args:
        cx: Centre x-coordinate.
        cy: Centre y-coordinate.
        r: Outer radius of the wedge.
        centre_deg: Angle of the wedge centre in degrees (SVG coords).
        active: Whether this aspect is active (highlighted).

    Returns:
        An SVG path element as a string.

    """
    centre_rad = math.radians(centre_deg)
    start_rad = centre_rad - _HALF
    end_rad = centre_rad + _HALF

    x1 = cx + r * math.cos(start_rad)
    y1 = cy + r * math.sin(start_rad)
    x2 = cx + r * math.cos(end_rad)
    y2 = cy + r * math.sin(end_rad)

    fill = _FILL_ACTIVE if active else _FILL_INACTIVE

    return (
        f'<path d="M{cx},{cy} L{x1:.2f},{y1:.2f} '
        f'A{r},{r} 0 0,1 {x2:.2f},{y2:.2f} Z" '
        f'fill="{fill}" '
        f'stroke="#FFFFFF" stroke-width="1.5"/>'
    )


@register.filter
def aspect_rose(aspects: list[str] | None, size: int = 36) -> str:
    """
    Render an inline SVG compass rose for a list of active aspect strings.

    Usage::

        {{ p.aspects|aspect_rose|safe }}
        {{ p.aspects|aspect_rose:32|safe }}

    Args:
        aspects: List of compass direction strings (e.g. ``["N", "NE"]``).
        size: Width and height of the SVG in pixels (default 36).

    Returns:
        An HTML-safe SVG string.

    """
    active = set(aspects or [])
    cx = cy = size / 2
    r = size / 2 - 2  # 2px inset for stroke clearance

    wedges = "".join(
        _wedge(cx, cy, r, angle, aspect in active)
        for aspect, angle in _ASPECT_ANGLES.items()
    )

    centre_dot = (
        f'<circle cx="{cx}" cy="{cy}" r="{r * 0.18:.1f}" fill="{_FILL_CENTRE}"/>'
    )

    return mark_safe(  # noqa: S308 — output is fully constructed from numeric values
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" '
        f'aria-label="Aspects: {", ".join(sorted(active)) or "none"}" '
        f'role="img">'
        f"{wedges}{centre_dot}"
        f"</svg>"
    )
