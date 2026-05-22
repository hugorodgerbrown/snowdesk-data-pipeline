"""
subscriptions/push_views.py — Spike Web Push endpoints.

Three views, all JSON in / JSON out:

- ``push_register``   POST  — body: ``{endpoint, keys: {p256dh, auth}}``.
                              Upserts a PushSubscription keyed by endpoint.
- ``push_unregister`` POST  — body: ``{endpoint}``. Hard-deletes the row.
- ``push_test``       POST  — body: ``{endpoint?, title?, body?, url?}``.
                              Dispatches a real push to one endpoint (or all
                              rows if no endpoint passed). Returns per-row
                              status codes.

All three are CSRF-exempt for the spike — a production cut would either
plug them into the existing Subscriber session-auth flow + django-csp or
mint a per-subscription token signed by TimestampSigner.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from subscriptions.models import PushSubscription
from subscriptions.push_service import dispatch_push

logger = logging.getLogger(__name__)


def _parse_json(request: HttpRequest) -> dict[str, Any]:
    """Parse and return the JSON body, or an empty dict on failure."""
    try:
        data: dict[str, Any] = json.loads(request.body.decode("utf-8") or "{}")
        return data
    except (ValueError, UnicodeDecodeError):
        return {}


@csrf_exempt
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

    subscriber = request.user if request.user.is_authenticated else None
    obj, created = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "p256dh": p256dh,
            "auth": auth,
            "subscriber": subscriber,
            "user_agent": request.headers.get("User-Agent", "")[:512],
        },
    )
    logger.info("push subscription %s for %s", "created" if created else "updated", obj)
    return JsonResponse({"ok": True, "created": created, "uuid": str(obj.uuid)})


@csrf_exempt
@require_POST
def push_unregister(request: HttpRequest) -> HttpResponse:
    """Hard-delete the PushSubscription matching the supplied endpoint."""
    data = _parse_json(request)
    endpoint = data.get("endpoint")
    if not endpoint:
        return JsonResponse({"ok": False, "error": "missing endpoint"}, status=400)
    deleted, _ = PushSubscription.objects.filter(endpoint=endpoint).delete()
    return JsonResponse({"ok": True, "deleted": deleted})


@csrf_exempt
@require_POST
def push_test(request: HttpRequest) -> HttpResponse:
    """Send a real push notification to one or all stored subscriptions.

    Body fields (all optional):

    - ``endpoint``  — if present, only fire to that one row
    - ``title``     — notification title (default: "Snowdesk")
    - ``body``      — notification body  (default: stub copy)
    - ``url``       — URL to open on click (default: "/")
    """
    data = _parse_json(request)
    qs = PushSubscription.objects.all()
    if endpoint := data.get("endpoint"):
        qs = qs.filter(endpoint=endpoint)

    payload = {
        "title": data.get("title", "Snowdesk"),
        "body": data.get("body", "Hello from the Web Push spike."),
        "url": data.get("url", "/"),
    }

    results = []
    for sub in qs:
        result = dispatch_push(sub, payload)
        results.append({"uuid": str(sub.uuid), **result})

    return JsonResponse({"ok": True, "sent": len(results), "results": results})
