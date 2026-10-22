"""
apps/oauth/views.py — Snowdesk's two views around mcp-auth (SNOW-1193).

Everything under ``/oauth/`` and ``/.well-known/`` is mcp-auth's (on
django-oauth-toolkit), mounted from ``config/urls.py``. Snowdesk adds:

* :func:`authorize` — sits in front of mcp-auth's consent view at the same
  path. A signed-in account that has not verified its address gets the
  "verify your email first" page instead of the bare 403 the connect rule
  would otherwise produce; everyone else goes straight through. When the
  request is for the staff ops server, the consent page is told so
  (``ops_access``) and says what that grants.
* :class:`ProtectedResourceMetadataView` — answers the path form of
  ``/.well-known/oauth-protected-resource/…`` ahead of mcp-auth's route, so
  the ``resource`` keeps the trailing slash of the URL called. Claude
  requires it to equal the URL the user typed, and Snowdesk's documented
  URL is ``/api/mcp/``; django-oauth-toolkit's own view strips the slash.
* :func:`disconnect` — the HTMX Disconnect button on ``/account/settings/``,
  which revokes every token a user holds for one app and removes its row.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from mcp_auth.connected import connected_apps, disconnect as revoke_app
from mcp_auth.ratelimit import limit_by_ip
from mcp_auth.views import ConsentView
from oauth2_provider.views import OAuthProtectedResourceMetadataView

from apps.accounts.models import user_is_verified
from apps.core.decorators import require_htmx

logger = logging.getLogger(__name__)

# mcp-auth's own route, rate limit included, so the gate below changes
# nothing for the requests it lets through.
_consent = limit_by_ip("authorize", "AUTHORIZE_RATE_PER_MINUTE")(ConsentView.as_view())


def _asks_for_ops(request: HttpRequest) -> bool:
    """Return whether the authorize request names the ops server as its resource.

    Args:
        request: The GET or POST to ``/oauth/authorize/``.

    Returns:
        True when ``resource`` is ``/api/ops/mcp/``, with or without the slash.

    """
    resource = request.POST.get("resource") or request.GET.get("resource", "")
    ops_path = reverse("api:mcp:ops").rstrip("/")
    return urlsplit(resource).path.rstrip("/") == ops_path


def authorize(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
    """Hand the consent request to mcp-auth, unless the account is unverified.

    Args:
        request: The GET or POST to ``/oauth/authorize/``.
        *args: Passed through.
        **kwargs: Passed through.

    Returns:
        The "verify your email first" page (403) for an active, signed-in,
        unverified account; otherwise mcp-auth's answer, with ``ops_access``
        added to the consent page's context.

    """
    user = request.user
    if user.is_authenticated and user.is_active and not user_is_verified(user):
        return render(request, "oauth/verify_first.html", status=403)
    response: HttpResponseBase = _consent(request, *args, **kwargs)
    if isinstance(response, TemplateResponse) and response.context_data is not None:
        response.context_data["ops_access"] = _asks_for_ops(request)
    return response


class ProtectedResourceMetadataView(OAuthProtectedResourceMetadataView):
    """RFC 9728 metadata whose ``resource`` is the path called, slash and all."""

    def get_resource(self, request: HttpRequest) -> str:
        """Return the origin plus the path after the well-known prefix, verbatim.

        Args:
            request: The GET of ``/.well-known/oauth-protected-resource/<path>``.

        Returns:
            ``https://host/api/mcp/`` for ``…/oauth-protected-resource/api/mcp/``
            and ``https://host/api/mcp`` for the slash-less spelling.

        """
        resource: str = super().get_resource(request)
        if request.path.endswith("/") and not resource.endswith("/"):
            return f"{resource}/"
        return resource


@require_htmx
@require_POST
def disconnect(request: HttpRequest, application_id: int) -> HttpResponse:
    """Revoke the signed-in user's tokens for one app and remove its row.

    The row is the swap target (``hx-swap="outerHTML"``), so an empty 200
    removes it.

    Args:
        request: The HTMX POST from the settings page.
        application_id: The connected app's primary key.

    Returns:
        An empty 200; 403 when signed out.

    Raises:
        Http404: For an app this user is not connected to.

    """
    if not request.user.is_authenticated:
        return HttpResponse(status=403)
    apps = {app.pk: app for app in connected_apps(request.user)}
    application = apps.get(application_id)
    if application is None:
        raise Http404("Not a connected app.")
    revoke_app(request.user, application)
    logger.info(
        "oauth: user %s disconnected application %s", request.user.pk, application.pk
    )
    return HttpResponse("")
