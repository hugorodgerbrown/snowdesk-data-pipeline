"""
public/templatetags/og_tags.py — Template filters for OpenGraph metadata.

Provides two filters used by ``templates/includes/_page_meta.html``:

``og_locale`` converts a Django ``LANGUAGE_CODE`` string (BCP-47 / RFC
5646 format with hyphens) into the OpenGraph locale format required by
the ``og:locale`` meta property (underscore-separated, e.g. ``"en_GB"``).

``squish`` collapses all whitespace runs in a value to single spaces and
strips the ends, so a multi-line template block body can't leak newlines
and indentation into a ``content="…"`` attribute (SNOW-553).

OpenGraph spec expects locales in the form ``language_TERRITORY`` (ISO 639-1
two-letter language code + ISO 3166-1 alpha-2 territory code joined by an
underscore).  Django's ``LANGUAGE_CODE`` uses hyphens (``"en-gb"``), so this
filter normalises the separator and title-cases the territory portion.

Usage::

    {% load og_tags %}
    <meta property="og:locale" content="{{ LANGUAGE_CODE|og_locale }}">

    {# "en-gb" → "en_GB" #}
    {# "de-ch" → "de_CH" #}
    {# "fr"    → "fr"    #}
    {# ""      → ""      #}
"""

import logging

from django import template

logger = logging.getLogger(__name__)

register = template.Library()


@register.filter
def og_locale(value: str | None) -> str:
    """
    Convert a Django language code to an OpenGraph locale string.

    Django's ``LANGUAGE_CODE`` uses BCP-47 hyphen-separated tags (e.g.
    ``"en-gb"``).  OpenGraph expects an underscore-separated tag with the
    territory portion in upper case (e.g. ``"en_GB"``).  Single-segment
    codes (e.g. ``"fr"``) are returned unchanged.

    Returns an empty string for ``None`` or empty input so downstream
    ``|default`` filters can handle missing values gracefully.

    Args:
        value: A Django language code string (e.g. ``"en-gb"``), or ``None``.

    Returns:
        An OpenGraph locale string (e.g. ``"en_GB"``), or ``""`` on failure.

    """
    if not value:
        return ""
    try:
        parts = str(value).split("-")
        if len(parts) == 1:
            return parts[0]
        language = parts[0]
        territory = parts[1].upper()
        return f"{language}_{territory}"
    except AttributeError, IndexError:
        logger.debug("og_locale: could not convert %r", value)
        return ""


@register.filter
def squish(value: str | None) -> str:
    """
    Collapse every run of whitespace in ``value`` to a single space.

    Meta ``content`` attributes are single-line values, but the copy that
    fills them is written as template block bodies — and ``djangofmt``
    reflows a block body onto several indented lines as soon as it is
    long enough. The newlines and indentation then reach the page
    verbatim: the bulletin description rendered as a literal newline,
    four spaces, "Avalanche bulletin for", another newline, eight spaces,
    "Martigny-Verbier," and so on. That is what Google put in the SERP
    snippet, and what every platform falling back to the description meta
    showed on its share card (SNOW-553).

    Applied at the emitter rather than per page, so no future template
    can reintroduce the defect by being reformatted.

    Returns an empty string for ``None`` or empty input, matching
    ``og_locale``'s None-safe shape.

    Args:
        value: The raw attribute value, possibly spanning several lines.

    Returns:
        The value with internal whitespace collapsed and the ends stripped.

    """
    if not value:
        return ""
    return " ".join(str(value).split())
