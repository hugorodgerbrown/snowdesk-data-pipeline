"""
apps/public/templatetags/map_flags.py — the map's flags, asked from a template.

One tag, ``{% map_3d_view_enabled as wears_specs %}``, which answers
``map_shared.map_3d_view_enabled`` for the request being rendered. The nav
reads it to put the red/cyan specs on the wordmark (SNOW-1451), so the nav,
the home page's 3D card, ``/map/`` and ``/offline/`` share one rule rather
than the nav nesting its own ``{% flag %}`` blocks.

A tag and not a context value, because every view that renders the nav
would otherwise have to pass it. A signed-out request with no route share
3D grant (SNOW-1469) pays no query for it, and a granted one is answered
once per request.
"""

from __future__ import annotations

import logging
from typing import Any

from django import template

from apps.public.map_shared import map_3d_view_enabled as _map_3d_view_enabled

logger = logging.getLogger(__name__)

register = template.Library()


@register.simple_tag(takes_context=True)
def map_3d_view_enabled(context: Any) -> bool:
    """Whether the request being rendered gets the map's 3D view.

    Args:
        context: The template context; its ``request`` is asked.

    Returns:
        False when the context carries no request (an email, a bare
        ``render_to_string``), otherwise ``map_shared.map_3d_view_enabled``.

    """
    request = context.get("request")
    if request is None:
        return False
    return _map_3d_view_enabled(request)
