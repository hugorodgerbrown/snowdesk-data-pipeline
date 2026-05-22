"""
subscriptions/push_service.py — Wrap pywebpush for the spike.

Single function ``dispatch_push(sub, payload)`` that encrypts and POSTs the
payload to the push service URL stored on a PushSubscription. Returns a
small status dict; never raises — callers iterate over many rows and want
per-row outcomes, not a halt on the first 410 Gone.

If the push service returns 404 or 410, the subscription is dead (user
removed permission, uninstalled the PWA, cleared site data). We drop the
DB row in that case so the next test run doesn't keep paying for it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

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
            logger.info("dropping dead push subscription %s (%s)", sub, status)
            sub.delete()
        logger.warning("webpush failed for %s: %s", sub, exc)
        return {"ok": False, "status": status, "error": str(exc)}
    return {"ok": True, "status": response.status_code}
