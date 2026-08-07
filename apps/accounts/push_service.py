"""
apps/accounts/push_service.py — Wrap pywebpush for the spike.

Single function ``dispatch_push(sub, payload, client_version="")`` that
encrypts and POSTs the payload to the push service URL stored on a
PushSubscription. Returns a small status dict; never raises — callers
iterate over many rows and want per-row outcomes, not a halt on the first
410 Gone. ``client_version`` is threaded through onto the server-side
``pwa.push.sent`` / ``pwa.push.gone_410`` telemetry signals so PostHog's
version-distribution and push-health dashboards can filter by client build
(SNOW-384); the value comes from the ``X-Client-Version`` request header
in the caller's context.

404 vs 410 (SNOW-380, spec §8): a 404 means the endpoint URL itself is
wrong (a rare transport-layer error) and the row is hard-deleted. A 410
means the push service has confirmed the subscription is gone (permission
revoked, PWA uninstalled, site data cleared) — the row is *soft*-deleted
by setting ``inactive_at`` so the client-side re-verification loop
(``static/js/push_demo.js::reverifyPushSubscription``) has a record to
reconcile against on next launch; a successful resubscribe overwrites the
same endpoint (or creates a fresh row for a new one) via
``update_or_create`` in ``push_register``.

The outgoing wire payload also branches on ``sub.mechanism``: the ``SW``
path (every browser today) keeps the existing ``{title, body, url}`` shape
that the service worker's ``push`` event handler parses in JS. The
``DECLARATIVE`` path (Apple's Declarative Web Push, iOS 18.4+) requires a
fixed JSON shape the OS renders without running any JS — see
``_build_wire_payload`` and ``docs/push-notifications.md``.

``enqueue_push(sub, payload, client_version="")`` is the async entry
point: it enqueues a ``_worker_dispatch_push`` task via django-tasks so
the actual pywebpush HTTP round-trip runs off the request cycle
(SNOW-319). ``client_version`` is captured at enqueue time and passed
through to the worker so the dispatched telemetry carries the same
value regardless of when the task actually runs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.utils import timezone
from django_tasks import task
from pywebpush import WebPushException, webpush

from apps.accounts.models import PushSubscription
from apps.accounts.push_config import (
    VAPID_CLAIM_EMAIL,
    VAPID_PRIVATE_KEY,
)
from apps.analytics.signals import emit_server_signal

logger = logging.getLogger(__name__)

# Apple's Declarative Web Push version tag — see
# https://webkit.org/blog/16535/meet-declarative-web-push/. Fixed value;
# not expected to change independently of a full payload-shape revision.
_DECLARATIVE_WEB_PUSH_VERSION = 8030


def _build_wire_payload(
    sub: PushSubscription, payload: dict[str, Any]
) -> dict[str, Any]:
    """Return the JSON payload to encrypt, shaped for ``sub.mechanism``.

    ``SW`` subscriptions keep the existing ``{title, body, url}`` shape
    that the service worker's ``push`` event handler expects.

    ``DECLARATIVE`` subscriptions get Apple's Declarative Web Push shape —
    the OS renders the notification directly from this JSON without
    running the service worker, so the keys are fixed by Apple's spec
    rather than by our own JS.
    """
    if sub.mechanism == PushSubscription.Mechanism.DECLARATIVE:
        return {
            "web_push": _DECLARATIVE_WEB_PUSH_VERSION,
            "notification": {
                "title": payload.get("title", "Snowdesk"),
                "body": payload.get("body", ""),
                "navigate": payload.get("url", "/"),
            },
        }
    return payload


def dispatch_push(
    sub: PushSubscription, payload: dict[str, Any], client_version: str = ""
) -> dict[str, Any]:
    """Send one Web Push and return a per-row outcome dict.

    Args:
        sub: The subscription to deliver to.
        payload: Push payload dict (``title``, ``body``, ``url``).
        client_version: SNOW-384 — threaded in from the caller rather than
            read off a request, because dispatch runs inside a background
            task (``_worker_dispatch_push``) with no request in scope.
            Defaults to ``""`` when the caller has no value to pass (e.g.
            a future non-request-triggered push send).

    """
    wire_payload = _build_wire_payload(sub, payload)
    try:
        response = webpush(
            subscription_info=sub.to_dict(),
            data=json.dumps(wire_payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            ttl=60,
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 410:
            # SNOW-380: soft-delete — the client-side re-verification loop
            # reconciles against this row on next launch.
            # SNOW-381 (spec §16.2): 410 Gone is the "subscription lost"
            # signal the observability dashboard reconciles against
            # client-side pwa.push.subscription_lost. Emit before the save
            # so sub.pk is unambiguous in the event payload.
            emit_server_signal(
                "pwa.push.gone_410",
                {"subscription_pk": sub.pk, "client_version": client_version},
            )
            logger.info(
                "marking push subscription pk=%s endpoint=%.30s… inactive (410)",
                sub.pk,
                sub.endpoint,
            )
            sub.inactive_at = timezone.now()
            sub.save(update_fields=["inactive_at"])
        elif status == 404:
            logger.info(
                "dropping dead push subscription pk=%s endpoint=%.30s… (404)",
                sub.pk,
                sub.endpoint,
            )
            sub.delete()
        logger.warning(
            "webpush failed for push subscription pk=%s endpoint=%.30s…: %s",
            sub.pk,
            sub.endpoint,
            exc,
        )
        return {"ok": False, "status": status, "error": str(exc)}
    # SNOW-381 (spec §16.2): server-side half of the push funnel; the
    # client emits pwa.push.received / .shown / .opened as the message
    # progresses.
    emit_server_signal(
        "pwa.push.sent",
        {
            "subscription_pk": sub.pk,
            "status": response.status_code,
            "client_version": client_version,
        },
    )
    return {"ok": True, "status": response.status_code}


# ---------------------------------------------------------------------------
# Worker functions — decorated with @task so django-tasks can enqueue and
# replay them.  Each accepts only JSON-serialisable primitives.
# ---------------------------------------------------------------------------


@task()
def _worker_dispatch_push(
    sub_pk: int, payload: dict[str, Any], client_version: str = ""
) -> None:
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
        client_version: SNOW-384 — carried through from ``enqueue_push``
            (which reads it off the triggering request) since this worker
            has no request of its own.

    """
    try:
        sub = PushSubscription.objects.get(pk=sub_pk)
    except PushSubscription.DoesNotExist:
        logger.info(
            "push subscription pk=%s no longer exists — skipping worker",
            sub_pk,
        )
        return
    dispatch_push(sub, payload, client_version)


# ---------------------------------------------------------------------------
# Public API — thin wrappers that enqueue a worker task.
# ---------------------------------------------------------------------------


def enqueue_push(
    sub: PushSubscription, payload: dict[str, Any], client_version: str = ""
) -> None:
    """
    Enqueue a Web Push delivery for ``sub`` to run off the request cycle.

    The actual pywebpush HTTP round-trip runs inside ``_worker_dispatch_push``
    via django-tasks (``ImmediateBackend`` in dev/test;
    ``django_tasks_db.DatabaseBackend`` in production).

    Args:
        sub: The ``PushSubscription`` to deliver to.
        payload: Push payload dict (``title``, ``body``, ``url``).
        client_version: SNOW-384 — the ``X-Client-Version`` header off the
            request that triggered this send, if any. Defaults to ``""``
            for callers with no request in scope.

    """
    _worker_dispatch_push.enqueue(sub.pk, payload, client_version)
