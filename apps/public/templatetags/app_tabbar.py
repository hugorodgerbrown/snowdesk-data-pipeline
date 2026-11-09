"""
apps/public/templatetags/app_tabbar.py — which tab the phone app's bar lights.

SNOW-1476. ``{% app_tabbar_state as tabbar %}`` answers, for the request
being rendered, the two facts ``includes/_app_tabbar.html`` needs:

- ``active`` — the tab lit on page load. "help" on the pages the Help
  panel links to (Help and the three legal pages), "" (none) on an account
  page, which the header's avatar menu leads to, and "map" everywhere
  else, the map and the bulletin, weather, resort and trip pages included.
  On the map the bar's script then follows whichever tab panel is open.
- ``on_map`` — whether this is the map page. There the tabs are buttons
  that open panels in place; elsewhere they are links to
  ``/map/?panel=…``.

The bar has three tabs since SNOW-1479 (Map, Saved, Help); none of them is
lit by this tag but Map and Help, so the Today tab's removal changed
nothing here.

A tag and not a context processor, because the bar is the only reader and
a processor would run for every template render, emails included.
"""

from __future__ import annotations

import logging
from typing import Any

from django import template

logger = logging.getLogger(__name__)

register = template.Library()

# The public URL names whose pages the Help tab's "?" menu links to.
HELP_URL_NAMES: frozenset[str] = frozenset(
    {"help", "privacy", "terms_of_service", "colophon"}
)

# The path prefix of every account page (config/urls.py mounts
# apps.accounts.urls there).
ACCOUNT_PATH_PREFIX = "/account/"


@register.simple_tag(takes_context=True)
def app_tabbar_state(context: Any) -> dict[str, Any]:
    """Say which tab is lit and whether the page is the map.

    Args:
        context: The template context; its ``request`` is read.

    Returns:
        A dict with ``active`` ("map", "help" or "") and ``on_map`` (bool).
        Without a request (a bare ``render_to_string``, the component
        library's fixtures) the answer is Map, off the map.

    """
    request = context.get("request")
    if request is None:
        return {"active": "map", "on_map": False}
    match = getattr(request, "resolver_match", None)
    namespace = getattr(match, "namespace", "") or ""
    url_name = getattr(match, "url_name", "") or ""
    on_map = namespace == "public" and url_name == "map"
    path = getattr(request, "path", "") or ""
    if namespace == "public" and url_name in HELP_URL_NAMES:
        active = "help"
    elif path.startswith(ACCOUNT_PATH_PREFIX):
        active = ""
    else:
        active = "map"
    return {"active": active, "on_map": on_map}
