"""
public/templatetags/snowdesk_html.py — Template filters for SLF prose.

``snowdesk_html`` sanitises SLF prose HTML strings before they are rendered in
templates.  SLF prose fields (snowpackStructure, weatherReview, weatherForecast,
tendency[].comment) arrive as raw HTML from the API and are stored untouched in
the render model.  Sanitisation is a render-time concern so the allowlist can
be changed without triggering a render-model rebuild.

``prose_title`` and ``prose_body`` pair up to hoist the leading ``<h1>`` out of
an SLF prose block — SLF always starts every prose string with a context-rich
``<h1>`` (e.g. ``"Weather review for Thursday"``) which makes a better panel
summary than a static label.  ``prose_title`` returns the stripped title text
(falling back to a caller-supplied default); ``prose_body`` returns the prose
HTML with the leading ``<h1>`` removed so the body doesn't duplicate it.

``snowdesk_html`` runs ``bleach.clean`` with a strict allowlist of structural
tags only (``h1``, ``h2``, ``p``, ``ul``, ``li``, ``strong``, ``em``).  All
attributes and protocols are removed.  Disallowed tags are *stripped* (not
escaped) so that unknown or dangerous tags disappear silently from the output.
"""

import logging
import re

import bleach
from django import template
from django.utils.safestring import SafeString, mark_safe

logger = logging.getLogger(__name__)

register = template.Library()

# Matches the leading <h1>…</h1> of an SLF prose block, allowing for leading
# whitespace and any attributes on the opening tag.  Non-greedy body match so
# we only consume the first heading.
_LEADING_H1_RE = re.compile(r"^\s*<h1\b[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)

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


@register.filter
def prose_title(value: str | None, fallback: str = "") -> str:
    """
    Extract the leading ``<h1>`` text from an SLF prose block.

    SLF prose always begins with a context-rich heading (e.g. ``"Weather
    review for Thursday"``) that is a better panel summary than a static
    label.  This filter returns that heading as plain text, stripped of any
    nested inline tags, or the ``fallback`` value when no leading ``<h1>``
    is present or the value is empty.

    The return type is a plain ``str`` so Django's auto-escaping applies
    when it lands in the template — the caller cannot end up rendering
    unexpected markup in a ``<summary>``.

    Usage::

        {{ prose.snowpack_structure|prose_title:"Snowpack" }}
    """
    if not value:
        return fallback
    match = _LEADING_H1_RE.match(value)
    if not match:
        return fallback
    # Strip any nested markup so the title is plain text only.
    plain = bleach.clean(match.group(1), tags=[], strip=True).strip()
    return plain or fallback


@register.filter
def prose_body(value: str | None) -> str:
    """
    Return an SLF prose block with the leading ``<h1>`` removed.

    Pairs with ``prose_title``: the h1 becomes the panel summary and this
    filter yields the remaining HTML for the body, avoiding a duplicated
    heading.  The returned string is still raw (unsanitised) HTML — pipe
    it through ``snowdesk_html`` when rendering.

    Usage::

        {{ prose.snowpack_structure|prose_body|snowdesk_html }}
    """
    if not value:
        return ""
    return _LEADING_H1_RE.sub("", value, count=1).lstrip()
