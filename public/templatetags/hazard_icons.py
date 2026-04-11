"""
public/templatetags/hazard_icons.py — Template filter for hazard icons.

Maps CAAML avalanche problem types to their EAWS SVG icon paths under
``static/icons/svg/``.
"""

from django import template

register = template.Library()

# Mapping from CAAML problemType values to SVG filenames in static/icons/svg/.
_ICON_MAP: dict[str, str] = {
    "gliding_snow": "icons/svg/Icon-Avalanche-Problem-Gliding-Snow-Grey-EAWS.svg",
    "new_snow": "icons/svg/Icon-Avalanche-Problem-New-Snow-Grey-EAWS.svg",
    "persistent_weak_layers": (
        "icons/svg/Icon-Avalanche-Problem-Persistent-Weak-Layer-Grey-EAWS.svg"
    ),
    "wet_snow": "icons/svg/Icon-Avalanche-Problem-Wet-Snow-Grey-EAWS.svg",
    "wind_slab": "icons/svg/Icon-Avalanche-Problem-Wind-Slab-Grey-EAWS.svg",
    "no_distinct_avalanche_problem": (
        "icons/svg/Icon-Avalanche-Problem-No-Distinct-Avalanche-Problem-EAWS.svg"
    ),
    "cornices": "icons/svg/Icon-Avalanche-Problem-Cornices.svg",
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
    return _ICON_MAP.get(problem_type, "")
