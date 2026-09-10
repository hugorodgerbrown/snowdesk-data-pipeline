"""
tests/analytics/test_schema.py — Tests for apps.analytics.schema.ALLOWED_EVENTS.

Covers the SNOW-414 map-favourites allowlist addition: the three
``map.favourite.*`` events must be members of ``ALLOWED_EVENTS`` (so the
receiver forwards them rather than 400-ing), and a well-formed envelope
carrying one of them passes ``parse_payload`` end-to-end. Also covers the
SNOW-419 ``map.community_reports.*`` allowlist addition, and the SNOW-462
``pwa.mutation.discarded`` addition (mutation-queue account-change /
principal-mismatch discards).
"""

from __future__ import annotations

import pytest

from apps.analytics.schema import ALLOWED_EVENTS, TelemetrySchemaError, parse_payload


class TestFavouriteEventsAllowlisted:
    """SNOW-414: map.favourite.* events are accepted by the schema."""

    @pytest.mark.parametrize(
        "event_name",
        [
            "map.favourite.created",
            "map.favourite.deleted",
            "map.favourite.overlay_toggled",
        ],
    )
    def test_event_name_in_allowed_events(self, event_name: str) -> None:
        """Each map.favourite.* event name is a member of ALLOWED_EVENTS."""
        assert event_name in ALLOWED_EVENTS

    @pytest.mark.parametrize(
        "event_name",
        [
            "map.favourite.created",
            "map.favourite.deleted",
            "map.favourite.overlay_toggled",
        ],
    )
    def test_parse_payload_accepts_valid_envelope(self, event_name: str) -> None:
        """A well-formed single envelope carrying the event name parses cleanly."""
        envelope = {
            "event": event_name,
            "timestamp": "2026-07-18T10:00:00+00:00",
            "client_version": "2026.07.18.abc",
        }
        parsed = parse_payload(envelope)
        assert parsed == [envelope]

    def test_unknown_favourite_event_rejected(self) -> None:
        """A plausible-looking but undeclared favourite event is rejected."""
        envelope = {
            "event": "map.favourite.renamed",
            "timestamp": "2026-07-18T10:00:00+00:00",
            "client_version": "2026.07.18.abc",
        }
        with pytest.raises(TelemetrySchemaError):
            parse_payload(envelope)


class TestCommunityReportsEventsAllowlisted:
    """SNOW-419: map.community_reports.* events are accepted by the schema."""

    @pytest.mark.parametrize(
        "event_name",
        [
            "map.community_reports.overlay_toggled",
            "map.community_reports.marker_tapped",
        ],
    )
    def test_event_name_in_allowed_events(self, event_name: str) -> None:
        """Each map.community_reports.* event name is a member of ALLOWED_EVENTS."""
        assert event_name in ALLOWED_EVENTS

    @pytest.mark.parametrize(
        "event_name",
        [
            "map.community_reports.overlay_toggled",
            "map.community_reports.marker_tapped",
        ],
    )
    def test_parse_payload_accepts_valid_envelope(self, event_name: str) -> None:
        """A well-formed single envelope carrying the event name parses cleanly."""
        envelope = {
            "event": event_name,
            "timestamp": "2026-07-18T10:00:00+00:00",
            "client_version": "2026.07.18.abc",
        }
        parsed = parse_payload(envelope)
        assert parsed == [envelope]

    def test_unknown_community_reports_event_rejected(self) -> None:
        """A plausible-looking but undeclared event is rejected."""
        envelope = {
            "event": "map.community_reports.marker_dismissed",
            "timestamp": "2026-07-18T10:00:00+00:00",
            "client_version": "2026.07.18.abc",
        }
        with pytest.raises(TelemetrySchemaError):
            parse_payload(envelope)


class TestMutationDiscardedEventAllowlisted:
    """SNOW-462: pwa.mutation.discarded is accepted by the schema."""

    def test_event_name_in_allowed_events(self) -> None:
        """pwa.mutation.discarded is a member of ALLOWED_EVENTS."""
        assert "pwa.mutation.discarded" in ALLOWED_EVENTS

    def test_parse_payload_accepts_valid_envelope(self) -> None:
        """A well-formed single envelope carrying the event name parses cleanly."""
        envelope = {
            "event": "pwa.mutation.discarded",
            "timestamp": "2026-07-18T10:00:00+00:00",
            "client_version": "2026.07.18.abc",
            "properties": {"reason": "account_change", "count": 2},
        }
        parsed = parse_payload(envelope)
        assert parsed == [envelope]


class TestJsErrorEventAllowlisted:
    """SNOW-894: the client-side error reporter's event is accepted."""

    def test_event_name_in_allowed_events(self) -> None:
        """``js.error`` is a member of ALLOWED_EVENTS."""
        assert "js.error" in ALLOWED_EVENTS

    def test_parse_payload_accepts_the_opted_in_payload(self) -> None:
        """The full diagnostic payload an opted-in client sends parses cleanly."""
        envelope = {
            "event": "js.error",
            "timestamp": "2026-09-10T10:00:00+00:00",
            "client_version": "2026.09.10.abc",
            "properties": {
                "kind": "error",
                "pathname": "/",
                "message": "Failed to initialize WebGL",
                "filename": "/static/js/map.js",
                "lineno": 718,
                "colno": 12,
                "stack": "Error: Failed to initialize WebGL\n  at boot",
            },
        }
        assert parse_payload(envelope) == [envelope]

    def test_parse_payload_accepts_the_opted_out_payload(self) -> None:
        """The reduced payload an opted-out client sends parses cleanly too.

        ``js.error`` is a CRITICAL event client-side, so it is sent whatever
        the opt-in preference says; the reduced properties are what makes
        that acceptable (see ``static/js/error_reporting.js``). Both shapes
        have to reach the receiver — a schema that only knew the full one
        would 400 exactly the reports from people who opted out.
        """
        envelope = {
            "event": "js.error",
            "timestamp": "2026-09-10T10:00:00+00:00",
            "client_version": "2026.09.10.abc",
            "properties": {"kind": "error", "pathname": "/"},
            # telemetry.js strips both ids for an opted-out envelope.
            "session_id": None,
            "user_id": None,
        }
        assert parse_payload(envelope) == [envelope]

    def test_unknown_error_event_rejected(self) -> None:
        """A plausible neighbour that was never declared is still rejected."""
        envelope = {
            "event": "js.warning",
            "timestamp": "2026-09-10T10:00:00+00:00",
            "client_version": "2026.09.10.abc",
        }
        with pytest.raises(TelemetrySchemaError):
            parse_payload(envelope)
