"""
apps/public/banners.py — the read side of the admin-managed site banners.

``django-persistent-messages`` owns the model, the admin and the dismissal
endpoint; this module owns everything about how a row reaches a page. It
answers three questions and nothing else:

  * which banners apply to this request (``banners_for_request``),
  * which status-palette token a Django message level paints in
    (``kind_for`` / ``KIND_FOR_LEVEL_TAG``), and
  * whether this reader's dismissal can be recorded (``dismiss_url_for``).

The package ships ``persistent_messages.shortcuts.get_persistent_messages``
for the first of those, and we deliberately do not use it: it is decorated
``functools.cache`` keyed on the ``HttpRequest``, which in a long-lived
worker retains every request object it has ever been handed. The query it
wraps is one line, so this module runs it directly and caches on the
request instead, where the lifetime is the one we actually want.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from persistent_messages.models import PersistentMessage

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

# Django's five message levels carry four palettes: ``debug`` has no status
# colour of its own and is not a distinct thing to say to a reader, so it
# paints as info. Keyed on ``level_tag`` rather than the integer level so a
# project-level ``MESSAGE_TAGS`` override lands here too.
#
# The values are design-system status keys — ``_overlay_banner.html`` builds
# ``bg-status-<kind>-bg`` / ``text-status-<kind>-text`` from them, and those
# four are what ``src/css/main.css``'s ``@source inline`` forces into the
# bundle. A fifth name here paints an unstyled strip.
KIND_FOR_LEVEL_TAG = {
    "debug": "info",
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "error",
}

# The palette a level we do not recognise falls back to. Deliberately the
# calmest of the four: an unknown level is a configuration slip, and it must
# not be able to dress a banner up as an error.
DEFAULT_KIND = "info"

# Where the per-request result is stashed. An attribute on the request, so
# the cache dies with the request — see the module docstring.
_CACHE_ATTR = "_snowdesk_persistent_banners"


def kind_for(message: Any) -> str:
    """
    Return the design-system status key a banner should paint in.

    Args:
        message: The banner row.

    Returns:
        One of "info", "success", "warning", "error".

    """
    return KIND_FOR_LEVEL_TAG.get(message.level_tag, DEFAULT_KIND)


def dismiss_url_for(message: Any, request: HttpRequest) -> str:
    """
    Return the URL that records this reader's dismissal, or "".

    Two conditions, both of which have to hold for the URL to be worth
    rendering. The row must be dismissable — the package's own
    ``dismiss_url()`` already returns "" otherwise — and the reader must be
    signed in, because the endpoint behind it is ``login_required`` and an
    anonymous DELETE would only earn a redirect to the login page.

    So the attribute's presence on the page means exactly "dismissing this
    can be recorded", which is the question static/js/persistent_messages.js
    needs answered and cannot answer for itself.

    Args:
        message: The banner row.
        request: The incoming request, for its user.

    Returns:
        The dismissal URL, or "" when this reader's dismissal cannot be
        recorded.

    """
    if not request.user.is_authenticated:
        return ""
    # ``cast`` rather than a bare return: the package ships no ``py.typed``,
    # so every one of its attributes reads as ``Any`` here and
    # ``warn_return_any`` rejects handing that back as a ``str``. The cast
    # states the contract the package's own annotation already gives
    # (``dismiss_url() -> str``) at the boundary where we adopt it. Same
    # reason for the one in ``_active_for_user`` below.
    return cast("str", message.dismiss_url())


def banners_for_request(request: HttpRequest) -> list[Any]:
    """
    Return the active banners targeted at this request's user.

    "Active" and "targeted" are both the package's own definitions:
    ``display_from`` has passed, ``display_until`` has not, the row's
    ``target`` matches the user (anonymous included), and an authenticated
    user has not already dismissed it. Ordered most severe first, then
    newest, so a maintenance warning outranks a marketing note.

    An anonymous visitor's dismissal is not recorded anywhere — the
    package's endpoint is ``login_required`` — so a banner they dismiss is
    hidden for that page view only and returns on the next load. That is
    the banner primitive's documented contract rather than a gap here.

    Cached on the request: a context processor is called once per template
    render, and a page that renders a fragment inside another render would
    otherwise pay for the query twice.

    Args:
        request: The incoming request. Must carry ``user`` — this runs
            after AuthenticationMiddleware, as every context processor does.

    Returns:
        The banners to render, in display order. Empty when none apply.

    """
    cached: list[Any] | None = getattr(request, _CACHE_ATTR, None)
    if cached is not None:
        return cached

    banners = list(_active_for_user(request))
    setattr(request, _CACHE_ATTR, banners)
    return banners


def _active_for_user(request: HttpRequest) -> QuerySet[Any]:
    """
    Build the banner queryset for this request's user.

    Split out from ``banners_for_request`` so the caching and the query are
    separately readable; ``filter_user`` already applies ``active()``, and
    the extra ``active()`` the package's own shortcut adds on top is
    redundant rather than harmful.

    Args:
        request: The incoming request.

    Returns:
        An unevaluated queryset ordered most severe first, then newest.

    """
    # The manager is built with ``from_queryset``, so ``filter_user`` exists
    # at runtime but is invisible to a type checker reading the class — the
    # cast is what lets both mypy and the editor's Pyright see past it.
    manager = cast("Any", PersistentMessage.objects)
    banners = manager.filter_user(request.user)
    return cast("QuerySet[Any]", banners.order_by("-level", "-created_at"))
