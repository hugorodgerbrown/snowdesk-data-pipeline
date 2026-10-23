"""
apps/data_feed/auth.py — The data feed's bearer check: a service client's token only.

``@require_feed_token`` admits a request only when its bearer token

* is a live access token carrying the ``data`` scope;
* is bound (RFC 8707) to a resource that is a URL prefix of the path called
  — django-oauth-toolkit checks the prefix, and a token that names no
  resource at all is refused here, because the toolkit would treat it as
  good for any audience;
* belongs to a **service client**: a confidential client-credentials
  application a member of staff created in the admin
  (``apps.oauth.validators.is_service_client``).

The last rule is the one that matters. A person's MCP connection is an
authorization-code token with a user behind it; even if one asked for the
``data`` scope at consent, its application is not a service client and it
is refused. Revoking a client is deleting its application in the admin.

Anything else is mcp-auth's challenge: a 401 (403 for a scope error)
whose ``WWW-Authenticate`` names this path's protected-resource metadata.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from mcp_auth.resource import INVALID_TOKEN, unauthorised
from oauth2_provider.oauth2_backends import get_oauthlib_core

from apps.data_feed import contract
from apps.oauth.validators import is_service_client


def require_feed_token(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Admit only a service client's token, bound to this feed, with the data scope.

    The wrapped view runs with ``request.feed_client`` set to the
    application. CSRF-exempt: it reads only the Authorization header, which
    a cross-site form cannot set, and ignores cookies.

    Args:
        view: The feed view.

    Returns:
        The guarded view.

    """

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        valid, oauth = get_oauthlib_core().verify_request(
            request, scopes=[contract.SCOPE]
        )
        if not valid:
            return unauthorised(request, getattr(oauth, "oauth2_error", None))
        token = oauth.access_token
        if not token.resource or not is_service_client(token.application):
            return unauthorised(request, INVALID_TOKEN)
        request.feed_client = token.application  # type: ignore[attr-defined]
        return view(request, *args, **kwargs)

    return csrf_exempt(wrapped)  # nosemgrep: bearer header only, see docstring
