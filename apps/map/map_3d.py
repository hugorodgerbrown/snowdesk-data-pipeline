"""
apps/map/map_3d.py — the retired /map/3d/ address, kept as a redirect (SNOW-1450).

/map/3d/ was the 3D terrain map's sandbox (SNOW-1438), a page of its own
beside /map/. SNOW-1449 brought the 3D view onto /map/ itself (``?view=3d``,
behind ``map_3d_view_enabled``), and SNOW-1450 retired the page. Links to it
are still out there, in chats and bookmarks, so the address answers a 302:

* ``?route_share=<token>`` goes to the route share's own 3D follow,
  ``/routes/s/<token>/3d/`` (``routes:share_redirect_3d``), which keeps its
  404/410/429 checks, its budget and its seating, the session's 3D grant
  included (SNOW-1469), so an old route link still opens 3D signed out;
* anything else goes to ``/map/?…&view=3d``, carrying ``probe`` and ``at``.

The old page's ``share`` token and its ``layers`` switches are dropped: the
token's model is gone, and /map/ has no URL for its switches (its own
``?layers=`` means the layer explainer). Outside the ``map_3d`` flag /map/
ignores ``view=3d`` and opens flat
(docs/decisions/a-3d-map-is-maplibre-terrain.md).
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

# The old page's parameters /map/ still reads, in the order they are
# carried. ``view=3d`` always follows them.
_CARRIED_PARAMS = ("probe", "at")


def _route_share_follow(token: str) -> str:
    """Return the 3D follow of the route share ``token`` names, or "".

    Whether the share is live is the follow's question, not this one's.

    Args:
        token: The ``?route_share=`` value from an old link.

    Returns:
        ``/routes/s/<token>/3d/``, or "" for a blank token or one no share
        URL could carry (a ``/`` in it).

    """
    if not token:
        return ""
    try:
        return reverse("routes:share_redirect_3d", args=[token])
    except NoReverseMatch:
        return ""


@require_http_methods(["GET", "HEAD"])
def map_3d_redirect(request: HttpRequest) -> HttpResponse:
    """Send an old /map/3d/ link to where its view now lives.

    The same answer for anyone, flagged or not, signed in or out, and it
    writes nothing to the session: a route share's token is seated by the
    follow this sends it to, not here. ``Cache-Control: no-store`` and a
    302, never a 301, so a browser that cached the answer cannot skip the
    route share follow's checks the next time.

    Args:
        request: The incoming GET or HEAD request.

    Returns:
        A 302 to ``/routes/s/<token>/3d/`` for a route share, otherwise to
        ``/map/`` with ``probe`` and ``at`` carried and ``view=3d`` last.
        A route share no URL could carry is treated as absent.

    """
    destination = _route_share_follow(request.GET.get("route_share", ""))
    if destination:
        query = {"at": request.GET["at"]} if "at" in request.GET else {}
    else:
        destination = reverse("public:map")
        query = {key: request.GET[key] for key in _CARRIED_PARAMS if key in request.GET}
        query["view"] = "3d"
    if query:
        destination = f"{destination}?{urlencode(query, safe=',')}"
    response = HttpResponseRedirect(destination)
    response["Cache-Control"] = "no-store"
    return response
