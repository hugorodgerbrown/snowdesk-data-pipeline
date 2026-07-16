"""
analytics/views.py — HTTP views for the analytics app.

Currently exposes one endpoint:

* ``POST /api/telemetry`` — first-party telemetry receiver (spec §16,
  SNOW-381). Accepts a batched or single-event envelope from the
  client-side buffer / ``navigator.sendBeacon``, validates the shape,
  strips server-side IP, and forwards each event to PostHog via
  ``analytics.track()``.

The endpoint is deliberately fire-and-forget: any failure downstream of
validation is logged and swallowed so a PostHog outage never causes the
client's ``sendBeacon`` retry timer to spin. Success is always
``204 No Content``; the client does not need a body, and returning one
would waste bytes on every batch flush.
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

import analytics
from analytics.exceptions import AnalyticsPIIError
from analytics.schema import (
    MAX_PAYLOAD_BYTES,
    TelemetrySchemaError,
    parse_payload,
)

logger = logging.getLogger(__name__)


def _error(status: int, code: str, detail: str) -> JsonResponse:
    """Return a JSON error response with a stable shape.

    Args:
        status: HTTP status code.
        code: Short machine identifier for the failure mode.
        detail: Human-readable reason (safe to log; no PII).

    Returns:
        JsonResponse with ``{"error": code, "detail": detail}``.

    """
    return JsonResponse({"error": code, "detail": detail}, status=status)


@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="60/m", block=False)
def telemetry_receive(request: HttpRequest) -> HttpResponse:
    """Ingest one or more client-side telemetry events and forward to PostHog.

    Contract:

    * Body must be ``application/json`` (or a subtype ending in
      ``+json``); other content types get 415.
    * Body must parse as JSON and match the envelope schema in
      ``analytics.schema.parse_payload`` — either
      ``{"events": [envelope, ...]}`` or a single envelope dict for the
      ``sendBeacon`` fast path.
    * Body must be no larger than ``MAX_PAYLOAD_BYTES`` (32 KiB); larger
      payloads get 413 without parsing.
    * Rate-limited per source IP to 60 requests / minute. Excess
      requests are silently dropped with 204 so the client's
      buffer-drain loop does not spiral into retry storms; PostHog will
      not receive those events (log-only).
    * CSRF-exempt because ``sendBeacon`` cannot attach CSRF headers and
      the endpoint has no side effect beyond forwarding to PostHog. An
      attacker replaying valid envelopes at rate can only inflate the
      PostHog event count — mitigated by the rate limit.
    * Server-side IP address is never captured into an event property;
      PostHog's own IP-derived GeoIP enrichment continues to run.
    * Any exception raised by PostHog during forwarding is caught by
      ``analytics.track()`` — the receiver's response is still 204.

    Args:
        request: The incoming POST request.

    Returns:
        204 No Content on success or when rate-limited; 400 / 413 / 415
        on malformed input.

    """
    content_type = (request.content_type or "").lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        return _error(415, "unsupported_media_type", "expected application/json")

    if len(request.body) > MAX_PAYLOAD_BYTES:
        return _error(413, "payload_too_large", f"max {MAX_PAYLOAD_BYTES} bytes")

    if getattr(request, "limited", False):
        logger.info("telemetry rate-limited ip=%s", _client_ip(request))
        return HttpResponse(status=204)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _error(400, "invalid_json", str(exc))

    try:
        events = parse_payload(payload)
    except TelemetrySchemaError as exc:
        return _error(400, "invalid_envelope", str(exc))

    forwarded = 0
    for event in events:
        distinct_id = _resolve_distinct_id(event)
        properties = _build_properties(event)
        try:
            analytics.track(str(event["event"]), distinct_id, properties)
        except AnalyticsPIIError as exc:
            # Client attempted to send a PII-tainted property. Reject the
            # whole batch so the caller notices and stops shipping it —
            # partial acceptance would silently drop data.
            return _error(400, "pii_property", str(exc))
        forwarded += 1

    logger.debug("telemetry forwarded events=%d", forwarded)
    return HttpResponse(status=204)


def _resolve_distinct_id(event: dict[str, object]) -> str:
    """Return the PostHog distinct_id for one event.

    Preference order matches spec §16.6:

    1. ``user_id`` if present and non-empty.
    2. ``session_id`` if present and non-empty.
    3. Synthetic ``_anon`` sentinel when both are stripped (opt-out
       critical events).

    A stable string is required — PostHog rejects captures with an
    empty ``distinct_id``.

    Args:
        event: A validated event dict.

    Returns:
        The distinct_id string.

    """
    user_id = event.get("user_id")
    if isinstance(user_id, str) and user_id:
        return user_id
    session_id = event.get("session_id")
    if isinstance(session_id, str) and session_id:
        return session_id
    return "_anon"


def _build_properties(event: dict[str, object]) -> dict[str, object]:
    """Assemble the PostHog ``properties`` dict from one envelope.

    Lifts the envelope-level context fields (``client_version``,
    ``platform``, ``install_state``, ``sw_state``, ``connection``,
    ``timestamp``) into the properties dict alongside the caller-
    supplied ``properties`` so they are queryable in PostHog. Server
    never inserts the source IP; PostHog's GeoIP path derives country
    and city from the request context without seeing the raw value.

    Args:
        event: A validated event dict.

    Returns:
        The merged properties dict for ``posthog.capture``.

    """
    props = event.get("properties") or {}
    assert isinstance(props, dict)  # already validated by parse_payload
    base: dict[str, object] = dict(props)
    for key in (
        "client_version",
        "platform",
        "install_state",
        "sw_state",
        "connection",
    ):
        value = event.get(key)
        if value is not None:
            base[key] = value
    # Preserve the client-supplied timestamp as an explicit property.
    # PostHog derives its own event time from the ``$time`` key when set,
    # but we store the original string too for cross-checking clock
    # skew.
    base["client_timestamp"] = event["timestamp"]
    return base


def _client_ip(request: HttpRequest) -> str:
    """Return the client IP for local logging only.

    Never fed into event properties — spec §16 forbids IP capture. The
    return value is used exclusively in a rate-limit log line so an
    operator can identify a hot source.

    Args:
        request: The incoming request.

    Returns:
        The remote address string, or ``"unknown"`` when unset.

    """
    value = request.META.get("REMOTE_ADDR", "unknown")
    return str(value)
