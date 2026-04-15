"""
public/templatetags/snowdesk_html.py — Template filter for safe HTML rendering.

Provides the ``snowdesk_html`` filter which sanitises SLF prose HTML strings
before they are rendered in templates.  SLF prose fields (snowpackStructure,
weatherReview, weatherForecast, tendency[].comment) arrive as raw HTML from
the API and are stored untouched in the render model.  Sanitisation is a
render-time concern so the allowlist can be changed without triggering a
render-model rebuild.

The filter runs ``bleach.clean`` with a strict allowlist of structural tags
only (``h1``, ``h2``, ``p``, ``ul``, ``li``, ``strong``, ``em``).  All
attributes and protocols are removed.  Disallowed tags are *stripped* (not
escaped) so that unknown or dangerous tags disappear silently from the output.
"""

import logging

import bleach
from django import template
from django.utils.safestring import SafeString, mark_safe

logger = logging.getLogger(__name__)

register = template.Library()

# Tags that SLF prose is known to contain and that are safe to render.
# This list is intentionally conservative — add tags here only when SLF
# actually ships them and the template needs to render them.
_ALLOWED_TAGS: list[str] = ["h1", "h2", "p", "ul", "li", "strong", "em"]

# No attributes are expected or required in SLF prose.
_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {}

# No link protocols are expected in SLF prose.
_ALLOWED_PROTOCOLS: list[str] = []


@register.filter
def snowdesk_html(value: str | None) -> SafeString:
    """
    Sanitise an SLF HTML prose string and return a ``SafeString``.

    Runs ``bleach.clean`` with a strict tag allowlist.  Disallowed tags are
    stripped (not escaped) so that unexpected markup vanishes rather than
    becoming visible text.  Returns an empty ``SafeString`` when given
    ``None`` or an empty string.

    Usage::

        {{ bulletin.prose.snowpack_structure|snowdesk_html }}

    Args:
        value: Raw HTML string from the SLF render model, or ``None``.

    Returns:
        A ``SafeString`` containing only the allowed structural tags,
        safe to render with Django's default auto-escaping.

    """
    if not value:
        return mark_safe("")  # noqa: S308 — empty string carries no XSS risk

    cleaned = bleach.clean(
        value,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
    return mark_safe(cleaned)  # noqa: S308 — content has been sanitised by bleach above
