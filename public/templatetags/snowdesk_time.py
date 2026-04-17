"""
public/templatetags/snowdesk_time.py — Template filters for timestamp handling.

Provides the ``parse_iso`` filter which converts an ISO 8601 string (as stored
in ``render_model.metadata``) into a Python ``datetime`` object that Django's
built-in ``date`` and ``time`` filters can format.

SLF timestamps arrive as strings such as ``"2026-01-15T16:00:00+00:00"`` or
``"2026-01-15T16:00:00Z"``.  Rendering them in templates is a two-step
process:

    {{ rm.metadata.publication_time|parse_iso|date:"j M H:i" }}

The filter returns ``None`` on any parse failure so that downstream filters
(or ``|default:"—"``) can handle missing values gracefully.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from django import template
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

register = template.Library()


@register.filter
def parse_iso(value: str | None) -> datetime | None:
    """
    Parse an ISO 8601 / RFC 3339 timestamp string into a ``datetime``.

    Accepts strings with a trailing ``Z`` (UTC) or explicit ``+HH:MM``
    offset.  Strings without a timezone offset are assumed to be UTC.
    Returns ``None`` for ``None`` input, empty strings, or any value that
    cannot be parsed — so downstream ``|date`` / ``|default`` filters
    degrade gracefully.

    Usage::

        {{ render_model.metadata.publication_time|parse_iso|date:"j M H:i" }}
        {{ render_model.metadata.next_update|parse_iso|date:"j M H:i"|default:"—" }}

    Args:
        value: An ISO 8601 timestamp string, or ``None``.

    Returns:
        An aware ``datetime`` (UTC), or ``None`` on failure.

    """
    if not value:
        return None
    try:
        normalised = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        logger.debug("parse_iso: could not parse %r", value)
        return None


# Mapping from danger level integer (as stored in render_model.traits[].danger_level)
# to the CSS data-level key used by danger-band and the display label.
_DANGER_LEVEL_TO_KEY: dict[int, str] = {
    1: "low",
    2: "moderate",
    3: "considerable",
    4: "high",
    5: "very_high",
}

_DANGER_LEVEL_TO_LABEL: dict[int, Any] = {
    1: _("Low"),
    2: _("Moderate"),
    3: _("Considerable"),
    4: _("High"),
    5: _("Very High"),
}


@register.filter
def danger_level_key(level: int | None) -> str:
    """
    Convert an integer danger level (1–5) to the CSS data-level key string.

    Usage::

        {{ trait.danger_level|danger_level_key }}  {# → "considerable" #}

    Args:
        level: Integer danger level from render_model.traits[].danger_level.

    Returns:
        CSS key string (e.g. ``"considerable"``), or empty string on failure.

    """
    if level is None:
        return ""
    try:
        return _DANGER_LEVEL_TO_KEY.get(int(level), "")
    except (ValueError, TypeError):
        return ""


@register.filter
def danger_level_label(level: int | None) -> str:
    """
    Convert an integer danger level (1–5) to the human-readable label.

    Usage::

        {{ trait.danger_level|danger_level_label }}  {# → "Considerable" #}

    Args:
        level: Integer danger level from render_model.traits[].danger_level.

    Returns:
        Human-readable label string (e.g. ``"Considerable"``), or empty string.

    """
    if level is None:
        return ""
    try:
        return str(_DANGER_LEVEL_TO_LABEL.get(int(level), ""))
    except (ValueError, TypeError):
        return ""
