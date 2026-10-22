"""
apps/core/sentry.py — Sentry error tracking (SNOW-1181).

Two entry points:

* ``init_sentry(dsn, environment, release)`` — called once from
  ``config/settings/base.py``. A no-op for an empty DSN, so dev and test
  never initialise the SDK. The default Django and logging integrations
  then report every unhandled view exception and every ``logger.error`` /
  ``logger.exception`` — management commands and task workers included,
  which is how a failed bulletin fetch reaches Sentry.
* ``capture_js_error(properties, ...)`` — called by the ``/api/telemetry``
  receiver for each ``js.error`` envelope. The browser never talks to
  Sentry itself: ``static/js/error_reporting.js`` keeps reporting to our
  own endpoint, so there is no third-party script, no CSP change, and
  SNOW-894's redaction still runs before anything leaves the device.

Privacy, by configuration rather than by convention:

* ``send_default_pii=False`` — no user, IP address or cookies. It also
  means the integration never reads ``request.user``, so it never touches
  the session and never adds ``Vary: Cookie`` to a CDN-cacheable response.
* ``include_local_variables=False`` — no frame locals, which can hold
  anything in scope at the crash.
* ``max_request_body_size="never"`` — no POST bodies.
* ``_before_send`` drops the request URL, query string and ``Referer``.
  Account-access and share URLs carry tokens, and a token is a capability;
  the event's transaction is the route pattern, which says where it
  happened without them.

Any Sentry-compatible host (GlitchTip, Better Stack) works by changing
``SENTRY_DSN`` alone.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

logger = logging.getLogger(__name__)


def _before_send(event: Event, hint: Hint) -> Event:
    """Strip the request URL, query string and Referer from an outgoing event.

    Args:
        event: The Sentry event about to be sent.
        hint: Sentry's hint dict (unused).

    Returns:
        The event, without ``request.url``, ``request.query_string`` or a
        ``Referer`` header.

    """
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("url", None)
        request.pop("query_string", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in [key for key in headers if key.lower() == "referer"]:
                del headers[name]
    return event


def init_sentry(dsn: str, environment: str, release: str) -> bool:
    """Initialise the Sentry SDK, or do nothing when ``dsn`` is empty.

    Args:
        dsn: The project DSN; empty or blank leaves the SDK uninitialised.
        environment: The environment label (``SITE_ENVIRONMENT``).
        release: The release identifier (``RELEASE_VERSION``).

    Returns:
        True when the SDK was initialised.

    """
    dsn = (dsn or "").strip()
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        integrations=[DjangoIntegration(transaction_style="url")],
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=_before_send,
    )
    return True


def capture_js_error(
    properties: dict[str, object],
    *,
    client_version: str,
    platform: str | None,
) -> None:
    """Report one browser ``js.error`` envelope to Sentry.

    An opted-in report carries ``message``, ``filename``, ``lineno``,
    ``colno`` and ``stack``; an opted-out one only ``kind`` and
    ``pathname`` (SNOW-894). Issues group on message + filename when there
    is a message, and on pathname + kind otherwise, so an opted-out crash
    still counts against the page it happened on. A no-op when the SDK is
    not initialised.

    Args:
        properties: The envelope's ``properties``, already redacted
            client-side.
        client_version: The envelope's client build.
        platform: The envelope's platform, when present.

    """
    message = str(properties.get("message") or "")
    filename = str(properties.get("filename") or "")
    pathname = str(properties.get("pathname") or "")
    kind = str(properties.get("kind") or "")
    if message:
        fingerprint = ["js.error", message, filename]
    else:
        fingerprint = ["js.error", pathname, kind]
    event: Event = {
        "level": "error",
        "logger": "js.error",
        "message": message or f"JavaScript {kind or 'error'} on {pathname or '?'}",
        "fingerprint": fingerprint,
        "tags": {
            "source": "browser",
            "kind": kind,
            "pathname": pathname,
            "client_version": client_version,
            "platform": platform or "",
        },
        "extra": {
            key: properties[key]
            for key in ("filename", "lineno", "colno", "stack")
            if key in properties
        },
    }
    sentry_sdk.capture_event(event)
