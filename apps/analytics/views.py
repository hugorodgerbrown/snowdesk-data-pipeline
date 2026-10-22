"""
apps/analytics/views.py — HTTP views for the analytics app.

Currently exposes one endpoint:

* ``POST /api/telemetry`` — first-party telemetry receiver (spec §16,
  SNOW-381). Accepts a batched or single-event envelope from the
  client-side buffer / ``navigator.sendBeacon``, validates the shape,
  strips server-side IP, and routes each event (SNOW-1182): fivebar goal
  events to fivebar via ``analytics.track()``, ``js.error`` to Sentry, and
  every other event to a structured log line, at WARNING for the failure
  events in ``FAILURE_EVENTS`` and INFO otherwise.

The endpoint is deliberately fire-and-forget: any failure downstream of
validation is logged and swallowed so a downstream outage never causes the
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

from apps import analytics
from apps.accounts.identity import request_identity
from apps.analytics.exceptions import AnalyticsPIIError
from apps.analytics.fivebar import GOAL_EVENTS
from apps.analytics.schema import (
    MAX_PAYLOAD_BYTES,
    TelemetrySchemaError,
    parse_payload,
)
from apps.analytics.signals import format_properties
from apps.core import sentry

logger = logging.getLogger(__name__)

# Client events that report something going wrong. Logged at WARNING so a
# Render log search for ``level=warning telemetry.client`` finds them; every
# other non-goal event logs at INFO.
FAILURE_EVENTS: frozenset[str] = frozenset(
    {
        "pwa.sw.activation_failed",
        "pwa.sw.fetch_undefined",
        "pwa.kill_switch.activated",
        "pwa.mutation.failed_permanent",
        "pwa.push.subscription_lost",
        "pwa.freshness.unsafe",
        "pwa.storage.evicted_probable",
        "map.basemap.record_write_failed",
    }
)


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


def _telemetry_enabled() -> bool:
    """Return True unless ``settings.PWA_TELEMETRY_ENABLED`` is explicitly False.

    The operator-level master switch for the whole PWA telemetry pipeline
    (docs/telemetry-pipeline.md). Read at call time (not import time) so
    ``@override_settings`` in tests takes effect immediately — matches the
    pattern used by ``apps.analytics.signals.emit_server_signal``. Defaults
    to True when unset so an environment that never configured the flag keeps ingesting.
    """
    return bool(getattr(settings, "PWA_TELEMETRY_ENABLED", True))


def _page_for(event_name: str) -> str:
    """Return the fivebar page name for a client telemetry event.

    The receiver sees ``/api/telemetry``, not the page the event happened
    on; every goal event the client sends is a ``map.*`` interaction, so
    those are attributed to ``/map/`` and anything else to ``/``.

    Args:
        event_name: The validated envelope event name.

    Returns:
        The fivebar page name.

    """
    return "/map/" if event_name.startswith("map.") else "/"


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


# The receiver has no side effect beyond forwarding and logging, and
# ``navigator.sendBeacon`` — the client-side critical-event fast path —
# cannot attach a CSRF token. The abuse surface is limited to inflating
# the fivebar goal counts and the logs, and is mitigated by the per-IP rate limit
# below. It no longer extends to *whose* event count: since SNOW-550 the
# distinct_id is derived from ``request.user``, so an unauthenticated
# caller cannot attribute events to somebody else's identity — see
# ``_resolve_distinct_id``. Rationale + threat model:
# docs/telemetry-pipeline.md.
# nosemgrep: python.django.security.audit.csrf-exempt.no-csrf-exempt
@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="60/m", block=False)
def telemetry_receive(request: HttpRequest) -> HttpResponse:
    """Ingest one or more client-side telemetry events and route each one.

    Contract:

    * Body must be ``application/json`` (or a subtype ending in
      ``+json``); other content types get 415.
    * Body must parse as JSON and match the envelope schema in
      ``apps.analytics.schema.parse_payload`` — either
      ``{"events": [envelope, ...]}`` or a single envelope dict for the
      ``sendBeacon`` fast path.
    * Body must be no larger than ``MAX_PAYLOAD_BYTES`` (32 KiB); larger
      payloads get 413 without parsing.
    * Rate-limited per source IP to 60 requests / minute. Excess
      requests are silently dropped with 204 so the client's
      buffer-drain loop does not spiral into retry storms; those events
      go nowhere (log-only).
    * CSRF-exempt because ``sendBeacon`` cannot attach CSRF headers and
      the endpoint has no side effect beyond forwarding and logging. An
      attacker replaying valid envelopes at rate can only inflate the
      fivebar goal counts and the logs — mitigated by the rate limit.
    * Server-side IP address is never captured into an event property,
      and fivebar is sent no IP at all (``apps/analytics/fivebar.py``).
    * The fivebar send runs in the background and logs its own failures
      — the receiver's response is still 204.

    Args:
        request: The incoming POST request.

    Returns:
        204 No Content on success, when rate-limited, when telemetry is
        disabled via ``settings.PWA_TELEMETRY_ENABLED``. 400 / 413 / 415
        on malformed input.
        The rate-limit short-circuit runs *before* input validation, so
        a rate-limited request with a bad content-type still gets 204
        (less information leaked to an abuser).

    """
    if getattr(request, "limited", False):
        logger.info("telemetry rate-limited ip=%s", _client_ip(request))
        return HttpResponse(status=204)

    # Master switch off (e.g. local dev via PWA_TELEMETRY_ENABLED=False):
    # accept and drop. Still 204 — never surface an error to the client,
    # so a shell rendered before the flag flipped drains its local
    # ``queue:events`` cleanly instead of retrying forever. Checked before
    # parsing so a disabled deploy spends nothing on validation.
    if not _telemetry_enabled():
        logger.debug("telemetry dropped (PWA_TELEMETRY_ENABLED false)")
        return HttpResponse(status=204)

    error_response, maybe_events = _parse_events(request)
    if error_response is not None:
        return error_response
    # ``_parse_events`` guarantees a non-None events list whenever
    # error_response is None; the cast narrows for mypy the same way
    # ``ratings()`` elsewhere in the codebase narrows a tuple return.
    events = cast("list[dict[str, object]]", maybe_events)

    # SNOW-1181: browser errors go to Sentry before anything below can drop
    # the batch — error reporting does not depend on an analytics key.
    _forward_js_errors(events)

    forwarded = 0
    for event in events:
        distinct_id = _resolve_distinct_id(request, event)
        properties = _build_properties(event)
        name = str(event["event"])
        try:
            # Runs the PII guard for every event, goal or not; sends only
            # the goals (fivebar.GOAL_EVENTS) and only when fivebar is set.
            analytics.track(name, distinct_id, properties, page=_page_for(name))
        except AnalyticsPIIError as exc:
            # Client attempted to send a PII-tainted property. Reject the
            # whole batch so the caller notices and stops shipping it —
            # partial acceptance would silently drop data.
            logger.warning("telemetry pii_property: %s", exc)
            return _error(400, "pii_property")
        if name not in GOAL_EVENTS and name != "js.error":
            _log_event(name, properties)
        forwarded += 1

    logger.debug("telemetry handled events=%d", forwarded)
    return HttpResponse(status=204)


def _log_event(name: str, properties: dict[str, object]) -> None:
    """Write one non-goal client event as a structured log line (SNOW-1182).

    ``telemetry.client event=<name> k=v …`` at WARNING for
    ``FAILURE_EVENTS``, INFO otherwise, so the PWA's operational signals
    stay searchable in Render's logs now that PostHog is gone. No identity
    is logged; ``client_timestamp`` is dropped as noise.

    Args:
        name: The validated event name.
        properties: The merged properties from ``_build_properties``.

    """
    fields = {k: v for k, v in properties.items() if k != "client_timestamp"}
    level = logging.WARNING if name in FAILURE_EVENTS else logging.INFO
    logger.log(level, "telemetry.client event=%s %s", name, format_properties(fields))


def _forward_js_errors(events: list[dict[str, object]]) -> None:
    """Report each ``js.error`` envelope in a batch to Sentry (SNOW-1181).

    A no-op for every other event, and for all of them when Sentry is not
    initialised (``capture_js_error`` then sends nothing).

    Args:
        events: The validated envelopes of one request.

    """
    for event in events:
        if event["event"] == "js.error":
            sentry.capture_js_error(
                cast("dict[str, object]", event.get("properties") or {}),
                client_version=str(event["client_version"]),
                platform=cast("str | None", event.get("platform")),
            )


def _resolve_distinct_id(request: HttpRequest, event: dict[str, object]) -> str:
    """Return the distinct_id (fivebar's ``id``) for one event.

    Resolution order:

    1. The **server-derived** identity of the requester, when
       authenticated — ``Account.uuid`` via ``apps.accounts.identity``
       (SNOW-549), following the same read-it-off-``request.user``
       pattern as ``apps.core.idempotency._principal``.
    2. ``session_id`` from the envelope, if present and non-empty.
    3. Synthetic ``_anon`` sentinel when the session id is stripped too
       (opt-out critical events).

    The envelope's own ``user_id`` is **never** used (SNOW-550). This
    endpoint is deliberately CSRF-exempt and unauthenticated — see the
    rationale on ``telemetry_receive`` — so taking the client's word for
    who it is meant any POST with ``{"user_id": "…"}`` and an allowed
    event name could attribute events to somebody else's
    identity. The 60/minute rate
    limit bounds request rate, not identity. Spec §16.6 describes
    ``user_id`` winning over ``session_id``; that preference is
    superseded here because the server can derive the same value
    trustworthily.

    An anonymous request never resolves to a client-asserted identity,
    even when the envelope carries a ``user_id``.

    A stable string is required — fivebar refuses an event with neither
    an ``id`` nor an ``ip``, and no ``ip`` is ever sent.

    Args:
        request: The incoming request, for the server-derived identity.
        event: A validated event dict.

    Returns:
        The distinct_id string.

    """
    identity = request_identity(request)
    if identity:
        return identity
    session_id = event.get("session_id")
    if isinstance(session_id, str) and session_id:
        return session_id
    return "_anon"


def _build_properties(event: dict[str, object]) -> dict[str, object]:
    """Assemble the ``properties`` dict for one envelope.

    Lifts the envelope-level context fields (``client_version``,
    ``platform``, ``install_state``, ``sw_state``, ``connection``,
    ``timestamp``) into the properties dict alongside the caller-
    supplied ``properties``. The server never inserts the source IP.

    Args:
        event: A validated event dict.

    Returns:
        The merged properties dict for ``analytics.track`` and the log line.

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
    # Preserve the client-supplied timestamp as an explicit property for
    # cross-checking clock skew. (fivebar and the log line both drop it.)
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
