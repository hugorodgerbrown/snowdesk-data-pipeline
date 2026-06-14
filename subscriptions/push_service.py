"""
subscriptions/push_service.py — Wrap pywebpush for the spike.

Single function ``dispatch_push(sub, payload)`` that encrypts and POSTs the
payload to the push service URL stored on a PushSubscription. Returns a
small status dict; never raises — callers iterate over many rows and want
per-row outcomes, not a halt on the first 410 Gone.

If the push service returns 404 or 410, the subscription is dead (user
removed permission, uninstalled the PWA, cleared site data). We drop the
DB row in that case so the next test run doesn't keep paying for it.

``enqueue_push(sub, payload)`` is the async entry point: it enqueues a
``_worker_dispatch_push`` task via django-tasks so the actual pywebpush
HTTP round-trip runs off the request cycle (SNOW-319).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django_tasks import task
from pywebpush import WebPushException, webpush

from subscriptions.models import PushSubscription
from subscriptions.push_config import (
    VAPID_CLAIM_EMAIL,
    VAPID_PRIVATE_KEY,
)

logger = logging.getLogger(__name__)


def dispatch_push(sub: PushSubscription, payload: dict[str, Any]) -> dict[str, Any]:
    """Send one Web Push and return a per-row outcome dict."""
    try:
        response = webpush(
            subscription_info=sub.to_dict(),
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            ttl=60,
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in {404, 410}:
            logger.info(
                "dropping dead push subscription pk=%s endpoint=%.30s… (%s)",
                sub.pk,
                sub.endpoint,
                status,
            )
            sub.delete()
        logger.warning(
            "webpush failed for push subscription pk=%s endpoint=%.30s…: %s",
            sub.pk,
            sub.endpoint,
            exc,
        )
        return {"ok": False, "status": status, "error": str(exc)}
    return {"ok": True, "status": response.status_code}


# ---------------------------------------------------------------------------
# Worker functions — decorated with @task so django-tasks can enqueue and
# replay them.  Each accepts only JSON-serialisable primitives.
# ---------------------------------------------------------------------------


@task()
def _worker_dispatch_push(sub_pk: int, payload: dict[str, Any]) -> None:
    """
    Background worker: load a PushSubscription by PK and dispatch the push.

    Accepts only JSON-serialisable primitives so the task can be serialised
    into the DB and replayed on retry.

    If the subscription row no longer exists (e.g. deleted by a prior 410
    handler between enqueue and execution), the worker exits silently after
    logging at INFO — this is an expected race and not a failure.

    ``dispatch_push`` handles all pywebpush errors internally and never
    raises; unexpected errors surface as a failed ``DbTaskResult`` in the
    django-tasks admin.

    Args:
        sub_pk: Primary key of the ``PushSubscription`` to deliver to.
        payload: Push payload dict (``title``, ``body``, ``url``).

    """
    try:
        sub = PushSubscription.objects.get(pk=sub_pk)
    except PushSubscription.DoesNotExist:
        logger.info(
            "push subscription pk=%s no longer exists — skipping worker",
            sub_pk,
        )
        return
    dispatch_push(sub, payload)


# ---------------------------------------------------------------------------
# Public API — thin wrappers that enqueue a worker task.
# ---------------------------------------------------------------------------


def enqueue_push(sub: PushSubscription, payload: dict[str, Any]) -> None:
    """
    Enqueue a Web Push delivery for ``sub`` to run off the request cycle.

    The actual pywebpush HTTP round-trip runs inside ``_worker_dispatch_push``
    via django-tasks (``ImmediateBackend`` in dev/test;
    ``django_tasks_db.DatabaseBackend`` in production).

    Args:
        sub: The ``PushSubscription`` to deliver to.
        payload: Push payload dict (``title``, ``body``, ``url``).

    """
    _worker_dispatch_push.enqueue(sub.pk, payload)
