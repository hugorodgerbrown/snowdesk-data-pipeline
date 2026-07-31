"""
tests/bulletins/services/test_open_meteo.py — Tests for Open-Meteo addressing.

Covers the two helpers in ``apps.bulletins.services.open_meteo`` (SNOW-577):

  - request_url: elevation and forecast resolve against
    ``OPEN_METEO_API_BASE_URL``, archive against
    ``OPEN_METEO_ARCHIVE_BASE_URL``, and a ``base_url`` override wins for
    every endpoint.
  - with_api_key: the key is appended only when configured and only when
    the request is going to the configured host.

Pure functions over settings — no HTTP, no database.
"""

from __future__ import annotations

from django.test import override_settings

from apps.bulletins.services import open_meteo


class TestRequestUrl:
    """request_url resolves each endpoint against the right configured host."""

    @override_settings(
        OPEN_METEO_API_BASE_URL="https://api.example/v1",
        OPEN_METEO_ARCHIVE_BASE_URL="https://archive.example/v1",
    )
    def test_elevation_uses_api_host(self) -> None:
        """Elevation is served from the main API host."""
        assert (
            open_meteo.request_url(open_meteo.ELEVATION)
            == "https://api.example/v1/elevation"
        )

    @override_settings(
        OPEN_METEO_API_BASE_URL="https://api.example/v1",
        OPEN_METEO_ARCHIVE_BASE_URL="https://archive.example/v1",
    )
    def test_forecast_uses_api_host(self) -> None:
        """Forecast is served from the main API host."""
        assert (
            open_meteo.request_url(open_meteo.FORECAST)
            == "https://api.example/v1/forecast"
        )

    @override_settings(
        OPEN_METEO_API_BASE_URL="https://api.example/v1",
        OPEN_METEO_ARCHIVE_BASE_URL="https://archive.example/v1",
    )
    def test_archive_uses_archive_host(self) -> None:
        """Archive has its own host on both the free and the paid tier."""
        assert (
            open_meteo.request_url(open_meteo.ARCHIVE)
            == "https://archive.example/v1/archive"
        )

    @override_settings(
        OPEN_METEO_API_BASE_URL="https://api.example/v1",
        OPEN_METEO_ARCHIVE_BASE_URL="https://archive.example/v1",
    )
    def test_base_url_override_wins_for_every_endpoint(self) -> None:
        """The dev mirror serves all three endpoints under one base."""
        mirror = "http://localhost:8000/dev/openmeteo-mirror/v1"
        assert (
            open_meteo.request_url(open_meteo.FORECAST, mirror) == f"{mirror}/forecast"
        )
        assert open_meteo.request_url(open_meteo.ARCHIVE, mirror) == f"{mirror}/archive"
        assert (
            open_meteo.request_url(open_meteo.ELEVATION, mirror)
            == f"{mirror}/elevation"
        )


class TestWithApiKey:
    """with_api_key appends the key only where it belongs."""

    @override_settings(OPEN_METEO_API_KEY="sk-test")
    def test_appends_key_when_configured(self) -> None:
        """A configured key is added to the params."""
        assert open_meteo.with_api_key({"latitude": "46.0"}) == {
            "latitude": "46.0",
            "apikey": "sk-test",
        }

    @override_settings(OPEN_METEO_API_KEY="")
    def test_returns_params_unchanged_on_free_tier(self) -> None:
        """With no key configured the mapping is returned untouched."""
        params = {"latitude": "46.0"}
        assert open_meteo.with_api_key(params) is params

    @override_settings(OPEN_METEO_API_KEY="sk-test")
    def test_returns_params_unchanged_for_base_url_override(self) -> None:
        """An override means a different host, which the key is not valid for."""
        params = {"latitude": "46.0"}
        assert open_meteo.with_api_key(params, "https://mirror.example/v1") is params

    @override_settings(OPEN_METEO_API_KEY="sk-test")
    def test_does_not_mutate_the_caller_mapping(self) -> None:
        """The caller's dict is left alone; a new one is returned."""
        params = {"latitude": "46.0"}
        result = open_meteo.with_api_key(params)
        assert params == {"latitude": "46.0"}
        assert result is not params
