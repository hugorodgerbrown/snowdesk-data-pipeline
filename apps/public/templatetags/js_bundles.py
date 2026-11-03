"""
apps/public/templatetags/js_bundles.py — one script tag per bundle (SNOW-1412).

The map page's classic scripts are listed, in execution order, in
``static/js/bundles/<name>.txt``: one filename per line, ``#`` lines are
notes. ``bin/bundle-js`` concatenates each list into
``static/bundles/<name>.bundle.js`` at build time.

Usage:

    {% load js_bundles %}
    {% js_bundle "map" %}

With ``settings.JS_BUNDLES`` off (development, tests) the tag renders one
``<script defer>`` per member, exactly as the page did before bundling, so
each file stays debuggable on its own. With it on (staging, production,
perf) it renders one ``<script defer>`` for the built bundle. Deferred
scripts run in document order, so either form runs the same code in the
same order. See docs/decisions/the-map-page-ships-one-script-bundle.md.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path

from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

logger = logging.getLogger(__name__)

register = template.Library()

# Where the membership lists live, relative to BASE_DIR.
BUNDLE_LISTS_DIR = Path("static") / "js" / "bundles"


def parse_bundle_list(text: str) -> tuple[str, ...]:
    """
    Return the filenames a bundle list names, in order.

    The format ``bin/bundle-js`` reads too: one filename per line, blank
    lines ignored, a line starting ``#`` a note.

    Args:
        text: The list file's contents.

    Returns:
        Filenames relative to ``static/js/``.

    """
    return tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


@cache
def bundle_members(name: str) -> tuple[str, ...]:
    """
    Return the member filenames of bundle ``name``, in execution order.

    Read once per process: the lists change only with a deploy.

    Args:
        name: The bundle's name, the stem of its list file.

    Returns:
        Filenames relative to ``static/js/``.

    Raises:
        template.TemplateSyntaxError: No list exists for ``name``, or it
            names nothing. A typo in a template should fail the page loudly
            rather than ship it without its scripts.

    """
    path = Path(settings.BASE_DIR) / BUNDLE_LISTS_DIR / f"{name}.txt"
    try:
        members = parse_bundle_list(path.read_text())
    except FileNotFoundError as exc:
        raise template.TemplateSyntaxError(f"No JS bundle named {name!r}") from exc
    if not members:
        raise template.TemplateSyntaxError(f"{path} names no scripts")
    return members


@register.simple_tag
def js_bundle(name: str) -> SafeString:
    """
    Render bundle ``name`` as its built file or as its separate members.

    Args:
        name: The bundle's name, e.g. ``"map"``.

    Returns:
        Escaped ``<script defer>`` markup.

    """
    if getattr(settings, "JS_BUNDLES", False):
        return format_html(
            '<script defer src="{}"></script>', static(f"bundles/{name}.bundle.js")
        )
    return format_html_join(
        "\n",
        '<script defer src="{}"></script>',
        ((static(f"js/{member}"),) for member in bundle_members(name)),
    )
