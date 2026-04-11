"""
public/templatetags/hazard_icons.py — Template filter for hazard icons.

Maps CAAML avalanche problem types to their EAWS SVG icon paths under
``static/icons/svg/``.
"""

from os import path

from django import template

register = template.Library()

# Mapping from CAAML problemType values to SVG filenames in static/icons/svg/.
_ICON_MAP: dict[str, str] = {
    "gliding_snow": "Gliding-Snow.svg",
    "new_snow": "New-Snow.svg",
    "persistent_weak_layers": "Persistent-Weak-Layer.svg",
    "wet_snow": "Wet-Snow.svg",
    "wind_slab": "Wind-Slab.svg",
    "no_distinct_avalanche_problem": "No-Distinct-Avalanche-Problem.svg",
    "cornices": "Cornices.svg",
}


@register.filter
def hazard_icon(problem_type: str) -> str:
    """
    Return the static-relative path to the SVG icon for a hazard problem type.

    Usage::

        {{ hazard.problem_type|hazard_icon }}

    Args:
        problem_type: A CAAML problemType value (e.g. ``"wind_slab"``).

    Returns:
        The icon path relative to STATIC_URL, or empty string if unknown.

    """
    return path.join("icons/eaws/avalanche_problems/", _ICON_MAP.get(problem_type, ""))
