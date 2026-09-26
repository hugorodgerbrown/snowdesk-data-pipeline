"""
apps/oauth/views.py — OAuth 2.1 endpoints for the MCP server (SNOW-1035).

Discovery (mounted at the root in ``config/urls.py``):

* ``GET /.well-known/oauth-protected-resource[/<path>]`` — RFC 9728
  protected-resource metadata. ``resource`` is the origin plus the path
  suffix exactly as requested, because Claude requires it to equal the URL
  the user typed.
* ``GET /.well-known/oauth-authorization-server`` — RFC 8414 AS metadata.

Endpoints under ``/oauth/`` (namespace ``oauth``):

* ``authorize/`` — GET shows the consent page, POST records the decision.
* ``token/`` — the ``authorization_code`` and ``refresh_token`` grants.
* ``register/`` — Dynamic Client Registration (RFC 7591).
* ``revoke/`` — token revocation (RFC 7009).
* ``grants/<uuid>/revoke/`` — the settings page's Disconnect (HTMX).

**Why the consent POST does not answer with a 302.** The site CSP carries
``form-action 'self'``, and Chrome applies ``form-action`` to the redirects
that follow a form submission — a 302 from this POST to
``https://claude.ai/…`` would be blocked once the CSP is enforced.
django-csp-plus builds the header in middleware from settings and database
rules with no per-response hook to widen one directive, so the POST renders
a small page that moves on with ``<meta http-equiv="refresh">`` and carries
a link for the reader to follow by hand. A meta refresh is a navigation,
not a form submission, so ``form-action`` does not apply to it.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from django.db import transaction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
    QueryDict,
)
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from apps.accounts.models import user_is_verified
from apps.core.decorators import require_htmx
from apps.oauth.models import OAuthClient, OAuthGrant
from apps.oauth.services.clients import (
    RegistrationError,
    register_client,
    registration_response,
    resolve_client,
)
from apps.oauth.services.pkce import is_valid_challenge
from apps.oauth.services.redirects import only_loopback, registered_redirect_uri
from apps.oauth.services.resource import (
    is_mcp_resource,
    mcp_resource_url,
    request_origin,
)
from apps.oauth.services.tokens import (
    SUPPORTED_SCOPES,
    OAuthError,
    exchange_code,
    issue_code,
    normalise_scope,
    refresh,
    revoke_grant,
    revoke_token,
)

logger = logging.getLogger(__name__)

# The authorize request's parameters, carried as hidden fields from the
# consent GET to its POST so the POST can re-run every check.
AUTHORIZE_PARAMS: tuple[str, ...] = (
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "state",
    "scope",
    "resource",
)

METADATA_MAX_AGE_SECONDS: int = 3600


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_store(response: HttpResponse) -> HttpResponse:
    """Mark a response that carries a secret or per-request state uncacheable.

    Args:
        response: The response to annotate.

    Returns:
        The same response.

    """
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


def _oauth_error(error: str, description: str, status: int = 400) -> HttpResponse:
    """Return an RFC 6749 §5.2 error body.

    Args:
        error: The ``error`` code.
        description: The ``error_description``.
        status: The HTTP status.

    Returns:
        A no-store JSON response.

    """
    return _no_store(
        JsonResponse({"error": error, "error_description": description}, status=status)
    )


def _metadata_response(body: dict[str, Any]) -> HttpResponse:
    """Return a public, hour-cacheable JSON metadata document.

    Args:
        body: The document.

    Returns:
        The JSON response.

    """
    response = JsonResponse(body)
    response["Cache-Control"] = f"public, max-age={METADATA_MAX_AGE_SECONDS}"
    return response


def _with_query(uri: str, params: dict[str, str]) -> str:
    """Return ``uri`` with ``params`` added to any query it already has.

    Empty values are dropped, so an absent ``state`` is not sent back as
    ``state=``.

    Args:
        uri: The client's redirect URI.
        params: The parameters to add.

    Returns:
        The combined URL.

    """
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((k, v) for k, v in params.items() if v)
    return urlunsplit(parts._replace(query=urlencode(query)))


def _authorize_params(source: QueryDict) -> dict[str, str]:
    """Return the authorize parameters from a GET or POST dict.

    Args:
        source: ``request.GET`` or ``request.POST``.

    Returns:
        Every name in ``AUTHORIZE_PARAMS``, ``""`` when absent.

    """
    return {name: source.get(name, "") for name in AUTHORIZE_PARAMS}


def _error_page(request: HttpRequest, message: str, status: int = 400) -> HttpResponse:
    """Render the error page — used when a redirect back is not safe.

    Args:
        request: The current request.
        message: What went wrong, already translated.
        status: The HTTP status.

    Returns:
        The rendered page.

    """
    return render(request, "oauth/error.html", {"message": message}, status=status)


def _returning_page(
    request: HttpRequest, redirect_uri: str, params: dict[str, str]
) -> HttpResponse:
    """Render the page that sends the browser back to the client.

    See the module docstring for why this is a page and not a 302.

    Args:
        request: The current request.
        redirect_uri: The validated redirect URI.
        params: ``code`` / ``error`` and ``state`` to add to it.

    Returns:
        The rendered interstitial.

    """
    target = _with_query(redirect_uri, params)
    return _no_store(
        render(
            request,
            "oauth/returning.html",
            {"target": target, "host": urlsplit(redirect_uri).hostname or ""},
        )
    )


def _resolve_client_and_redirect(
    request: HttpRequest, params: dict[str, str]
) -> tuple[OAuthClient, str] | HttpResponse:
    """Return the client and redirect URI, or the error page when either fails.

    Never redirects: until both are known good, sending the browser to the
    ``redirect_uri`` would make this an open redirector.

    Args:
        request: The current request.
        params: The authorize parameters.

    Returns:
        ``(client, redirect_uri)`` or the error page.

    """
    client = resolve_client(params["client_id"])
    if client is None:
        return _error_page(
            request, _("The app asking to connect is not one Snowdesk recognises.")
        )
    # Every redirect from here on goes to the client's registered URI, never
    # to the request's string (only a loopback port is taken from it).
    redirect_uri = registered_redirect_uri(client, params["redirect_uri"])
    if redirect_uri is None:
        return _error_page(
            request,
            _("The app asked to send you back to an address it has not registered."),
        )
    return client, redirect_uri


class _AuthorizeError(Exception):
    """An authorize-request error reported back to the client's redirect URI."""

    def __init__(self, error: str, description: str) -> None:
        """Store the RFC 6749 ``error`` and its description.

        Args:
            error: The ``error`` code.
            description: The ``error_description``.

        """
        super().__init__(description)
        self.error = error
        self.description = description


def _validate_authorize(
    request: HttpRequest, params: dict[str, str]
) -> tuple[str, str]:
    """Check the authorize parameters whose failure is reported to the client.

    Args:
        request: The current request.
        params: The authorize parameters.

    Returns:
        ``(scope, resource)``, normalised.

    Raises:
        _AuthorizeError: Naming the RFC 6749 error to redirect back with.

    """
    if params["response_type"] != "code":
        raise _AuthorizeError(
            "unsupported_response_type", "response_type must be code."
        )
    if params["code_challenge_method"] != "S256" or not is_valid_challenge(
        params["code_challenge"]
    ):
        raise _AuthorizeError("invalid_request", "An S256 code_challenge is required.")
    scope = normalise_scope(params["scope"])
    if scope is None:
        raise _AuthorizeError(
            "invalid_scope", f"Supported scopes: {' '.join(sorted(SUPPORTED_SCOPES))}."
        )
    resource = params["resource"] or mcp_resource_url(request)
    if not is_mcp_resource(request, resource):
        raise _AuthorizeError(
            "invalid_target", "resource must be this server's MCP URL."
        )
    return scope, resource


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@require_GET
def protected_resource_metadata(request: HttpRequest, suffix: str = "") -> HttpResponse:
    """Serve RFC 9728 protected-resource metadata.

    ``/.well-known/oauth-protected-resource/api/mcp/`` answers for the
    resource ``<origin>/api/mcp/`` — the suffix is echoed exactly, slash and
    all, because Claude compares it byte for byte with the URL the user
    typed. The bare path answers for the documented MCP URL.

    Args:
        request: The GET request.
        suffix: The path after ``oauth-protected-resource/``, if any.

    Returns:
        The metadata document.

    """
    origin = request_origin(request)
    resource = f"{origin}/{suffix}" if suffix else mcp_resource_url(request)
    return _metadata_response(
        {
            "resource": resource,
            "authorization_servers": [origin],
            "scopes_supported": ["mcp"],
            "bearer_methods_supported": ["header"],
            "resource_name": "Snowdesk",
        }
    )


@require_GET
def authorization_server_metadata(request: HttpRequest) -> HttpResponse:
    """Serve RFC 8414 authorization-server metadata.

    ``client_id_metadata_document_supported`` together with ``"none"`` in
    ``token_endpoint_auth_methods_supported`` is what makes Claude use CIMD
    rather than DCR. ``offline_access`` in ``scopes_supported`` is what
    makes it ask for a refresh token.

    Args:
        request: The GET request.

    Returns:
        The metadata document.

    """
    origin = request_origin(request)
    return _metadata_response(
        {
            "issuer": origin,
            "authorization_endpoint": origin + reverse("oauth:authorize"),
            "token_endpoint": origin + reverse("oauth:token"),
            "registration_endpoint": origin + reverse("oauth:register"),
            "revocation_endpoint": origin + reverse("oauth:revoke"),
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["mcp", "offline_access"],
            "client_id_metadata_document_supported": True,
        }
    )


# ---------------------------------------------------------------------------
# Authorize + consent
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def authorize(request: HttpRequest) -> HttpResponse:
    """Show the consent page (GET) or record the user's decision (POST).

    GET, in order:

    1. Resolve the client and redirect URI; if either fails, render the
       error page and never redirect.
    2. Send an anonymous visitor to sign-in with this URL as ``next``.
    3. Check ``response_type``, the S256 challenge, ``scope`` and
       ``resource``; a failure redirects back to the client with ``error``.
    4. An unverified account sees a "verify your email first" state.
    5. Otherwise render the consent page.

    POST re-runs every check against the hidden fields, then Approve
    creates or reactivates the grant and issues a code, and Deny returns
    ``access_denied``. Both leave through the returning page.

    Args:
        request: The GET or POST request.

    Returns:
        The consent page, the error page, a redirect, or the returning page.

    """
    source = request.POST if request.method == "POST" else request.GET
    params = _authorize_params(source)

    resolved = _resolve_client_and_redirect(request, params)
    if isinstance(resolved, HttpResponse):
        return resolved
    client, redirect_uri = resolved

    if not request.user.is_authenticated:
        if request.method == "POST":
            return _error_page(
                request, _("Your session ended. Start again from the app."), 403
            )
        sign_in = reverse("accounts:sign_in")
        return HttpResponseRedirect(
            f"{sign_in}?next={quote(request.get_full_path(), safe='/')}"
        )

    try:
        scope, resource = _validate_authorize(request, params)
    except _AuthorizeError as exc:
        reply = {
            "error": exc.error,
            "error_description": exc.description,
            "state": params["state"],
        }
        if request.method == "POST":
            return _returning_page(request, redirect_uri, reply)
        return HttpResponseRedirect(_with_query(redirect_uri, reply))

    verified = user_is_verified(request.user)
    if request.method == "GET" or not verified:
        return _no_store(
            render(
                request,
                "oauth/consent.html",
                {
                    "client": client,
                    "redirect_host": urlsplit(redirect_uri).hostname or "",
                    "loopback_only": only_loopback(client),
                    "params": params,
                    "verified": verified,
                },
                status=200 if verified or request.method == "GET" else 403,
            )
        )

    if request.POST.get("decision") != "approve":
        return _returning_page(
            request,
            redirect_uri,
            {"error": "access_denied", "state": params["state"]},
        )

    with transaction.atomic():
        grant, _is_new = OAuthGrant.objects.get_or_create(
            user=request.user, client=client
        )
        grant.scope = scope
        grant.resource = resource
        grant.revoked_at = None
        grant.save(update_fields=["scope", "resource", "revoked_at", "updated_at"])
        code = issue_code(
            grant,
            redirect_uri=redirect_uri,
            code_challenge=params["code_challenge"],
            resource=resource,
            scope=scope,
        )
    logger.info("oauth: user pk=%s approved client pk=%s", request.user.pk, client.pk)
    return _returning_page(
        request, redirect_uri, {"code": code, "state": params["state"]}
    )


# ---------------------------------------------------------------------------
# Token, registration, revocation — machine endpoints
# ---------------------------------------------------------------------------


# OAuth clients cannot hold a Django CSRF token, and these endpoints read no
# session: a public client authenticates with PKCE and its code or refresh
# token, never a cookie. Same rationale as apps.mcp_server.views.mcp_endpoint.
# nosemgrep: python.django.security.audit.csrf-exempt.no-csrf-exempt
@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="60/m", block=False)
def token(request: HttpRequest) -> HttpResponse:
    """Handle the ``authorization_code`` and ``refresh_token`` grants.

    The body is ``application/x-www-form-urlencoded``. A public client
    identifies itself with ``client_id`` in the body.

    Args:
        request: The POST request.

    Returns:
        The token response, or an RFC 6749 error (400; 401 for
        ``invalid_client``; 429 when rate-limited).

    """
    if getattr(request, "limited", False):
        return _oauth_error("slow_down", "Too many token requests.", 429)

    client = OAuthClient.objects.filter(
        client_id=request.POST.get("client_id", "")
    ).first()
    if client is None:
        return _oauth_error("invalid_client", "Unknown client_id.", 401)

    grant_type = request.POST.get("grant_type", "")
    try:
        if grant_type == "authorization_code":
            pair = exchange_code(
                raw_code=request.POST.get("code", ""),
                client=client,
                redirect_uri=request.POST.get("redirect_uri", ""),
                code_verifier=request.POST.get("code_verifier", ""),
                resource=request.POST.get("resource") or None,
            )
        elif grant_type == "refresh_token":
            pair = refresh(
                raw_refresh=request.POST.get("refresh_token", ""),
                client=client,
                resource=request.POST.get("resource") or None,
                scope=request.POST.get("scope") or None,
            )
        else:
            return _oauth_error(
                "unsupported_grant_type",
                "grant_type must be authorization_code or refresh_token.",
            )
    except OAuthError as exc:
        logger.info("oauth: token %s refused: %s", grant_type, exc.error)
        return _oauth_error(exc.error, exc.description, exc.status)

    return _no_store(JsonResponse(pair.as_response()))


# See ``token`` for why this is CSRF-exempt.
# nosemgrep: python.django.security.audit.csrf-exempt.no-csrf-exempt
@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="10/h", block=False)
def register(request: HttpRequest) -> HttpResponse:
    """Register a public client (RFC 7591). The body is JSON.

    Rate-limited to ten registrations an hour per IP, which bounds how fast
    anyone can grow the client table.

    Args:
        request: The POST request.

    Returns:
        201 with the client information, 400 with an RFC 7591 error, or 429.

    """
    if getattr(request, "limited", False):
        return _oauth_error("slow_down", "Too many registrations.", 429)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        return _oauth_error("invalid_client_metadata", "The body must be JSON.")
    try:
        client = register_client(payload)
    except RegistrationError as exc:
        return _oauth_error(exc.error, exc.description)
    return _no_store(JsonResponse(registration_response(client), status=201))


# See ``token`` for why this is CSRF-exempt.
# nosemgrep: python.django.security.audit.csrf-exempt.no-csrf-exempt
@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="60/m", block=False)
def revoke(request: HttpRequest) -> HttpResponse:
    """Revoke a token (RFC 7009). Always 200, whatever the token was.

    Args:
        request: The POST request (form-encoded ``token``, ``client_id``).

    Returns:
        An empty 200, or 429 when rate-limited.

    """
    if getattr(request, "limited", False):
        return _oauth_error("slow_down", "Too many revocation requests.", 429)
    revoke_token(request.POST.get("token", ""), request.POST.get("client_id") or None)
    return _no_store(HttpResponse(status=200))


# ---------------------------------------------------------------------------
# Settings page: Disconnect
# ---------------------------------------------------------------------------


@require_POST
@require_htmx
@ratelimit(key="ip", rate="10/m", block=False)
def grant_revoke(request: HttpRequest, grant_uuid: str) -> HttpResponse:
    """Disconnect one of the signed-in user's connected apps.

    Returns an empty 200 so HTMX can remove the row via
    ``hx-swap="outerHTML"`` — the shape of ``passkey_delete``.

    Args:
        request: HTMX POST request.
        grant_uuid: The grant's uuid.

    Returns:
        200 on success, 403 when unauthenticated, 404 for a grant that is
        not this user's, 429 when rate-limited.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)
    if not request.user.is_authenticated:
        return HttpResponse(status=403)
    grant = get_object_or_404(
        OAuthGrant.objects.for_user(request.user).active(), uuid=grant_uuid
    )
    revoke_grant(grant)
    logger.info("oauth: user pk=%s disconnected grant %s", request.user.pk, grant_uuid)
    return HttpResponse(status=200)
