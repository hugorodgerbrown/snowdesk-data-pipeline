"""
tests/analytics/test_analytics.py — Unit tests for the analytics wrapper.

Covers:
  track()  — raises AnalyticsPIIError for each banned key, before anything
             is sent; hands everything else to fivebar (the send itself is
             covered in test_fivebar.py).
  Test-suite network guard (SNOW-548, SNOW-1180) — the suite cannot reach
             fivebar regardless of the launching process's environment.

SNOW-1182 removed PostHog, and with it the alias(), middleware filter and
tag-map tests that lived here.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from django.conf import settings

from apps import analytics
from apps.analytics.exceptions import AnalyticsPIIError


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

    def test_pii_check_fires_even_when_fivebar_is_off(self) -> None:
        """PII guard is unconditional — not bypassed by a no-op send."""
        with pytest.raises(AnalyticsPIIError):
            analytics.track("test_event", "user-1", {"email": "x@example.com"})


class TestTrackDefaults:
    """track() fills in the optional arguments."""

    def test_no_properties_and_default_page(self) -> None:
        """No properties → an empty dict; no page → ``/``."""
        with patch("apps.analytics.fivebar.send") as send:
            analytics.track("region_added", "user-1")
        send.assert_called_once_with("region_added", "user-1", {}, "/")


class TestNoLiveSendsInTests:
    """The suite never reaches fivebar, however the process was launched."""

    def test_fivebar_settings_are_empty(self) -> None:
        """The settings every no-op branch checks read as empty."""
        assert settings.FIVEBAR_TOKEN == ""
        assert settings.FIVEBAR_DOMAIN == ""

    def test_telemetry_master_switch_is_on(self) -> None:
        """PWA_TELEMETRY_ENABLED shows the shipped default, not local .env."""
        assert settings.PWA_TELEMETRY_ENABLED is True

    def test_env_token_does_not_leak_into_a_send(self) -> None:
        """An ambient env var cannot re-enable sending during a test."""
        with patch.dict(os.environ, {"FIVEBAR_TOKEN": "leaked"}):
            with patch("apps.analytics.fivebar._post") as post:
                analytics.track("region_added", "user-1")
        post.assert_not_called()
