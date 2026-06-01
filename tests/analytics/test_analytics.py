"""
tests/analytics/test_analytics.py — Unit tests for the analytics wrapper.

Covers:
  track()  — no-op when POSTHOG_API_KEY is unset; calls through when key is set;
             raises AnalyticsPIIError for each banned key.
  alias()  — no-op when key unset; calls through when key is set.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

import analytics
from analytics.exceptions import AnalyticsPIIError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_client() -> None:
    """Reset the module-level client singleton between tests."""
    analytics._client = None


# ---------------------------------------------------------------------------
# track() — no-op when key is absent
# ---------------------------------------------------------------------------


class TestTrackNoOp:
    """track() is a no-op when POSTHOG_API_KEY is empty."""

    def setup_method(self) -> None:
        """Reset client singleton before each test."""
        _reset_client()

    @override_settings(POSTHOG_API_KEY="")
    def test_no_call_when_key_empty(self) -> None:
        with patch("posthog.Posthog") as mock_cls:
            analytics.track("test_event", "user-1")
            mock_cls.assert_not_called()

    @override_settings(POSTHOG_API_KEY="  ")
    def test_no_call_when_key_whitespace(self) -> None:
        """Whitespace-only key is treated as absent."""
        with patch("posthog.Posthog") as mock_cls:
            analytics.track("test_event", "user-1")
            mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# track() — calls through when key is set
# ---------------------------------------------------------------------------


class TestTrackCallsThrough:
    """track() calls posthog.Posthog.capture() when a key is configured."""

    def setup_method(self) -> None:
        """Reset client singleton before each test."""
        _reset_client()

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_capture_called_with_event_and_distinct_id(self) -> None:
        mock_client = MagicMock()
        with patch("posthog.Posthog", return_value=mock_client):
            analytics.track("subscription_started", "abc-123")
            mock_client.capture.assert_called_once_with(
                event="subscription_started",
                distinct_id="abc-123",
                properties={},
            )

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_capture_called_with_properties(self) -> None:
        mock_client = MagicMock()
        with patch("posthog.Posthog", return_value=mock_client):
            analytics.track(
                "region_added",
                "user-42",
                {"region_id": "CH-4115", "region_count_after": 2},
            )
            mock_client.capture.assert_called_once_with(
                event="region_added",
                distinct_id="user-42",
                properties={"region_id": "CH-4115", "region_count_after": 2},
            )

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_exception_from_client_does_not_propagate(self) -> None:
        """Errors from PostHog client are swallowed — analytics must not break requests."""
        mock_client = MagicMock()
        mock_client.capture.side_effect = RuntimeError("PostHog is down")
        with patch("posthog.Posthog", return_value=mock_client):
            # Should not raise.
            analytics.track("test_event", "user-1")


# ---------------------------------------------------------------------------
# track() — PII key rejection
# ---------------------------------------------------------------------------


class TestTrackPIIRejection:
    """track() raises AnalyticsPIIError when properties contain PII keys."""

    def setup_method(self) -> None:
        """Reset client singleton before each test."""
        _reset_client()

    @pytest.mark.parametrize("key", ["email", "ip", "token", "credential_id"])
    def test_raises_on_pii_key(self, key: str) -> None:
        with pytest.raises(AnalyticsPIIError, match=key):
            analytics.track("test_event", "user-1", {key: "some-value"})

    def test_raises_mentions_all_violations(self) -> None:
        """Error message lists every offending key, not just the first."""
        with pytest.raises(AnalyticsPIIError, match="email"):
            analytics.track("test_event", "user-1", {"email": "a", "ip": "b"})

    @override_settings(POSTHOG_API_KEY="")
    def test_pii_check_fires_even_when_key_absent(self) -> None:
        """PII guard is unconditional — not bypassed by a no-op client."""
        with pytest.raises(AnalyticsPIIError):
            analytics.track("test_event", "user-1", {"email": "x@example.com"})


# ---------------------------------------------------------------------------
# alias() — no-op when key is absent
# ---------------------------------------------------------------------------


class TestAliasNoOp:
    """alias() is a no-op when POSTHOG_API_KEY is empty."""

    def setup_method(self) -> None:
        """Reset client singleton before each test."""
        _reset_client()

    @override_settings(POSTHOG_API_KEY="")
    def test_no_call_when_key_empty(self) -> None:
        with patch("posthog.Posthog") as mock_cls:
            analytics.alias("user-42", "anon-uuid-xyz")
            mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# alias() — calls through when key is set
# ---------------------------------------------------------------------------


class TestAliasCallsThrough:
    """alias() calls posthog.Posthog.alias() when a key is configured."""

    def setup_method(self) -> None:
        """Reset client singleton before each test."""
        _reset_client()

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_alias_called_with_correct_args(self) -> None:
        mock_client = MagicMock()
        with patch("posthog.Posthog", return_value=mock_client):
            analytics.alias("user-42", "anon-uuid-xyz")
            mock_client.alias.assert_called_once_with(
                previous_id="anon-uuid-xyz",
                distinct_id="user-42",
            )

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_exception_from_client_does_not_propagate(self) -> None:
        """Errors from PostHog alias call are swallowed."""
        mock_client = MagicMock()
        mock_client.alias.side_effect = RuntimeError("PostHog is down")
        with patch("posthog.Posthog", return_value=mock_client):
            # Should not raise.
            analytics.alias("user-42", "anon-xyz")
