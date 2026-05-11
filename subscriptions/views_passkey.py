"""
subscriptions/views_passkey.py — HTTP views for the WebAuthn / passkey flow.

Provides five endpoints that back the browser's WebAuthn API calls:

  passkey_auth_request     GET  — return authentication options (challenge).
  passkey_auth_response    POST — verify navigator.credentials.get() response;
                                  establish the subscriber session on success.
  passkey_register_request GET  — return registration options (challenge).
  passkey_register_response POST — verify navigator.credentials.create() response;
                                   persist the new PasskeyCredential.
  passkey_delete           POST — hard-delete one PasskeyCredential for the
                                  session-authenticated subscriber (HTMX).

All WebAuthn endpoints consume and produce JSON.  The ``passkey_delete`` view
returns empty 200; HTMX handles DOM removal via ``hx-swap="outerHTML"``.

Rate limiting:
  passkey_auth_response:     10 requests/min per IP.
  passkey_register_response: 10 requests/min per IP.
  passkey_delete:             5 requests/min per IP.
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from core.decorators import require_htmx

from .models import PasskeyCredential, Subscriber
from .services.passkey import (
    PasskeyError,
    PasskeyUnknownCredentialError,
    generate_authentication_options,
    generate_registration_options as _gen_reg_opts,
    verify_and_save_registration,
    verify_authentication_response as _verify_auth_response,
)

logger = logging.getLogger(__name__)

# Must match the _SESSION_KEY constant in views.py.
_SESSION_KEY = "subscriber_uuid"


def _get_subscriber_from_session(request: HttpRequest) -> Subscriber | None:
    """
    Return the session-authenticated Subscriber, or None.

    Mirrors the helper in views.py — kept local to avoid cross-view imports.

    Args:
        request: Incoming HTTP request with an attached session.

    Returns:
        Active Subscriber or None.

    """
    uuid_str = request.session.get(_SESSION_KEY)
    if not uuid_str:
        return None
    try:
        return Subscriber.objects.active().get(uuid=uuid_str)
    except (Subscriber.DoesNotExist, ValueError):
        request.session.pop(_SESSION_KEY, None)
        return None


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
    Verify a navigator.credentials.get() response and establish the subscriber session.

    On success: stores the subscriber UUID in the session and returns a JSON
    response containing ``{"ok": true}``.

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
        json.loads(credential_json)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"error": "invalid_json"}, status=400)

    try:
        subscriber = _verify_auth_response(credential_json, request.session)
    except PasskeyUnknownCredentialError as exc:
        return JsonResponse(
            {"error": "unknown_credential", "credentialId": exc.credential_id},
            status=404,
        )
    except PasskeyError as exc:
        logger.info("Passkey auth failed: %s", exc)
        return JsonResponse({"error": "verification_failed"}, status=400)

    request.session[_SESSION_KEY] = str(subscriber.uuid)
    logger.info("Subscriber %s signed in via passkey", subscriber.email)
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@require_GET
def passkey_register_request(request: HttpRequest) -> JsonResponse:
    """
    Return WebAuthn registration options for navigator.credentials.create().

    Requires a valid subscriber session; returns 403 if unauthenticated.

    Args:
        request: Incoming GET request.

    Returns:
        JSON response containing PublicKeyCredentialCreationOptions, or 403.

    """
    subscriber = _get_subscriber_from_session(request)
    if subscriber is None:
        return JsonResponse({"error": "unauthenticated"}, status=403)

    options = _gen_reg_opts(subscriber, request.session)
    return JsonResponse(options)


@require_POST
@ratelimit(key="ip", rate="10/m", block=False)
def passkey_register_response(request: HttpRequest) -> JsonResponse:
    """
    Verify a navigator.credentials.create() response and persist the passkey.

    Requires a valid subscriber session; returns 403 if unauthenticated.

    On success: returns JSON with the new passkey's UUID, name, and device_type.
    On failure: returns a 4xx JSON error.

    Args:
        request: POST request with the raw WebAuthn JSON in the body.

    Returns:
        JSON response.

    """
    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limited"}, status=429)

    subscriber = _get_subscriber_from_session(request)
    if subscriber is None:
        return JsonResponse({"error": "unauthenticated"}, status=403)

    credential_json = request.body.decode("utf-8")
    if not credential_json:
        return JsonResponse({"error": "empty_body"}, status=400)

    try:
        passkey = verify_and_save_registration(
            credential_json, request.session, subscriber
        )
    except PasskeyError as exc:
        logger.info("Passkey registration failed for %s: %s", subscriber.email, exc)
        return JsonResponse({"error": str(exc)}, status=400)

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
    Hard-delete a specific PasskeyCredential for the session-authenticated subscriber.

    Returns an empty 200 so HTMX can remove the credential card from the DOM
    via ``hx-swap="outerHTML"``.

    Guarded by session authentication (no session → 403), ``@require_POST``,
    ``@require_htmx``, and rate-limited at 5 requests/min per IP.

    Args:
        request: HTMX POST request.
        passkey_uuid: UUID string of the PasskeyCredential to delete.

    Returns:
        200 on success, 403 when unauthenticated, 429 when rate-limited.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    subscriber = _get_subscriber_from_session(request)
    if subscriber is None:
        return HttpResponse(status=403)

    passkey = get_object_or_404(
        PasskeyCredential,
        uuid=passkey_uuid,
        subscriber=subscriber,
    )
    passkey.delete()
    logger.info(
        "Subscriber %s deleted passkey %s",
        subscriber.email,
        passkey_uuid,
    )
    return HttpResponse(status=200)
