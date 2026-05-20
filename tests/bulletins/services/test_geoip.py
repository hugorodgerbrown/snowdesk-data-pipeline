"""
tests/bulletins/services/test_geoip.py — Tests for bulletins.services.geoip.

Covers the happy path (public IP), private/loopback addresses, malformed
input, and missing-mmdb graceful degradation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_django.fixtures import SettingsWrapper

from bulletins.services import geoip as geoip_module
from bulletins.services.geoip import country_code_for, reset_reader_for_testing


@pytest.fixture(autouse=True)
def reset_geoip_reader() -> None:
    """Reset the module-level cached reader before every test."""
    reset_reader_for_testing()


class TestCountryCodeFor:
    """Tests for country_code_for()."""

    def test_public_ip_returns_code(self) -> None:
        """8.8.8.8 is a Google DNS server; MaxMind maps it to the US."""
        code = country_code_for("8.8.8.8")
        assert len(code) == 2
        assert code == code.upper()

    def test_loopback_returns_empty_string(self) -> None:
        """127.0.0.1 is a private address; GeoLite2 does not cover it."""
        assert country_code_for("127.0.0.1") == ""

    def test_rfc1918_returns_empty_string(self) -> None:
        """192.168.x.x is a private range not covered by GeoLite2."""
        assert country_code_for("192.168.1.1") == ""

    def test_malformed_input_returns_empty_string(self) -> None:
        """Non-IP strings must not raise; they return empty string."""
        assert country_code_for("not-an-ip") == ""
        assert country_code_for("") == ""
        assert country_code_for("999.999.999.999") == ""

    def test_missing_mmdb_returns_empty_string(
        self, settings: SettingsWrapper, tmp_path: Path
    ) -> None:
        """When GEOIP_PATH points to a non-existent file, return "" and log a warning."""
        settings.GEOIP_PATH = tmp_path / "nonexistent.mmdb"
        reset_reader_for_testing()
        result = country_code_for("8.8.8.8")
        assert result == ""

    def test_geoip_path_none_returns_empty_string(
        self, settings: SettingsWrapper
    ) -> None:
        """When GEOIP_PATH is None/unset, return "" gracefully."""
        settings.GEOIP_PATH = None
        reset_reader_for_testing()
        result = country_code_for("8.8.8.8")
        assert result == ""

    def test_reader_cached_after_first_call(self) -> None:
        """A second call should use the cached reader, not open the file again."""
        code1 = country_code_for("8.8.8.8")
        code2 = country_code_for("8.8.8.8")
        assert code1 == code2

    def test_reset_reader_for_testing_clears_cache(
        self, settings: SettingsWrapper
    ) -> None:
        """reset_reader_for_testing() allows a fresh reader to be opened."""
        # First call populates the cache.
        country_code_for("8.8.8.8")
        assert geoip_module._reader_initialised is True  # noqa: SLF001
        # Reset clears it.
        reset_reader_for_testing()
        assert geoip_module._reader_initialised is False  # noqa: SLF001
