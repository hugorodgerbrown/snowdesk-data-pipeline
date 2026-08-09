"""
apps/public/decorators.py — View decorators for the public bulletin site.

Provides ``lowercase_region_id``, a lightweight decorator that permanently
redirects requests whose ``region_id`` URL kwarg is not already in canonical
lowercase form.  This is the canonicalisation layer that pairs with
``RegionIdConverter`` (which allows mixed-case matches) to ensure every
non-lowercase inbound URL is permanently redirected to its canonical form
before any view logic or DB access occurs.

The redirect comes in two flavours, selected by the ``preserve_method``
argument:

  - **301** (default) for full-page GET views, where the status code is also
    an SEO signal to search engines.
  - **308** (``preserve_method=True``) for POST-only endpoints, where a 301
    would silently downgrade the request to a GET and produce a 405.  See
    ``docs/decisions/method-preserving-region-id-redirect.md`` (SNOW-650).
"""

import functools
import logging
from collections.abc import Callable
from typing import Any, overload

from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger(__name__)

_ViewFunc = Callable[..., HttpResponse]


class HttpResponsePermanentRedirectPreserveMethod(HttpResponsePermanentRedirect):
    """
    A permanent redirect (308) that preserves the request's method and body.

    Django ships no 308 response class, only ``HttpResponsePermanentRedirect``
    (301).  A user agent following a 301 on a POST re-issues it as a GET; on
    a 308 it replays the original method and body against the new location.
    """

    status_code = 308


@overload
def lowercase_region_id(view_func: _ViewFunc) -> _ViewFunc: ...


@overload
def lowercase_region_id(
    *, preserve_method: bool
) -> Callable[[_ViewFunc], _ViewFunc]: ...


def lowercase_region_id(
    view_func: _ViewFunc | None = None, *, preserve_method: bool = False
) -> _ViewFunc | Callable[[_ViewFunc], _ViewFunc]:
    """
    Permanently redirect when the ``region_id`` URL kwarg is not lowercase.

    If the inbound ``region_id`` kwarg contains any uppercase characters the
    decorator returns a permanent redirect whose ``Location`` is
    ``request.path`` with the first occurrence of the original ``region_id``
    replaced by its lowercase equivalent.  Any query string on the original
    request is preserved on the redirect target.

    When ``region_id`` is already lowercase the wrapped view is called
    unchanged.

    Usable bare (``@lowercase_region_id``) or with arguments
    (``@lowercase_region_id(preserve_method=True)``).

    This decorator performs no DB access.  Place it *above* any other
    decorators (e.g. ``@require_htmx``, ``@require_POST``, ``@condition``) so
    the casing check short-circuits before cache headers, method guards or
    HTMX guards are evaluated.

    Args:
        view_func: The view function to wrap, when used bare.
        preserve_method: When ``True``, redirect with **308** rather than
            **301** so a POST is replayed as a POST at the canonical path.
            Required on any method-restricted endpoint — every EAWS region id
            is uppercase, so a 301 here downgrades every POST to a GET and the
            view answers 405 (SNOW-650).  Leave ``False`` on full-page GET
            views, where 301 is the established canonical-URL signal.

    Returns:
        Wrapped view that permanently redirects non-lowercase ``region_id``
        kwargs, or — when called with arguments — the decorator that produces
        one.

    """
    redirect_class: type[HttpResponsePermanentRedirect] = (
        HttpResponsePermanentRedirectPreserveMethod
        if preserve_method
        else HttpResponsePermanentRedirect
    )

    def decorator(func: _ViewFunc) -> _ViewFunc:
        """Wrap ``func`` with the casing check."""

        @functools.wraps(func)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            """Check region_id casing and redirect to lowercase if needed."""
            region_id: str = kwargs.get("region_id", "")
            if region_id != region_id.lower():
                canonical_path = request.path.replace(region_id, region_id.lower(), 1)
                qs = request.META.get("QUERY_STRING", "")
                location = f"{canonical_path}?{qs}" if qs else canonical_path
                # The Location is derived from request.path, so it is
                # remote-source-derived even though it cannot in practice carry
                # a host: RegionIdConverter's regex admits no dot or slash, and
                # Django's root resolver anchors on a single leading "/", so a
                # protocol-relative path never routes here in the first place.
                # The check is free, and refusing to hand a user agent an
                # off-site Location is worth more than the argument that it
                # cannot happen. An unsafe path falls through to the view
                # uncanonicalised rather than redirecting.
                if not url_has_allowed_host_and_scheme(location, allowed_hosts=None):
                    logger.warning(
                        "Refusing to canonicalise %r — off-site redirect target %s",
                        region_id,
                        location,
                    )
                    return func(request, *args, **kwargs)
                logger.debug(
                    "Redirecting non-canonical region_id %r to %s",
                    region_id,
                    location,
                )
                return redirect_class(location)
            return func(request, *args, **kwargs)

        return wrapper

    if view_func is not None:
        return decorator(view_func)
    return decorator
