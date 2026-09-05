"""
apps/public/templatetags/weather_icons.py — Resolve a weather icon's URL.

Which directory an icon comes from is decided per request rather than baked
into the templates, so ``?icons=<name>`` can pin a set for a session in
DEBUG and ``/_icon-sets/`` can draw every set side by side.

This is a tag rather than a context variable because every weather partial is
included with ``only``, which strips the parent context — a context
processor's value simply does not arrive. See
``apps.weather.icon_sets`` for the ContextVar the middleware sets.
"""

from __future__ import annotations

from django import template
from django.templatetags.static import static

from apps.weather.icon_sets import (
    ICON_SETS,
    active_icon_dir,
    active_set_needs_halo,
)

register = template.Library()


@register.simple_tag
def weather_icon_url(filename: str) -> str:
    """Return the static URL for one condition icon in the active set.

    Args:
        filename: A bare basename from ``weather_icon_filename``, e.g.
            ``"light_snow.svg"``.

    Returns:
        The full static URL, e.g. ``"/static/icons/weather/yr/light_snow.svg"``.

    """
    return static(active_icon_dir() + filename)


@register.simple_tag
def weather_icon_class() -> str:
    """Return the icon's CSS class list for the active set.

    ``.weather-icon`` paints a dark edge around the silhouette, and it does
    so with a blur — so a set whose artwork already carries an edge gets
    nothing from it but softened marks. Emitted only for the sets that need
    it.

    Returns:
        ``"weather-icon"`` or the empty string.

    """
    return "weather-icon" if active_set_needs_halo() else ""


@register.simple_tag
def weather_icon_needs_halo() -> str:
    """Return ``"true"``/``"false"`` for the map element's data attribute.

    A string rather than a bool because ``{% tag %}`` output cannot be piped
    through ``yesno``, and ``map.js`` compares the attribute to ``'true'``
    the same way it does for every other boolean it is handed.

    Returns:
        ``"true"`` when the active set needs the canvas silhouette edge.

    """
    return "true" if active_set_needs_halo() else "false"


@register.filter
def for_active_icon_set(path: str) -> str:
    """Re-point a component-library icon path at the active set.

    ``design_tokens`` resolves its weather-icon paths once at import, against
    the configured set — right for a page documenting the design system, and
    wrong for the one page that shows every icon at once, which is where the
    candidate sets are easiest to compare. This swaps the set directory for
    whichever set the request has pinned.

    Paths outside a set directory — ``sunrise.svg`` and ``sunset.svg``, which
    every set shares — are returned untouched.

    Args:
        path: A static-relative path from an ``IconToken``.

    Returns:
        The path, re-pointed if it names a set directory.

    """
    for rel in ICON_SETS.values():
        if path.startswith(rel):
            return active_icon_dir() + path[len(rel) :]
    return path
