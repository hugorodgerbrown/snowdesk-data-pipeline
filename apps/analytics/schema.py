"""
apps/analytics/schema.py — Envelope validation for /api/telemetry payloads.

Spec §16 (offline-first PWA compliance) defines the eight-field event
envelope that the client-side telemetry buffer POSTs to
``/api/telemetry`` — batched or single. This module validates that shape
before the receiver forwards to PostHog.

The validator is deliberately strict: unknown top-level keys are rejected
so a mistyped field never silently disappears into a PostHog property
where it becomes an operational puzzle three weeks later. Unknown
``properties`` sub-keys pass through — the caller controls its own
event-specific payload shape.

Allowed event names are declared here as a frozenset so the receiver can
reject typos at the boundary without shipping garbage to PostHog. Add a
new event name here and in ``docs/telemetry-pipeline.md`` when
extending the catalogue.
"""

from __future__ import annotations

from datetime import datetime

# The eight envelope keys per spec §16.1. ``event``, ``timestamp`` and
# ``client_version`` are always required; the remaining five may be
# ``None`` (spec §16.6 — critical events fire even when the user opts
# out of anonymous usage data, but ``user_id`` / ``session_id`` are
# stripped in that case).
REQUIRED_KEYS: frozenset[str] = frozenset({"event", "timestamp", "client_version"})
OPTIONAL_KEYS: frozenset[str] = frozenset(
    {
        "session_id",
        "user_id",
        "platform",
        "install_state",
        "sw_state",
        "connection",
        "properties",
    }
)
ALLOWED_KEYS: frozenset[str] = REQUIRED_KEYS | OPTIONAL_KEYS

# Client-emitted event catalogue. Kept in one place so the receiver can
# reject unknown names at the boundary — every entry here must also be
# documented in ``docs/telemetry-pipeline.md``. Server-originated events
# use a separate namespace (``pwa.*`` emitted directly via
# ``apps.analytics.signals.emit_server_signal``) and are NOT in this
# allowlist.
ALLOWED_EVENTS: frozenset[str] = frozenset(
    {
        # Service-worker lifecycle (spec §5.10, §16.2).
        "pwa.sw.installed",
        "pwa.sw.activated",
        "pwa.sw.activation_failed",
        "pwa.sw.update_available",
        "pwa.sw.update_applied",
        "pwa.sw.fetch_undefined",
        # Kill switch + forced update (spec §6, §16.2).
        "pwa.kill_switch.activated",
        "pwa.forced_update.triggered",
        # Install funnel (spec §11, §16.4 dashboard 3).
        "pwa.install.prompted",
        "pwa.install.accepted",
        "pwa.install.dismissed",
        "pwa.install.completed",
        "pwa.install.notifications_granted",
        # Push delivery — client half of the funnel (server half emits
        # ``pwa.push.sent`` / ``pwa.push.gone_410`` directly via
        # ``apps.analytics.signals``).
        "pwa.push.received",
        "pwa.push.shown",
        "pwa.push.opened",
        "pwa.push.subscription_lost",
        # Mutation queue (spec §7, §16.4 dashboard 5).
        "pwa.mutation.enqueued",
        "pwa.mutation.drained",
        "pwa.mutation.failed_permanent",
        # SNOW-462: a queued row discarded, never replayed, because its
        # stamped principal no longer matches the current one — either an
        # account change (reason: account_change, whole-queue clear) or a
        # single row caught by the drain-guard (reason:
        # principal_mismatch).
        "pwa.mutation.discarded",
        # Reset flow (spec §3.10, §16.4 dashboard 8).
        "pwa.reset.user_initiated",
        "pwa.reset.forced",
        # Freshness classifier (spec §12.6, §16.4 dashboard 6).
        "pwa.freshness.fresh",
        "pwa.freshness.stale",
        "pwa.freshness.unsafe",
        # Storage-pressure signal (spec §16.4 dashboard 8 baseline).
        "pwa.storage.evicted_probable",
        # Map favourites (SNOW-414) — deliberately a `map.*` namespace, not
        # `pwa.*`, since these describe a map-surface interaction rather
        # than PWA shell lifecycle. Emitted by static/js/favourites.js and
        # static/js/map.js's overlay-toggle handler.
        "map.favourite.created",
        "map.favourite.deleted",
        "map.favourite.overlay_toggled",
        # Community reports overlay (SNOW-419) — anonymised, clustered
        # FieldObservation pins on /map/. Emitted by static/js/map.js's
        # overlay-toggle handler and the community-reports-point click
        # handler. Deliberately no location/identity data on the marker-
        # tapped event — see the geojson endpoint's anonymisation contract.
        "map.community_reports.overlay_toggled",
        "map.community_reports.marker_tapped",
        # SNOW-612: a completed basemap download whose `basemap.regions`
        # record could not be written. Emitted by static/js/map.js's
        # `_recordRegionDownload`, whose failure used to be swallowed
        # entirely — the run leaves a pinned Cache Storage bucket behind
        # with nothing recording it, which then reads as an orphan. The
        # bucket is now reconciled and deletable either way; this is how
        # often it happens.
        "map.basemap.record_write_failed",
    }
)

# Maximum payload size accepted at the receiver, in bytes. Chosen to fit
# a healthy batch of ~50 events with generous properties on each without
# opening the door to abuse. sendBeacon on modern browsers imposes a
# separate ~64 KB cap client-side, so anything larger than this is
# certainly not a real telemetry POST.
MAX_PAYLOAD_BYTES: int = 32 * 1024


class TelemetrySchemaError(ValueError):
    """Raised when an incoming telemetry envelope is malformed.

    Inherits from ``ValueError`` so callers that catch broad exception
    types in tests can assert the specific type without importing this
    class. The receiver maps this to HTTP 400.
    """


def _check_keys(event: dict[str, object], index: int) -> None:
    """Reject unknown or missing top-level keys on one event."""
    unknown = event.keys() - ALLOWED_KEYS
    if unknown:
        raise TelemetrySchemaError(f"event[{index}]: unknown key(s) {sorted(unknown)}")
    missing = REQUIRED_KEYS - event.keys()
    if missing:
        raise TelemetrySchemaError(
            f"event[{index}]: missing required key(s) {sorted(missing)}"
        )


def _check_timestamp(event: dict[str, object], index: int) -> None:
    """Reject a timestamp that isn't a parseable ISO-8601 string."""
    timestamp = event["timestamp"]
    if not isinstance(timestamp, str):
        raise TelemetrySchemaError(f"event[{index}]: timestamp must be a string")
    try:
        # ``fromisoformat`` accepts ``Z`` suffix in Python 3.11+ and every
        # supported production runtime here is 3.14. Reject strings that
        # aren't parseable so a bad clock isn't silently forwarded.
        datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise TelemetrySchemaError(
            f"event[{index}]: timestamp {timestamp!r} not ISO-8601"
        ) from exc


def _validate_event(event: object, index: int) -> dict[str, object]:
    """Validate one event dict from the incoming payload.

    Args:
        event: The candidate event object — must be a dict.
        index: Zero-based position in the batch, for error messages.

    Returns:
        The validated event dict (same object, unmodified).

    Raises:
        TelemetrySchemaError: On any schema violation.

    """
    if not isinstance(event, dict):
        raise TelemetrySchemaError(f"event[{index}]: not an object")

    _check_keys(event, index)
    _check_timestamp(event, index)

    name = event["event"]
    if not isinstance(name, str) or name not in ALLOWED_EVENTS:
        raise TelemetrySchemaError(f"event[{index}]: unknown event name {name!r}")

    client_version = event["client_version"]
    if not isinstance(client_version, str) or not client_version:
        raise TelemetrySchemaError(
            f"event[{index}]: client_version must be a non-empty string"
        )

    properties = event.get("properties", {})
    if not isinstance(properties, dict):
        raise TelemetrySchemaError(f"event[{index}]: properties must be an object")

    return event


def parse_payload(payload: object) -> list[dict[str, object]]:
    """Validate the top-level payload shape and return the event list.

    The receiver accepts two shapes:

    * ``{"events": [envelope, ...]}`` — batched, the normal case.
    * A single envelope dict — used by ``sendBeacon`` for critical
      events (spec §16.3) where the client doesn't want to buffer.

    Args:
        payload: The JSON-decoded request body.

    Returns:
        A list of validated event dicts, ready to forward to PostHog.

    Raises:
        TelemetrySchemaError: On any structural or per-event violation.

    """
    if isinstance(payload, dict) and "events" in payload:
        events = payload["events"]
        if not isinstance(events, list) or not events:
            raise TelemetrySchemaError("'events' must be a non-empty list")
        return [_validate_event(e, i) for i, e in enumerate(events)]

    if isinstance(payload, dict):
        # Single envelope — sendBeacon fast path.
        return [_validate_event(payload, 0)]

    raise TelemetrySchemaError("payload must be a JSON object")
