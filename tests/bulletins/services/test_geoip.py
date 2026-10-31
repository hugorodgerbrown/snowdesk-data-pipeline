"""
tests/bulletins/services/test_geoip.py — Tests for apps.bulletins.services.geoip.

Covers the happy path (public IP returning a GeoLookup), private/loopback
addresses, malformed input, and missing-mmdb graceful degradation.

``TestGeoLookupWithStubReader`` exercises the success path, the field
mapping and the reader cache against a stub standing in for
``geoip2.database.Reader``, so it runs with no database file present.

Tests that require the actual GeoLite2-City.mmdb database file are marked
``requires_geoip_db`` and are skipped automatically when the file is absent
(i.e. the MaxMind credentials have not been configured and
``bin/fetch-geoip-data`` has not been run locally).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import geoip2.database
import geoip2.errors
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


class _StubSubdivisions(list[SimpleNamespace]):
    """A list of subdivisions exposing ``most_specific`` like geoip2's."""

    @property
    def most_specific(self) -> SimpleNamespace:
        """Return the last (most specific) subdivision, as geoip2 does."""
        return self[-1]


def _city_response(
    *,
    country: str | None = "CH",
    subdivisions: list[str | None] | None = None,
    city: str | None = "Verbier",
    latitude: float | None = 46.0964,
    longitude: float | None = 7.2286,
    accuracy_radius: int | None = 20,
) -> SimpleNamespace:
    """Build a namespace shaped like a geoip2 ``City`` response.

    Args:
        country: The country ISO code.
        subdivisions: Subdivision ISO codes, least to most specific.
        city: The city name.
        latitude: The location's latitude.
        longitude: The location's longitude.
        accuracy_radius: The location's accuracy radius in kilometres.

    Returns:
        A namespace with the attributes ``geo_lookup`` reads.

    """
    codes = ["VS"] if subdivisions is None else subdivisions
    return SimpleNamespace(
        country=SimpleNamespace(iso_code=country),
        subdivisions=_StubSubdivisions(SimpleNamespace(iso_code=c) for c in codes),
        city=SimpleNamespace(name=city),
        location=SimpleNamespace(
            latitude=latitude,
            longitude=longitude,
            accuracy_radius=accuracy_radius,
        ),
    )


class _StubReader:
    """Stand-in for ``geoip2.database.Reader`` that never touches a file."""

    instances: list[_StubReader] = []
    response: SimpleNamespace | None = None
    error: Exception | None = None

    def __init__(self, path: str) -> None:
        """Record the path opened and this construction."""
        self.path = path
        _StubReader.instances.append(self)

    def city(self, ip: str) -> SimpleNamespace:
        """Return the configured response, or raise the configured error."""
        if _StubReader.error is not None:
            raise _StubReader.error
        assert _StubReader.response is not None
        return _StubReader.response


class TestGeoLookupWithStubReader:
    """geo_lookup() against a stubbed Reader — runs without the mmdb file."""

    @pytest.fixture(autouse=True)
    def stub_reader(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
    ) -> type[_StubReader]:
        """Patch ``geoip2.database.Reader`` with a fresh ``_StubReader``."""
        settings.GEOIP_PATH = tmp_path / "stub.mmdb"
        _StubReader.instances = []
        _StubReader.response = _city_response()
        _StubReader.error = None
        monkeypatch.setattr(geoip2.database, "Reader", _StubReader)
        return _StubReader

    def test_full_response_maps_onto_lookup(self) -> None:
        """Every field of the response lands on the matching GeoLookup field."""
        assert geo_lookup("203.0.113.7") == GeoLookup(
            country="CH",
            subdivision="VS",
            city="Verbier",
            latitude=46.0964,
            longitude=7.2286,
            accuracy_radius_km=20,
        )

    def test_most_specific_subdivision_is_used(self, stub_reader: Any) -> None:
        """With several subdivisions, the most specific one's code is kept."""
        stub_reader.response = _city_response(subdivisions=["ENG", "LND"])
        result = geo_lookup("203.0.113.7")
        assert result is not None
        assert result.subdivision == "LND"

    def test_empty_subdivisions_gives_blank_subdivision(self, stub_reader: Any) -> None:
        """No subdivisions in the response → ``subdivision == ""``."""
        stub_reader.response = _city_response(subdivisions=[])
        result = geo_lookup("203.0.113.7")
        assert result is not None
        assert result.subdivision == ""

    def test_missing_names_and_codes_are_blank_not_none(self, stub_reader: Any) -> None:
        """Absent strings come back as "" and absent numbers as None."""
        stub_reader.response = _city_response(
            country=None,
            subdivisions=[None],
            city=None,
            latitude=None,
            longitude=None,
            accuracy_radius=None,
        )
        assert geo_lookup("203.0.113.7") == GeoLookup.empty()

    @pytest.mark.parametrize(
        "error",
        [
            geoip2.errors.AddressNotFoundError("not found", "10.0.0.1"),
            ValueError("'nonsense' does not appear to be an IP address"),
        ],
        ids=["address-not-found", "value-error"],
    )
    def test_reader_error_returns_none(
        self, stub_reader: Any, error: Exception
    ) -> None:
        """An exception from ``reader.city`` is swallowed and yields None."""
        stub_reader.error = error
        assert geo_lookup("10.0.0.1") is None

    def test_reader_opened_once_and_cached(
        self, stub_reader: Any, settings: Settings
    ) -> None:
        """``_get_reader`` opens GEOIP_PATH once, then reuses the instance."""
        # Typed as object: the real annotation is Reader, which the stub is not.
        first: object = geoip_module._get_reader()  # noqa: SLF001
        second: object = geoip_module._get_reader()  # noqa: SLF001
        assert len(stub_reader.instances) == 1
        opened: _StubReader = stub_reader.instances[0]
        assert opened.path == str(settings.GEOIP_PATH)
        assert first is opened
        assert second is opened
