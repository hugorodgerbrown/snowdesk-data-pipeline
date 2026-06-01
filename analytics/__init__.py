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
are caught, logged at WARNING, and never propagate to the caller.  PII
keys (``email``, ``ip``, ``token``, ``credential_id``) are rejected at the
call site by raising ``AnalyticsPIIError`` *before* any network call is
attempted.

Client initialisation is lazy (one ``posthog.Posthog`` instance per
process, created on first call) so import-time errors do not affect startup.
"""

from __future__ import annotations

import logging
from typing import Any

from analytics.exceptions import AnalyticsPIIError

logger = logging.getLogger(__name__)

# Keys that must never appear in event properties — contain personally
# identifiable information or credentials.
_PII_KEYS: frozenset[str] = frozenset({"email", "ip", "token", "credential_id"})

# Module-level singleton: populated lazily on first call to track() or alias().
_client: Any = None


def _get_client() -> Any:
    """Return the lazily-initialised PostHog client, or None when disabled.

    Reads ``settings.POSTHOG_API_KEY`` and ``settings.POSTHOG_HOST`` at
    call time so that test overrides via ``@override_settings`` take effect.
    Returns ``None`` when the key is absent or empty so callers can
    short-circuit without any network activity.
    """
    global _client  # noqa: PLW0603 — intentional module-level singleton

    from django.conf import settings  # local import to avoid import-time side effects

    api_key: str = (getattr(settings, "POSTHOG_API_KEY", "") or "").strip()
    if not api_key:
        return None

    if _client is None:
        import posthog

        host: str = getattr(settings, "POSTHOG_HOST", "https://eu.posthog.com")
        # disable_geoip=False retains PostHog's server-side GeoIP enrichment
        # (country/city from IP) while we strip the raw IP from the event
        # properties ourselves — PII safeguard prevents the ip key reaching
        # the payload, but PostHog can still derive geo from the inbound
        # request header on its ingestion layer.
        _client = posthog.Posthog(
            project_api_key=api_key,
            host=host,
            disable_geoip=False,
        )
        logger.debug("PostHog client initialised (host=%s)", host)

    return _client


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
    exception raised by the PostHog client is caught and logged at WARNING
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
    props = properties or {}
    _assert_no_pii(props)

    client = _get_client()
    if client is None:
        logger.debug(
            "analytics.track no-op (POSTHOG_API_KEY unset): event=%s distinct_id=%s",
            event,
            distinct_id,
        )
        return

    try:
        client.capture(event=event, distinct_id=distinct_id, properties=props)
        logger.debug("analytics.track: event=%s distinct_id=%s", event, distinct_id)
    except Exception:  # noqa: BLE001 — analytics must never break requests
        logger.warning(
            "analytics.track failed: event=%s distinct_id=%s",
            event,
            distinct_id,
            exc_info=True,
        )


def alias(distinct_id: str, alias_id: str) -> None:
    """Link two distinct identifiers in PostHog.

    Called once at ``subscription_confirmed`` to join the pre-auth anonymous
    UUID (stored in the session) to the authenticated subscriber's PK so
    that the pre-confirmation ``subscription_started`` event and all
    subsequent authenticated events appear under a single user profile.

    No-op when ``settings.POSTHOG_API_KEY`` is unset or empty.  Exceptions
    are caught and logged at WARNING.

    Args:
        distinct_id: The canonical, permanent identifier (``str(subscriber.pk)``).
        alias_id: The temporary alias to merge (anonymous session UUID).

    """
    client = _get_client()
    if client is None:
        logger.debug(
            "analytics.alias no-op (POSTHOG_API_KEY unset): distinct_id=%s alias_id=%s",
            distinct_id,
            alias_id,
        )
        return

    try:
        # PostHog alias: previous_id is the alias we want to merge; distinct_id
        # is the canonical identity to merge it into.
        client.alias(previous_id=alias_id, distinct_id=distinct_id)
        logger.debug(
            "analytics.alias: distinct_id=%s alias_id=%s", distinct_id, alias_id
        )
    except Exception:  # noqa: BLE001 — analytics must never break requests
        logger.warning(
            "analytics.alias failed: distinct_id=%s alias_id=%s",
            distinct_id,
            alias_id,
            exc_info=True,
        )
