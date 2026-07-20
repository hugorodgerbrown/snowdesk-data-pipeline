"""
tests/analytics/test_schema.py — Tests for analytics.schema.ALLOWED_EVENTS.

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

from analytics.schema import ALLOWED_EVENTS, TelemetrySchemaError, parse_payload


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
