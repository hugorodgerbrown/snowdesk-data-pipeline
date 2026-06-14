"""
subscriptions/push_views.py — Web Push JSON endpoints (staff-only).

Three views, all JSON in / JSON out:

- ``push_register``   POST  — body: ``{endpoint, keys: {p256dh, auth}}``.
                              Upserts a PushSubscription keyed by endpoint.
- ``push_unregister`` POST  — body: ``{endpoint}``. Hard-deletes the row.
- ``push_test``       POST  — body: ``{endpoint?, title?, body?, url?}``.
                              Enqueues one ``_worker_dispatch_push`` task per
                              matching subscription (or all rows if no endpoint
                              is passed) and returns a count of rows enqueued.

All three are guarded by ``@staff_member_required`` and rely on Django's
default CSRF middleware. The JS client at ``static/js/push_demo.js`` sends
the ``X-CSRFToken`` header read from the ``csrftoken`` cookie.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_POST

from subscriptions.models import PushSubscription, Subscriber
from subscriptions.push_service import enqueue_push

logger = logging.getLogger(__name__)


def _parse_json(request: HttpRequest) -> dict[str, Any]:
    """Parse and return the JSON body, or an empty dict on failure."""
    try:
        data: dict[str, Any] = json.loads(request.body.decode("utf-8") or "{}")
        return data
    except (ValueError, UnicodeDecodeError):
        return {}


@staff_member_required
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

    # The staff gate guarantees an authenticated staff User.  Staff users who
    # have a Subscriber profile (e.g. created via the subscribe flow) link the
    # PushSubscription directly; staff with no profile leave subscriber=None.
    subscriber: Subscriber | None = None
    try:
        subscriber = request.user.subscriber  # type: ignore[union-attr]  # staff_member_required guarantees authentication
    except Subscriber.DoesNotExist:
        pass
    obj, created = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "p256dh": p256dh,
            "auth": auth,
            "subscriber": subscriber,
            "user_agent": request.headers.get("User-Agent", "")[:512],
        },
    )
    logger.info(
        "push subscription %s pk=%s endpoint=%.30s…",
        "created" if created else "updated",
        obj.pk,
        obj.endpoint,
    )
    return JsonResponse({"ok": True, "created": created, "uuid": str(obj.uuid)})


@staff_member_required
@require_POST
def push_unregister(request: HttpRequest) -> HttpResponse:
    """Hard-delete the PushSubscription matching the supplied endpoint."""
    data = _parse_json(request)
    endpoint = data.get("endpoint")
    if not endpoint:
        return JsonResponse({"ok": False, "error": "missing endpoint"}, status=400)
    deleted, _ = PushSubscription.objects.filter(endpoint=endpoint).delete()
    return JsonResponse({"ok": True, "deleted": deleted})


@staff_member_required
@require_POST
def push_test(request: HttpRequest) -> HttpResponse:
    """Enqueue a push notification task for one or all stored subscriptions.

    Body fields (all optional):

    - ``endpoint``  — if present, only enqueue for that one row
    - ``title``     — notification title (default: "Snowdesk")
    - ``body``      — notification body  (default: stub copy)
    - ``url``       — URL to open on click (default: "/")

    Returns ``{"ok": True, "enqueued": N}`` where N is the number of tasks
    queued.  Actual delivery happens asynchronously in the background worker.
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

    count = 0
    for sub in qs:
        enqueue_push(sub, payload)
        count += 1

    return JsonResponse({"ok": True, "enqueued": count})
