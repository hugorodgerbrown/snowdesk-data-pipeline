"""
public/templatetags/hazard_icons.py — Template filters for hazard icons.

Maps CAAML avalanche problem types to their EAWS SVG icon paths under
``static/icons/eaws/avalanche_problems/``, and exposes a
``category_danger_icon`` filter that resolves the per-category (dry/wet)
danger-level pictogram under ``static/icons/eaws/danger_levels/``.
"""

from os import path

from django import template

register = template.Library()

# Mapping from CAAML problemType values to filenames in
# static/icons/eaws/avalanche_problems/.
_ICON_MAP: dict[str, str] = {
    "gliding_snow": "Gliding-Snow.svg",
    "new_snow": "New-Snow.svg",
    "persistent_weak_layers": "Persistent-Weak-Layer.svg",
    "wet_snow": "Wet-Snow.svg",
    "wind_slab": "Wind-Slab.svg",
    "no_distinct_avalanche_problem": "No-Distinct-Avalanche-Problem.svg",
    "cornices": "Cornices.svg",
}

# Per-category danger-level pictograms.  SLF ships a single Dry-Snow-4-5 file
# for both high levels; wet snow has a distinct file per level 1–5.
_DANGER_LEVEL_DIR = "icons/eaws/danger_levels/"


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
    filename = _ICON_MAP.get(problem_type, "")
    if not filename:
        return ""
    return path.join("icons/eaws/avalanche_problems/", filename)


@register.filter
def category_danger_icon(trait: dict | None) -> str:
    """
    Return the static-relative path to the EAWS danger-level icon for a trait.

    Resolves the per-category pictogram — ``Dry-Snow-{n}.svg`` (or
    ``Dry-Snow-4-5.svg`` for levels 4 and 5) or ``Wet-Snow-{n}.svg`` —
    based on the trait's ``category`` and ``danger_level`` fields.

    Usage::

        {{ trait|category_danger_icon }}

    Args:
        trait: A trait dict from the render model (must have ``category``
            and ``danger_level`` keys) or ``None``.

    Returns:
        The icon path relative to STATIC_URL, or empty string on missing /
        unknown inputs.

    """
    if not trait or not isinstance(trait, dict):
        return ""
    category = trait.get("category")
    level = trait.get("danger_level")
    if category not in {"dry", "wet"} or not isinstance(level, int):
        return ""
    if level < 1 or level > 5:
        return ""
    if category == "dry":
        filename = "Dry-Snow-4-5.svg" if level >= 4 else f"Dry-Snow-{level}.svg"
    else:
        filename = f"Wet-Snow-{level}.svg"
    return path.join(_DANGER_LEVEL_DIR, filename)
