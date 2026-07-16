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
from typing import cast

from django.conf import settings
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


def _error(status: int, code: str) -> JsonResponse:
    """Return a JSON error response with a stable shape.

    Only the short machine-identifier ``code`` is included in the
    response body — never a stringified exception, never a request
    detail — so validation failures cannot leak internal state
    (CodeQL py/stack-trace-exposure). Operators diagnose via the
    server log; the client only needs to know the failure category.

    Args:
        status: HTTP status code.
        code: Short machine identifier for the failure mode.

    Returns:
        JsonResponse with ``{"error": code}``.

    """
    return JsonResponse({"error": code}, status=status)


def _posthog_configured() -> bool:
    """Return True when ``settings.POSTHOG_API_KEY`` is a non-empty string.

    Read at call time (not module import time) so ``@override_settings``
    in tests takes effect immediately — matches the pattern used by
    ``config.settings.base._posthog_request_filter`` and
    ``analytics.signals.emit_server_signal``.
    """
    return bool((getattr(settings, "POSTHOG_API_KEY", "") or "").strip())


def _parse_events(
    request: HttpRequest,
) -> tuple[JsonResponse, None] | tuple[None, list[dict[str, object]]]:
    """Validate content-type, size, JSON body, and envelope schema.

    Returns either ``(error_response, None)`` on the first validation
    failure, or ``(None, events)`` once the request body has been reduced
    to a validated list of envelope dicts. Split out of
    ``telemetry_receive`` purely to keep that view's cyclomatic complexity
    within the project's lint threshold — no behavioural change from the
    inline version.

    Args:
        request: The incoming POST request.

    Returns:
        A ``(error_response, None)`` or ``(None, events)`` pair.

    """
    content_type = (request.content_type or "").lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        return _error(415, "unsupported_media_type"), None

    if len(request.body) > MAX_PAYLOAD_BYTES:
        return _error(413, "payload_too_large"), None

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Full exception detail is logged server-side; the response body
        # carries only the machine code so no stack info flows out.
        logger.info("telemetry invalid_json: %s", exc)
        return _error(400, "invalid_json"), None

    try:
        events = parse_payload(payload)
    except TelemetrySchemaError as exc:
        logger.info("telemetry invalid_envelope: %s", exc)
        return _error(400, "invalid_envelope"), None

    return None, events


# The receiver has no side effect beyond forwarding to PostHog and
# ``navigator.sendBeacon`` — the client-side critical-event fast path —
# cannot attach a CSRF token. The abuse surface is limited to inflating
# the PostHog event count and is mitigated by the per-IP rate limit
# below. Rationale + threat model: docs/telemetry-pipeline.md.
# nosemgrep: python.django.security.audit.csrf-exempt.no-csrf-exempt
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
        204 No Content on success, when rate-limited, or when the
        PostHog API key is unset. 400 / 413 / 415 on malformed input.
        The rate-limit short-circuit runs *before* input validation, so
        a rate-limited request with a bad content-type still gets 204
        (less information leaked to an abuser).

    """
    if getattr(request, "limited", False):
        logger.info("telemetry rate-limited ip=%s", _client_ip(request))
        return HttpResponse(status=204)

    error_response, maybe_events = _parse_events(request)
    if error_response is not None:
        return error_response
    # ``_parse_events`` guarantees a non-None events list whenever
    # error_response is None; the cast narrows for mypy the same way
    # ``ratings()`` elsewhere in the codebase narrows a tuple return.
    events = cast("list[dict[str, object]]", maybe_events)

    # SNOW-384: no PostHog key configured (dev/test, or an unconfigured
    # deploy) — the client still gets its 204 so ``queue:events`` drains
    # normally client-side (telemetry.js's local buffering is a purely
    # client-side concern, unaffected either way); there is simply
    # nothing to forward server-side. Schema validation above already
    # ran; the per-event PII check normally performed inside
    # ``analytics.track()`` is skipped along with the capture call it
    # would have guarded — there is no PostHog send for it to protect
    # once the key is absent.
    if not _posthog_configured():
        logger.debug(
            "telemetry dropped (POSTHOG_API_KEY unset): events=%d", len(events)
        )
        return HttpResponse(status=204)

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
            logger.warning("telemetry pii_property: %s", exc)
            return _error(400, "pii_property")
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
    # ``parse_payload`` already enforces ``properties`` is a dict; cast
    # narrows the type for mypy without a runtime ``assert`` (which ruff
    # S101 rejects in non-test code and Python -O would strip anyway).
    props = cast(dict[str, object], event.get("properties") or {})
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
