"""
analytics/__init__.py — Public surface for server-side event capture.

Provides two callables consumed throughout the codebase:

  track(event, distinct_id, properties)
      Capture a single named event to PostHog.  No-op when
      ``settings.POSTHOG_API_KEY`` is empty (local development, test
      runs, any environment without explicit opt-in).

  alias(distinct_id, alias_id)
      Link two distinct identifiers in PostHog so that pre-auth anonymous
      sessions are joined to the authenticated subscriber identity on
      confirmation.  No-op under the same condition as ``track()``.

Both functions are safe to call on every request path — analytics failures
are caught, logged via ``logger.exception`` (ERROR with traceback), and
never propagate to the caller.  PII
keys (``email``, ``ip``, ``token``, ``credential_id``) are rejected at the
call site by raising ``AnalyticsPIIError`` *before* any network call is
attempted.

The global ``posthog`` module-level client is initialised in
``analytics/apps.py`` ``AppConfig.ready()``; ``track()`` and ``alias()``
call ``posthog.capture`` / ``posthog.alias`` directly so they share the
same client as ``PosthogContextMiddleware`` without any singleton of their
own.
"""

from __future__ import annotations

import logging
from typing import Any

import posthog

from analytics.exceptions import AnalyticsPIIError

logger = logging.getLogger(__name__)

# Keys that must never appear in event properties — contain personally
# identifiable information or credentials.
_PII_KEYS: frozenset[str] = frozenset({"email", "ip", "token", "credential_id"})


def _assert_no_pii(properties: dict[str, object]) -> None:
    """Raise ``AnalyticsPIIError`` when any PII key is present in properties.

    Called unconditionally — even when PostHog is disabled — so tests and
    any future callers cannot accidentally pass PII regardless of the key
    configuration.

    Args:
        properties: The event properties dict to validate.

    Raises:
        AnalyticsPIIError: When any key in ``properties`` is in the PII
            block-list.

    """
    violations = _PII_KEYS & properties.keys()
    if violations:
        raise AnalyticsPIIError(
            f"Event properties contain PII key(s): {sorted(violations)}. "
            "Remove them before calling track()."
        )


def track(
    event: str,
    distinct_id: str,
    properties: dict[str, object] | None = None,
) -> None:
    """Capture one named event to PostHog.

    No-op when ``settings.POSTHOG_API_KEY`` is unset or empty.  Any
    exception raised by the PostHog client is caught and logged at ERROR
    so analytics failures never break user-facing requests.

    Args:
        event: The event name (e.g. ``"subscription_started"``).
        distinct_id: Stable identifier for the actor — ``str(subscriber.pk)``
            for authenticated events, or a session-scoped anonymous UUID for
            pre-auth events.
        properties: Optional dict of event properties.  Must not contain any
            of the PII keys ``email``, ``ip``, ``token``, or
            ``credential_id``.

    Raises:
        AnalyticsPIIError: Immediately (before any network call) if
            ``properties`` contains a PII key.

    """
    props: dict[str, Any] = properties or {}
    _assert_no_pii(props)

    from django.conf import settings

    api_key: str = (getattr(settings, "POSTHOG_API_KEY", "") or "").strip()
    if not api_key:
        logger.debug(
            "analytics.track no-op (POSTHOG_API_KEY unset): event=%s distinct_id=%s",
            event,
            distinct_id,
        )
        return

    try:
        posthog.capture(event=event, distinct_id=distinct_id, properties=props)
        logger.debug("analytics.track: event=%s distinct_id=%s", event, distinct_id)
    except Exception:  # noqa: BLE001 — analytics must never break requests
        logger.exception(
            "analytics.track failed: event=%s distinct_id=%s",
            event,
            distinct_id,
        )


def alias(distinct_id: str, alias_id: str) -> None:
    """Link two distinct identifiers in PostHog.

    Called once at ``subscription_confirmed`` to join the pre-auth anonymous
    UUID (stored in the session) to the authenticated subscriber's PK so
    that the pre-confirmation ``subscription_started`` event and all
    subsequent authenticated events appear under a single user profile.

    No-op when ``settings.POSTHOG_API_KEY`` is unset or empty.  Exceptions
    are caught and logged at ERROR.

    Args:
        distinct_id: The canonical, permanent identifier (``str(subscriber.pk)``).
        alias_id: The temporary alias to merge (anonymous session UUID).

    """
    from django.conf import settings

    api_key: str = (getattr(settings, "POSTHOG_API_KEY", "") or "").strip()
    if not api_key:
        logger.debug(
            "analytics.alias no-op (POSTHOG_API_KEY unset): distinct_id=%s alias_id=%s",
            distinct_id,
            alias_id,
        )
        return

    try:
        # PostHog alias: previous_id is the alias we want to merge; distinct_id
        # is the canonical identity to merge it into.
        posthog.alias(previous_id=alias_id, distinct_id=distinct_id)
        logger.debug(
            "analytics.alias: distinct_id=%s alias_id=%s", distinct_id, alias_id
        )
    except Exception:  # noqa: BLE001 — analytics must never break requests
        logger.exception(
            "analytics.alias failed: distinct_id=%s alias_id=%s",
            distinct_id,
            alias_id,
        )
