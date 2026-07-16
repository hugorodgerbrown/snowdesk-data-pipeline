"""
analytics/signals.py — Server-originated PWA telemetry signals.

Spec §16.2 defines five events that the server itself emits directly to
PostHog (not routed through the client-side buffer at
``/api/telemetry``) so they cannot be silenced by a broken client or a
user opting out of anonymous usage data:

* ``pwa.version.endpoint.hit``  — client fetched ``/api/version``
* ``pwa.sw_config.hit``         — client fetched ``/api/sw-config``
* ``pwa.push.sent``             — server successfully pushed to a subscription
* ``pwa.push.gone_410``         — push service returned 410 Gone
* ``pwa.idempotency.replay``    — Idempotency-Key middleware served a cached response

Each call site is a one-liner: ``emit_server_signal(name, {...})``. The
helper delegates to ``analytics.track()`` with a fixed distinct_id so
these events are grouped in PostHog under a synthetic "server" actor
rather than mingled with real user identities.

Signals are no-ops when ``POSTHOG_API_KEY`` is empty, and any exception
from the PostHog client is caught by ``analytics.track()`` and logged
without propagating — matching the existing analytics contract that
event capture must never break a request.
"""

from __future__ import annotations

import analytics

# Synthetic distinct_id for events originated by the server rather than
# by a real user. PostHog requires a distinct_id on every capture; using
# a fixed sentinel keeps server-side signals grouped in the UI and
# prevents them from inflating the DAU count.
SERVER_DISTINCT_ID: str = "_server"


def emit_server_signal(event: str, properties: dict[str, object] | None = None) -> None:
    """Capture one server-originated PWA signal to PostHog.

    Args:
        event: The event name — one of the spec §16.2 signals listed in
            this module's docstring. Not enforced here (adding a new one
            should not require touching this helper).
        properties: Optional event properties. Must not contain any of
            the PII keys blocked by ``analytics.track()``.

    """
    analytics.track(event, SERVER_DISTINCT_ID, properties or {})
