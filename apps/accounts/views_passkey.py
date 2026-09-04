"""
apps/accounts/views_passkey.py — HTTP views for the WebAuthn / passkey flow.

Provides five endpoints that back the browser's WebAuthn API calls:

  passkey_auth_request     GET  — return authentication options (challenge).
  passkey_auth_response    POST — verify navigator.credentials.get() response;
                                  log the user in via Django auth on success.
  passkey_register_request GET  — return registration options (challenge).
  passkey_register_response POST — verify navigator.credentials.create() response;
                                   persist the new PasskeyCredential.
  passkey_delete           POST — hard-delete one PasskeyCredential for the
                                  authenticated user (HTMX).

All WebAuthn endpoints consume and produce JSON.  The ``passkey_delete`` view
returns empty 200; HTMX handles DOM removal via ``hx-swap="outerHTML"``.

Registration and delete are gated on ``request.user.is_authenticated`` so any
authenticated ``auth.User`` — including staff/superusers without an Account
profile — can manage passkeys.

Rate limiting:
  passkey_auth_response:     10 requests/min per IP.
  passkey_register_response: 10 requests/min per IP.
  passkey_delete:             5 requests/min per IP.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth import login
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from apps.core.decorators import require_htmx

from .models import PasskeyCredential
from .redirects import safe_next
from .services.passkey import (
    PasskeyError,
    PasskeyUnknownCredentialError,
    generate_authentication_options,
    generate_registration_options as _gen_reg_opts,
    verify_and_save_registration,
    verify_authentication_response as _verify_auth_response,
)

logger = logging.getLogger(__name__)

_TOKEN_BACKEND = "apps.accounts.backends.TokenBackend"  # noqa: S105 — backend path, not a password


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@require_GET
def passkey_auth_request(request: HttpRequest) -> JsonResponse:
    """
    Return WebAuthn authentication options for navigator.credentials.get().

    Generates a fresh challenge and stores it in the session.  Returns options
    with an empty ``allowCredentials`` list so the browser presents all
    available passkeys via conditional UI autofill.

    Args:
        request: Incoming GET request.

    Returns:
        JSON response containing PublicKeyCredentialRequestOptions.

    """
    options = generate_authentication_options(request.session)
    return JsonResponse(options)


@require_POST
@ratelimit(key="ip", rate="10/m", block=False)
def passkey_auth_response(request: HttpRequest) -> JsonResponse:
    """
    Verify a navigator.credentials.get() response and log the user in.

    On success: calls ``django.contrib.auth.login()`` to establish the
    Django session and returns a JSON response containing
    ``{"ok": true, "next": <url or null>}`` — the browser navigates to
    ``next`` when it is set (SNOW-825), so a recipient who signed in from a
    trip share link lands back on the trip. The client sends its ``next``
    alongside the credential in the same JSON object; the extra key is
    ignored by the WebAuthn parser, which reads only the fields it names.
    It is validated here with ``safe_next`` and answered as ``null`` when it
    is not a same-site destination — this endpoint must never hand the
    browser somebody else's host.

    On failure: returns a 4xx JSON error.  If the credential is unknown (e.g.
    revoked but still cached in the browser), returns HTTP 404 with
    ``{"error": "unknown_credential", "credentialId": "…"}`` so the browser
    JS can call ``PublicKeyCredential.signalUnknownCredential()``.

    Args:
        request: POST request with the raw WebAuthn JSON in the body.

    Returns:
        JSON response.

    """
    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limited"}, status=429)

    credential_json = request.body.decode("utf-8")
    if not credential_json:
        return JsonResponse({"error": "empty_body"}, status=400)

    try:
        payload = json.loads(credential_json)
    except json.JSONDecodeError, TypeError:
        return JsonResponse({"error": "invalid_json"}, status=400)

    # The body is attacker-shaped as well as attacker-supplied: anything that
    # is not a string is not a destination, and never reaches ``safe_next``.
    raw_next = payload.get("next") if isinstance(payload, dict) else None
    next_url = safe_next(request, raw_next if isinstance(raw_next, str) else None)

    try:
        user = _verify_auth_response(credential_json, request.session)
    except PasskeyUnknownCredentialError as exc:
        return JsonResponse(
            {"error": "unknown_credential", "credentialId": exc.credential_id},
            status=404,
        )
    except PasskeyError as exc:
        logger.info("Passkey auth failed: %s", exc)
        return JsonResponse({"error": "verification_failed"}, status=400)

    login(request, user, backend=_TOKEN_BACKEND)
    logger.info("User pk=%s signed in via passkey", user.pk)
    return JsonResponse({"ok": True, "next": next_url})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@require_GET
def passkey_register_request(request: HttpRequest) -> JsonResponse:
    """
    Return WebAuthn registration options for navigator.credentials.create().

    Requires authentication; returns 403 if unauthenticated.  Any authenticated
    auth.User (including staff without an Account profile) may register a passkey.

    Args:
        request: Incoming GET request.

    Returns:
        JSON response containing PublicKeyCredentialCreationOptions, or 403.

    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "unauthenticated"}, status=403)

    options = _gen_reg_opts(request.user, request.session)
    return JsonResponse(options)


@require_POST
@ratelimit(key="ip", rate="10/m", block=False)
def passkey_register_response(request: HttpRequest) -> JsonResponse:
    """
    Verify a navigator.credentials.create() response and persist the passkey.

    Requires authentication; returns 403 if unauthenticated.

    On success: returns JSON with the new passkey's UUID, name, and device_type.
    On failure: returns a 4xx JSON error.

    Args:
        request: POST request with the raw WebAuthn JSON in the body.

    Returns:
        JSON response.

    """
    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limited"}, status=429)

    if not request.user.is_authenticated:
        return JsonResponse({"error": "unauthenticated"}, status=403)

    credential_json = request.body.decode("utf-8")
    if not credential_json:
        return JsonResponse({"error": "empty_body"}, status=400)

    try:
        passkey = verify_and_save_registration(
            credential_json, request.session, request.user
        )
    except PasskeyError as exc:
        # The exception detail stays server-side: return a fixed token, as the
        # sibling auth view does (SNOW-558, CodeQL py/stack-trace-exposure).
        # Both callers of this endpoint render their own translated string and
        # ignore this value, so there is no user-facing loss.
        logger.info(
            "Passkey registration failed for user pk=%s: %s", request.user.pk, exc
        )
        return JsonResponse({"error": "registration_failed"}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "passkey": {
                "uuid": str(passkey.uuid),
                "name": passkey.name,
                "device_type": passkey.device_type,
            },
        }
    )


# ---------------------------------------------------------------------------
# Passkey management
# ---------------------------------------------------------------------------


@require_POST
@require_htmx
@ratelimit(key="ip", rate="5/m", block=False)
def passkey_delete(request: HttpRequest, passkey_uuid: str) -> HttpResponse:
    """
    Hard-delete a specific PasskeyCredential for the authenticated user.

    Returns an empty 200 so HTMX can remove the credential card from the DOM
    via ``hx-swap="outerHTML"``.

    Guarded by authentication (no session → 403), ``@require_POST``,
    ``@require_htmx``, and rate-limited at 5 requests/min per IP.

    Args:
        request: HTMX POST request.
        passkey_uuid: UUID string of the PasskeyCredential to delete.

    Returns:
        200 on success, 403 when unauthenticated, 429 when rate-limited.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    if not request.user.is_authenticated:
        return HttpResponse(status=403)

    passkey = get_object_or_404(
        PasskeyCredential,
        uuid=passkey_uuid,
        user=request.user,
    )
    passkey.delete()
    logger.info(
        "User pk=%s deleted passkey %s",
        request.user.pk,
        passkey_uuid,
    )
    return HttpResponse(status=200)
