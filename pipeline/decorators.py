"""
pipeline/decorators.py — Shared view decorators.

Currently hosts `require_htmx`, used by any view in the project that returns
an HTMX-only HTML fragment. Kept in the `pipeline` app because the pipeline
app is an import-time dependency of both `public` and `subscriptions`; either
of those apps is free to import from here without creating a circular
dependency.
"""

import logging
from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


_ViewFunc = Callable[..., HttpResponse]


def require_htmx(view_func: _ViewFunc) -> _ViewFunc:
    """
    Return 400 Bad Request for non-HTMX requests.

    Partial/fragment views should only be called by HTMX. This decorator
    enforces that, preventing full-page responses from being accidentally
    bypassed.

    Args:
        view_func: The view function to wrap.

    Returns:
        Wrapped view that returns 400 for non-HTMX requests.

    """

    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Check the request is an HTMX request before delegating."""
        if not request.htmx:  # type: ignore[attr-defined]
            logger.warning("Non-HTMX request to partial view %s", request.path)
            return HttpResponse("HTMX requests only.", status=400)
        return view_func(request, *args, **kwargs)

    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper
