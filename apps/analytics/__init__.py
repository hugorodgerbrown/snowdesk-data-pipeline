"""
apps/analytics/__init__.py — Public surface for server-side event capture.

Provides one callable consumed throughout the codebase:

  track(event, distinct_id, properties, page=...)
      Send a single named event to fivebar (``apps/analytics/fivebar.py``,
      SNOW-1180) when it is one of fivebar's goals.  A no-op when fivebar
      is not configured (local development, test runs, any environment
      without explicit opt-in).

Safe to call on every request path — the send happens on a background
thread, and its failures are logged, never raised.  PII keys (``email``,
``ip``, ``token``, ``credential_id``) are rejected at the call site by
raising ``AnalyticsPIIError`` *before* anything is sent.

SNOW-1182 removed PostHog, and with it ``alias()``: fivebar keeps no
person profiles, so there is no anonymous identity to join to an account.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.analytics import fivebar
from apps.analytics.exceptions import AnalyticsPIIError

logger = logging.getLogger(__name__)

# Re-exported so call sites name the page with ``analytics.page_of(request)``
# without importing the fivebar module themselves.
page_of = fivebar.page_of

# Keys that must never appear in event properties — contain personally
# identifiable information or credentials.
_PII_KEYS: frozenset[str] = frozenset({"email", "ip", "token", "credential_id"})


def _assert_no_pii(properties: dict[str, object]) -> None:
    """Raise ``AnalyticsPIIError`` when any PII key is present in properties.

    Called unconditionally — even when fivebar is not configured — so tests
    and any future callers cannot accidentally pass PII regardless of the
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
    *,
    page: str = "/",
) -> None:
    """Send one named event to fivebar, if it is a goal.

    The send is a no-op unless the event is in ``fivebar.GOAL_EVENTS`` and
    fivebar is configured.

    Args:
        event: The event name (e.g. ``"region_added"``).
        distinct_id: Stable identifier for the actor — ``Account.uuid`` for
            authenticated events, or a session-scoped id for anonymous ones.
        properties: Optional dict of event properties.  Must not contain any
            of the PII keys ``email``, ``ip``, ``token``, or
            ``credential_id``.
        page: The route pattern the event happened on, from
            ``analytics.page_of(request)``.

    Raises:
        AnalyticsPIIError: Immediately (before anything is sent) if
            ``properties`` contains a PII key.

    """
    props: dict[str, Any] = properties or {}
    _assert_no_pii(props)
    fivebar.send(event, distinct_id, props, page)
    logger.debug("analytics.track: event=%s distinct_id=%s", event, distinct_id)
