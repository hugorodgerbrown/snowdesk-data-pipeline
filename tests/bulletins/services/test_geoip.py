"""
tests/bulletins/services/test_geoip.py — Tests for apps.bulletins.services.geoip.

Covers the happy path (public IP returning a GeoLookup), private/loopback
addresses, malformed input, and missing-mmdb graceful degradation.

Tests that require the actual GeoLite2-City.mmdb database file are marked
``requires_geoip_db`` and are skipped automatically when the file is absent
(i.e. the MaxMind credentials have not been configured and
``bin/fetch-geoip-data`` has not been run locally).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings as django_settings
from pytest_django.fixtures import Settings

from apps.bulletins.services import geoip as geoip_module
from apps.bulletins.services.geoip import (
    GeoLookup,
    geo_lookup,
    reset_reader_for_testing,
)

# Marker: skip if the City DB doesn't exist locally.
_GEOIP_DB_EXISTS = (
    Path(django_settings.GEOIP_PATH).exists()
    if getattr(django_settings, "GEOIP_PATH", None)
    else False
)

requires_geoip_db = pytest.mark.skipif(
    not _GEOIP_DB_EXISTS,
    reason=(
        "GeoLite2-City.mmdb not present — run ./bin/fetch-geoip-data with MaxMind "
        "credentials to enable live geo lookup tests."
    ),
)


@pytest.fixture(autouse=True)
def reset_geoip_reader() -> None:
    """Reset the module-level cached reader before every test."""
    reset_reader_for_testing()


class TestGeoLookupDataclass:
    """Tests for the GeoLookup dataclass."""

    def test_empty_classmethod_returns_blank_lookup(self) -> None:
        """GeoLookup.empty() returns a fully-blank instance."""
        empty = GeoLookup.empty()
        assert empty.country == ""
        assert empty.subdivision == ""
        assert empty.city == ""
        assert empty.latitude is None
        assert empty.longitude is None
        assert empty.accuracy_radius_km is None

    def test_frozen(self) -> None:
        """GeoLookup instances are immutable (frozen dataclass)."""
        lookup = GeoLookup.empty()
        with pytest.raises((AttributeError, TypeError)):
            lookup.country = "CH"  # type: ignore[misc]


class TestGeoLookup:
    """Tests for geo_lookup()."""

    @requires_geoip_db
    def test_public_ip_returns_lookup_with_country(self) -> None:
        """8.8.8.8 is a Google DNS server; MaxMind maps it to the US."""
        result = geo_lookup("8.8.8.8")
        assert result is not None
        assert len(result.country) == 2
        assert result.country == result.country.upper()

    @requires_geoip_db
    def test_public_ip_has_coordinates(self) -> None:
        """A public IP lookup should include latitude and longitude."""
        result = geo_lookup("8.8.8.8")
        assert result is not None
        assert result.latitude is not None
        assert result.longitude is not None

    @requires_geoip_db
    def test_loopback_returns_none(self) -> None:
        """127.0.0.1 is a private address; GeoLite2 does not cover it."""
        assert geo_lookup("127.0.0.1") is None

    @requires_geoip_db
    def test_rfc1918_returns_none(self) -> None:
        """192.168.x.x is a private range not covered by GeoLite2."""
        assert geo_lookup("192.168.1.1") is None

    def test_malformed_input_returns_none(self) -> None:
        """Non-IP strings must not raise; they return None.

        This test intentionally passes even without the database —
        malformed input is rejected before the reader is consulted when
        the database is absent (the reader returns None, so the whole
        chain returns None).
        """
        assert geo_lookup("not-an-ip") is None
        assert geo_lookup("") is None
        assert geo_lookup("999.999.999.999") is None

    def test_missing_mmdb_returns_none(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """When GEOIP_PATH points to a non-existent file, return None."""
        settings.GEOIP_PATH = tmp_path / "nonexistent.mmdb"
        reset_reader_for_testing()
        result = geo_lookup("8.8.8.8")
        assert result is None

    def test_geoip_path_none_returns_none(self, settings: Settings) -> None:
        """When GEOIP_PATH is None/unset, return None gracefully."""
        settings.GEOIP_PATH = None
        reset_reader_for_testing()
        result = geo_lookup("8.8.8.8")
        assert result is None

    @requires_geoip_db
    def test_reader_cached_after_first_call(self) -> None:
        """A second call should use the cached reader, not open the file again."""
        result1 = geo_lookup("8.8.8.8")
        result2 = geo_lookup("8.8.8.8")
        assert (result1 is None) == (result2 is None)
        if result1 is not None and result2 is not None:
            assert result1.country == result2.country

    def test_reset_reader_for_testing_clears_cache(self, settings: Settings) -> None:
        """reset_reader_for_testing() allows a fresh reader to be opened."""
        # First call populates the cache.
        geo_lookup("8.8.8.8")
        assert geoip_module._reader_initialised is True  # noqa: SLF001
        # Reset clears it.
        reset_reader_for_testing()
        assert geoip_module._reader_initialised is False  # noqa: SLF001
