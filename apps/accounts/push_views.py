"""
apps/accounts/push_views.py — Web Push JSON endpoints.

Four views, all JSON in / JSON out:

- ``push_register``   POST  — body: ``{endpoint, keys: {p256dh, auth}, mechanism?}``.
                              Upserts a PushSubscription keyed by endpoint.
                              ``mechanism`` defaults to ``"SW"`` when omitted,
                              is upper-cased before validation (SNOW-582 — so
                              a stale client still sending the old lower-case
                              wire value keeps working), and must be one of
                              ``PushSubscription.Mechanism`` (SNOW-380); an
                              unknown value is rejected with 400.
- ``push_unregister`` POST  — body: ``{endpoint}``. Hard-deletes the row.
- ``push_test``       POST  — body: ``{endpoint?, title?, body?, url?}``.
                              Enqueues one ``_worker_dispatch_push`` task per
                              matching subscription (or all rows if no endpoint
                              is passed) and returns a count of rows enqueued.
- ``push_subscriptions`` GET — every stored row, for the demo page's list
                              (SNOW-874). Read-only, so no CSRF token and no
                              POST; the three writes above still take both.

``push_register`` and ``push_unregister`` are open to any signed-in account
since SNOW-1325, because bulletin alerts subscribe devices from the
settings page (``static/js/alerts_optin.js``). An anonymous call gets a 401,
not a redirect, since the callers are fetch() clients. A non-staff caller
needs an Account profile, and may unregister only its own endpoints; staff
keep the spike's reach over every row. ``push_test`` and
``push_subscriptions`` stay staff-only — they act on every row — and the
account's own test is ``accounts:alert_test``.

The POSTs rely on Django's default CSRF middleware; the JS clients send the
``X-CSRFToken`` header read from the ``csrftoken`` cookie.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import Account, PushSubscription
from apps.accounts.push_service import enqueue_push

logger = logging.getLogger(__name__)


def _parse_json(request: HttpRequest) -> dict[str, Any]:
    """Parse and return the JSON body, or an empty dict on failure."""
    try:
        data: dict[str, Any] = json.loads(request.body.decode("utf-8") or "{}")
        return data
    except ValueError, UnicodeDecodeError:
        return {}


def _signed_in_json(
    view: Callable[[HttpRequest], HttpResponse],
) -> Callable[[HttpRequest], HttpResponse]:
    """Return 401 JSON for an anonymous caller instead of a sign-in redirect."""

    @wraps(view)
    def wrapper(request: HttpRequest) -> HttpResponse:
        """Refuse anonymous callers, then call the view."""
        if not request.user.is_authenticated:
            return JsonResponse({"ok": False, "error": "sign in"}, status=401)
        return view(request)

    return wrapper


@_signed_in_json
@require_POST
def push_register(request: HttpRequest) -> HttpResponse:
    """Upsert a PushSubscription keyed by the browser-provided endpoint."""
    data = _parse_json(request)
    endpoint = data.get("endpoint")
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not (endpoint and p256dh and auth):
        return JsonResponse({"ok": False, "error": "missing fields"}, status=400)

    # Normalise before validating: a stale cached page or service worker may
    # still send the pre-SNOW-582 lower-case wire value ("sw", "declarative").
    # This is a tolerant read of an inbound value, not a lower-case store —
    # the row is always persisted with the upper-case member below.
    mechanism = str(data.get("mechanism", PushSubscription.Mechanism.SW)).upper()
    if mechanism not in PushSubscription.Mechanism.values:
        return JsonResponse({"ok": False, "error": "invalid mechanism"}, status=400)

    # A signed-in user with an Account links the PushSubscription to it.
    # Staff with no profile (createsuperuser) may still register for the
    # spike, leaving account=None; anyone else needs a profile.
    account: Account | None = None
    try:
        account = request.user.account  # type: ignore[union-attr]  # _signed_in_json guarantees authentication
    except Account.DoesNotExist:
        if not request.user.is_staff:
            return JsonResponse({"ok": False, "error": "no account"}, status=403)
    obj, created = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "p256dh": p256dh,
            "auth": auth,
            "account": account,
            "user_agent": request.headers.get("User-Agent", "")[:512],
            "mechanism": mechanism,
            "inactive_at": None,
        },
    )
    logger.info(
        "push subscription %s pk=%s endpoint=%.30s…",
        "created" if created else "updated",
        obj.pk,
        obj.endpoint,
    )
    return JsonResponse({"ok": True, "created": created, "uuid": str(obj.uuid)})


@_signed_in_json
@require_POST
def push_unregister(request: HttpRequest) -> HttpResponse:
    """Hard-delete the PushSubscription matching the supplied endpoint.

    A non-staff caller can delete only a row on its own account.
    """
    data = _parse_json(request)
    endpoint = data.get("endpoint")
    if not endpoint:
        return JsonResponse({"ok": False, "error": "missing endpoint"}, status=400)
    rows = PushSubscription.objects.filter(endpoint=endpoint)
    if not request.user.is_staff:
        # _signed_in_json guarantees a pk; ``or 0`` only narrows the type.
        rows = rows.filter(account__user_id=request.user.pk or 0)
    deleted, _ = rows.delete()
    return JsonResponse({"ok": True, "deleted": deleted})


@staff_member_required
@require_POST
def push_test(request: HttpRequest) -> HttpResponse:
    """Enqueue a push notification task for one or all stored subscriptions.

    Body fields (all optional):

    - ``endpoint``  — if present, only enqueue for that one row
    - ``title``     — notification title (default: "Snowdesk")
    - ``body``      — notification body  (default: stub copy)
    - ``url``       — URL to open on click (default: "/map/")

    Returns ``{"ok": True, "enqueued": N}`` where N is the number of tasks
    queued.  Actual delivery happens asynchronously in the background worker.
    """
    data = _parse_json(request)
    qs = PushSubscription.objects.active()
    if endpoint := data.get("endpoint"):
        qs = qs.filter(endpoint=endpoint)

    payload = {
        "title": data.get("title", "Snowdesk"),
        "body": data.get("body", "Hello from the Web Push spike."),
        "url": data.get("url", "/map/"),
    }

    # SNOW-384: thread client_version through to the background dispatch —
    # the worker has no request of its own to read the header from.
    # Client sends this header via static/js/pwa_client_version.js (SNOW-388).
    client_version = request.headers.get("X-Client-Version", "")

    count = 0
    for sub in qs:
        enqueue_push(sub, payload, client_version)
        count += 1

    return JsonResponse({"ok": True, "enqueued": count})


@staff_member_required
@require_GET
def push_subscriptions(request: HttpRequest) -> HttpResponse:
    """Return every stored PushSubscription, newest first.

    SNOW-874: the demo page used to render this list server-side at page
    load, so it froze at whatever the table held when the page was opened.
    Enabling push then created a row the list never showed, and the page
    ended up displaying one endpoint in its State panel and a different,
    hour-old one directly below — which is worse than showing nothing,
    because it invites the reader to reconcile two views that were never
    describing the same moment. The list is now fetched alongside the rest
    of the state, so it moves when the state moves.

    Each row carries the endpoint, the owning account's email (or a
    placeholder for the anonymous staff rows the spike allows), the
    mechanism, and whether a 410 has marked it inactive.
    """
    rows = [
        {
            "endpoint": sub.endpoint,
            "label": sub.account.user.email if sub.account else "(anon)",
            "mechanism": sub.mechanism,
            "inactive": sub.inactive_at is not None,
        }
        for sub in PushSubscription.objects.select_related("account__user")
    ]
    return JsonResponse({"ok": True, "subscriptions": rows})
