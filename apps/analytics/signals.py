"""
apps/analytics/signals.py — Server-originated PWA telemetry signals.

Spec §16.2 defines five events that the server itself emits, rather than
routing them through the client-side buffer at ``/api/telemetry``, so they
cannot be silenced by a broken client or a user opting out of anonymous
usage data:

* ``pwa.version.endpoint.hit``  — client fetched ``/api/version``
* ``pwa.sw_config.hit``         — client fetched ``/api/sw-config``
* ``pwa.push.sent``             — server successfully pushed to a subscription
* ``pwa.push.gone_410``         — push service returned 410 Gone
* ``pwa.idempotency.replay``    — Idempotency-Key middleware served a cached response

Each call site is a one-liner: ``emit_server_signal(name, {...})``.

SNOW-1182: these went to PostHog until PostHog was removed. They are now a
structured INFO log line — ``telemetry.signal event=<name> k=v …`` — so
they are searchable in Render's logs. They are deliberately not fivebar
goals: they are operational, two of them fire on every client poll, and
fivebar bills every event.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def format_properties(properties: dict[str, object]) -> str:
    """Render event properties as ``k=v`` pairs for a log line.

    Sorted, so one event always logs its fields in the same order and a
    log search for ``status=410`` reads the same on every line.

    Args:
        properties: The event properties.

    Returns:
        The space-separated ``k=v`` pairs (empty for no properties).

    """
    return " ".join(f"{key}={properties[key]}" for key in sorted(properties))


def emit_server_signal(event: str, properties: dict[str, object] | None = None) -> None:
    """Log one server-originated PWA signal.

    No-op when ``settings.PWA_TELEMETRY_ENABLED`` is False — the operator
    master switch for the whole telemetry pipeline
    (docs/telemetry-pipeline.md).

    Args:
        event: The event name — one of the spec §16.2 signals listed in
            this module's docstring. Not enforced here (adding a new one
            should not require touching this helper).
        properties: Optional event properties.

    """
    # Deferred import: reading django.conf.settings at call time (not import
    # time) means @override_settings in tests takes effect immediately.
    from django.conf import settings  # noqa: PLC0415 — intentional late import

    if not bool(getattr(settings, "PWA_TELEMETRY_ENABLED", True)):
        logger.debug(
            "emit_server_signal no-op (PWA_TELEMETRY_ENABLED false): event=%s", event
        )
        return
    logger.info(
        "telemetry.signal event=%s %s", event, format_properties(properties or {})
    )
