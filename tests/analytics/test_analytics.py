"""
tests/analytics/test_analytics.py — Unit tests for the analytics wrapper.

Covers:
  track()  — no-op when POSTHOG_API_KEY is unset; calls through when key is set;
             raises AnalyticsPIIError for each banned key.
  alias()  — no-op when key unset; calls through when key is set.
  Settings callables — POSTHOG_MW_REQUEST_FILTER and POSTHOG_MW_TAG_MAP.
  Test-suite network guard (SNOW-548) — the suite cannot reach PostHog
             regardless of the launching process's environment.
"""

from __future__ import annotations

import os
from typing import Any, Callable
from unittest.mock import patch

import posthog
import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings

import analytics
from analytics.exceptions import AnalyticsPIIError

# ---------------------------------------------------------------------------
# track() — no-op when key is absent
# ---------------------------------------------------------------------------


class TestTrackNoOp:
    """track() is a no-op when POSTHOG_API_KEY is empty."""

    @override_settings(POSTHOG_API_KEY="")
    def test_no_call_when_key_empty(self) -> None:
        with patch("posthog.capture") as mock_capture:
            analytics.track("test_event", "user-1")
            mock_capture.assert_not_called()

    @override_settings(POSTHOG_API_KEY="  ")
    def test_no_call_when_key_whitespace(self) -> None:
        """Whitespace-only key is treated as absent."""
        with patch("posthog.capture") as mock_capture:
            analytics.track("test_event", "user-1")
            mock_capture.assert_not_called()


# ---------------------------------------------------------------------------
# track() — calls through when key is set
# ---------------------------------------------------------------------------


class TestTrackCallsThrough:
    """track() calls posthog.capture() when a key is configured."""

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_capture_called_with_event_and_distinct_id(self) -> None:
        with patch("posthog.capture") as mock_capture:
            analytics.track("subscription_started", "abc-123")
            mock_capture.assert_called_once_with(
                event="subscription_started",
                distinct_id="abc-123",
                properties={},
            )

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_capture_called_with_properties(self) -> None:
        with patch("posthog.capture") as mock_capture:
            analytics.track(
                "region_added",
                "user-42",
                {"region_id": "CH-4115", "region_count_after": 2},
            )
            mock_capture.assert_called_once_with(
                event="region_added",
                distinct_id="user-42",
                properties={"region_id": "CH-4115", "region_count_after": 2},
            )

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_exception_from_client_does_not_propagate(self) -> None:
        """Errors from PostHog client are swallowed — analytics must not break requests."""
        with patch("posthog.capture", side_effect=RuntimeError("PostHog is down")):
            # Should not raise.
            analytics.track("test_event", "user-1")


# ---------------------------------------------------------------------------
# track() — PII key rejection
# ---------------------------------------------------------------------------


class TestTrackPIIRejection:
    """track() raises AnalyticsPIIError when properties contain PII keys."""

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

    @override_settings(POSTHOG_API_KEY="")
    def test_no_call_when_key_empty(self) -> None:
        with patch("posthog.alias") as mock_alias:
            analytics.alias("user-42", "anon-uuid-xyz")
            mock_alias.assert_not_called()


# ---------------------------------------------------------------------------
# alias() — calls through when key is set
# ---------------------------------------------------------------------------


class TestAliasCallsThrough:
    """alias() calls posthog.alias() when a key is configured."""

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_alias_called_with_correct_args(self) -> None:
        with patch("posthog.alias") as mock_alias:
            analytics.alias("user-42", "anon-uuid-xyz")
            mock_alias.assert_called_once_with(
                previous_id="anon-uuid-xyz",
                distinct_id="user-42",
            )

    @override_settings(
        POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
    )
    def test_exception_from_client_does_not_propagate(self) -> None:
        """Errors from PostHog alias call are swallowed."""
        with patch("posthog.alias", side_effect=RuntimeError("PostHog is down")):
            # Should not raise.
            analytics.alias("user-42", "anon-xyz")


# ---------------------------------------------------------------------------
# POSTHOG_MW_TAG_MAP — strips email from middleware tag dict
# ---------------------------------------------------------------------------


class TestPosthogMwTagMap:
    """POSTHOG_MW_TAG_MAP callable strips the ``email`` key from tag dicts."""

    def test_email_stripped_from_tags(self) -> None:
        """email is removed to honour the PII invariant."""
        tag_map: Callable[[dict[str, Any]], dict[str, Any]] = getattr(
            settings, "POSTHOG_MW_TAG_MAP"
        )
        tags = {"email": "user@example.com", "id": "42", "is_staff": False}
        result = tag_map(tags)
        assert "email" not in result

    def test_other_keys_retained(self) -> None:
        """Non-PII keys pass through unchanged."""
        tag_map: Callable[[dict[str, Any]], dict[str, Any]] = getattr(
            settings, "POSTHOG_MW_TAG_MAP"
        )
        tags = {"email": "user@example.com", "id": "42", "is_staff": False}
        result = tag_map(tags)
        assert result == {"id": "42", "is_staff": False}

    def test_empty_dict_is_safe(self) -> None:
        """Calling with an empty dict returns an empty dict."""
        tag_map: Callable[[dict[str, Any]], dict[str, Any]] = getattr(
            settings, "POSTHOG_MW_TAG_MAP"
        )
        assert tag_map({}) == {}


# ---------------------------------------------------------------------------
# POSTHOG_MW_REQUEST_FILTER — short-circuits when API key is absent
# ---------------------------------------------------------------------------


class TestPosthogMwRequestFilter:
    """POSTHOG_MW_REQUEST_FILTER returns False when the API key is empty."""

    def _make_request(self) -> object:
        """Build a minimal GET request object."""
        return RequestFactory().get("/")

    @override_settings(POSTHOG_API_KEY="")
    def test_returns_false_when_key_empty(self) -> None:
        """Middleware is bypassed when no key is configured."""
        request_filter: Callable[[object], bool] = getattr(
            settings, "POSTHOG_MW_REQUEST_FILTER"
        )
        assert request_filter(self._make_request()) is False

    @override_settings(POSTHOG_API_KEY="  ")
    def test_returns_false_when_key_whitespace(self) -> None:
        """Whitespace-only key is treated as absent."""
        request_filter: Callable[[object], bool] = getattr(
            settings, "POSTHOG_MW_REQUEST_FILTER"
        )
        assert request_filter(self._make_request()) is False

    @override_settings(POSTHOG_API_KEY="phc_real_key")
    def test_returns_true_when_key_set(self) -> None:
        """Middleware runs normally when a key is configured."""
        request_filter: Callable[[object], bool] = getattr(
            settings, "POSTHOG_MW_REQUEST_FILTER"
        )
        assert request_filter(self._make_request()) is True


# ---------------------------------------------------------------------------
# Test-suite network guard (SNOW-548)
# ---------------------------------------------------------------------------


class TestPosthogDisabledInTests:
    """The suite never reaches PostHog, however the process was launched.

    ``tox.ini`` sets ``POSTHOG_API_KEY=`` for ``[testenv:test]``, but a
    bare ``pytest`` run inherits whatever is in the developer's ``.env``
    and ``AnalyticsConfig.ready()`` forwards it to the module-level
    client. The autouse ``_disable_posthog`` fixture in
    ``tests/conftest.py`` is what makes these assertions hold either way.
    """

    def test_module_client_is_disabled(self) -> None:
        """The global posthog client is off with no key, mid-test."""
        assert posthog.disabled is True
        assert posthog.api_key == ""

    def test_setting_is_empty(self) -> None:
        """The setting every no-op branch checks reads as empty."""
        assert settings.POSTHOG_API_KEY == ""

    def test_telemetry_master_switch_is_on(self) -> None:
        """PWA_TELEMETRY_ENABLED shows the shipped default, not local .env.

        A developer running with ``PWA_TELEMETRY_ENABLED=False`` in
        ``.env`` was silently no-opping ``emit_server_signal`` and
        ``telemetry_receive``, failing 28 analytics tests locally that
        pass in CI.
        """
        assert settings.PWA_TELEMETRY_ENABLED is True

    def test_env_key_does_not_leak_into_a_request_cycle(self) -> None:
        """An ambient env var cannot re-enable capture during a request."""
        with patch.dict(os.environ, {"POSTHOG_API_KEY": "phc_leaked_key"}):
            with patch("posthog.capture") as mock_capture:
                analytics.track("test_event", "user-1")
        mock_capture.assert_not_called()
