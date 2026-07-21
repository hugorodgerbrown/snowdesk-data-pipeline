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

SNOW-384: the empty-key check is duplicated here (``analytics.track()``
already no-ops internally on the same condition) so this module is
self-contained — a caller reading this file doesn't need to trace into
``analytics.track()`` to understand why nothing reaches PostHog in
dev/test. The duplication is deliberate, not drift.
"""

from __future__ import annotations

import logging

import analytics

logger = logging.getLogger(__name__)

# Synthetic distinct_id for events originated by the server rather than
# by a real user. PostHog requires a distinct_id on every capture; using
# a fixed sentinel keeps server-side signals grouped in the UI and
# prevents them from inflating the DAU count.
SERVER_DISTINCT_ID: str = "_server"


def emit_server_signal(event: str, properties: dict[str, object] | None = None) -> None:
    """Capture one server-originated PWA signal to PostHog.

    No-op when ``settings.POSTHOG_API_KEY`` is empty or unset — logged at
    DEBUG rather than silently swallowed, so a dev/test run can confirm
    signals would have fired without needing a real PostHog key.

    Args:
        event: The event name — one of the spec §16.2 signals listed in
            this module's docstring. Not enforced here (adding a new one
            should not require touching this helper).
        properties: Optional event properties. Must not contain any of
            the PII keys blocked by ``analytics.track()``.

    """
    # Deferred import — mirrors the pattern in analytics/__init__.py and
    # config/settings/base.py's _posthog_request_filter: reading
    # django.conf.settings at call time (not import time) means
    # @override_settings in tests takes effect immediately.
    from django.conf import settings  # noqa: PLC0415 — intentional late import

    # Operator-level master switch — silences the server-emitted §16.2
    # signals along with the rest of the pipeline (docs/telemetry-pipeline.md).
    # Checked before the key so PWA_TELEMETRY_ENABLED=False is a full off
    # switch even where POSTHOG_API_KEY is populated.
    if not bool(getattr(settings, "PWA_TELEMETRY_ENABLED", True)):
        logger.debug(
            "emit_server_signal no-op (PWA_TELEMETRY_ENABLED false): event=%s", event
        )
        return

    api_key: str = (getattr(settings, "POSTHOG_API_KEY", "") or "").strip()
    if not api_key:
        logger.debug(
            "emit_server_signal no-op (POSTHOG_API_KEY unset): event=%s", event
        )
        return
    analytics.track(event, SERVER_DISTINCT_ID, properties or {})
