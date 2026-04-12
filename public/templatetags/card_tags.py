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

from pipeline.schema import Elevation

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


@register.filter
def elevation_icon(elevation: Elevation, size: int = 24) -> str:
    """
    Render an inline SVG elevation indicator icon.

    Uses elevation.bound_type (LOWER | UPPER | BOTH) to select variant.

    Usage: {{ p.elevation|elevation_icon|safe }}
    Custom size: {{ p.elevation|elevation_icon:32|safe }}
    """
    if not elevation:
        return ""

    bound_type = getattr(elevation, "bound_type", None)
    if not bound_type:
        return ""

    s = size
    cx = s / 2  # mountain centre x
    base_y = s * 0.88  # base of mountain
    peak_y = s * 0.08  # peak of mountain
    hw = s * 0.42  # half-width of mountain base

    # Mountain vertices
    px = cx
    py = peak_y
    blx = cx - hw
    brx = cx + hw
    by = base_y

    mountain = (
        f'<polygon points="{px},{py:.2f} {blx:.2f},{by:.2f} {brx:.2f},{by:.2f}" '
        f'fill="none" stroke="#2C2C2A" stroke-width="1.5" stroke-linejoin="round"/>'
    )

    # Clip path so shading stays inside mountain triangle
    clip_id = f"emtn-{bound_type.lower()}-{size}"
    clip = (
        f'<clipPath id="{clip_id}">'
        f'<polygon points="{px},{py:.2f} {blx:.2f},{by:.2f} {brx:.2f},{by:.2f}"/>'
        f"</clipPath>"
    )

    shade_opacity = "0.18"
    stroke = "#2C2C2A"
    dash = 'stroke-dasharray="2,2"'

    if bound_type == "LOWER":
        # Shaded zone: above the line to peak
        line_y = s * 0.62
        shade = (
            f'<rect x="{blx:.2f}" y="{py:.2f}" '
            f'width="{hw * 2:.2f}" height="{line_y - py:.2f}" '
            f'fill="{stroke}" opacity="{shade_opacity}" clip-path="url(#{clip_id})"/>'
        )
        lines = (
            f'<line x1="{blx:.2f}" y1="{line_y:.2f}" '
            f'x2="{brx:.2f}" y2="{line_y:.2f}" '
            f'stroke="{stroke}" stroke-width="1" {dash}/>'
        )
        aria = "Elevation: above lower bound"

    elif bound_type == "UPPER":
        # Shaded zone: below the line to base
        line_y = s * 0.46
        shade = (
            f'<rect x="{blx:.2f}" y="{line_y:.2f}" '
            f'width="{hw * 2:.2f}" height="{by - line_y:.2f}" '
            f'fill="{stroke}" opacity="{shade_opacity}" clip-path="url(#{clip_id})"/>'
        )
        lines = (
            f'<line x1="{blx:.2f}" y1="{line_y:.2f}" '
            f'x2="{brx:.2f}" y2="{line_y:.2f}" '
            f'stroke="{stroke}" stroke-width="1" {dash}/>'
        )
        aria = "Elevation: below upper bound"

    else:  # BOTH
        # Shaded band between two lines
        upper_y = s * 0.44
        lower_y = s * 0.64
        shade = (
            f'<rect x="{blx:.2f}" y="{upper_y:.2f}" '
            f'width="{hw * 2:.2f}" height="{lower_y - upper_y:.2f}" '
            f'fill="{stroke}" opacity="{shade_opacity}" clip-path="url(#{clip_id})"/>'
        )
        lines = (
            f'<line x1="{blx:.2f}" y1="{upper_y:.2f}" '
            f'x2="{brx:.2f}" y2="{upper_y:.2f}" '
            f'stroke="{stroke}" stroke-width="1" {dash}/>'
            f'<line x1="{blx:.2f}" y1="{lower_y:.2f}" '
            f'x2="{brx:.2f}" y2="{lower_y:.2f}" '
            f'stroke="{stroke}" stroke-width="1" {dash}/>'
        )
        aria = "Elevation: between bounds"

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{s}" height="{s}" viewBox="0 0 {s} {s}" '
        f'aria-label="{aria}" role="img">'
        f"<defs>{clip}</defs>"
        f"{shade}{mountain}{lines}"
        f"</svg>"
    )
