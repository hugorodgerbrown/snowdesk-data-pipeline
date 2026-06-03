"""
public/decorators.py — View decorators for the public bulletin site.

Provides ``lowercase_region_id``, a lightweight decorator that 301-redirects
requests whose ``region_id`` URL kwarg is not already in canonical lowercase
form.  This is the canonicalisation layer that pairs with ``RegionIdConverter``
(which allows mixed-case matches) to ensure every non-lowercase inbound URL
is permanently redirected to its canonical form before any view logic or DB
access occurs.
"""

import functools
import logging
from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect

logger = logging.getLogger(__name__)

_ViewFunc = Callable[..., HttpResponse]


def lowercase_region_id(view_func: _ViewFunc) -> _ViewFunc:
    """
    301-redirect when the ``region_id`` URL kwarg is not already lowercase.

    If the inbound ``region_id`` kwarg contains any uppercase characters the
    decorator returns an ``HttpResponsePermanentRedirect`` (301) whose
    ``Location`` is ``request.path`` with the first occurrence of the original
    ``region_id`` replaced by its lowercase equivalent.  Any query string on
    the original request is preserved on the redirect target.

    When ``region_id`` is already lowercase the wrapped view is called
    unchanged.

    This decorator performs no DB access.  Place it *above* any other
    decorators (e.g. ``@require_htmx``, ``@condition``) so the casing check
    short-circuits before cache headers or HTMX guards are evaluated.

    Args:
        view_func: The view function to wrap.

    Returns:
        Wrapped view that 301-redirects non-lowercase ``region_id`` kwargs.

    """

    @functools.wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Check region_id casing and redirect to lowercase if needed."""
        region_id: str = kwargs.get("region_id", "")
        if region_id != region_id.lower():
            canonical_path = request.path.replace(region_id, region_id.lower(), 1)
            qs = request.META.get("QUERY_STRING", "")
            location = f"{canonical_path}?{qs}" if qs else canonical_path
            logger.debug(
                "Redirecting non-canonical region_id %r to %s",
                region_id,
                location,
            )
            return HttpResponsePermanentRedirect(location)
        return view_func(request, *args, **kwargs)

    return wrapper
