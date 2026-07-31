"""
tests/bulletins/services/test_elevation.py — Tests for the elevation service.

Covers:
  - fetch_elevation: happy path, settings-derived default host, base_url
    override, the customer-API ``apikey`` parameter (SNOW-577), HTTP error
    propagation, malformed payload (missing key / empty array).

All outbound HTTP calls are mocked via ``unittest.mock.patch`` so no network
traffic is required, mirroring test_weather_fetcher.py's pattern.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings

from apps.bulletins.services.elevation import fetch_elevation


def _mock_get(response_data: dict[str, Any]) -> MagicMock:
    """Return a mock for requests.get that yields a JSON response."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = response_data
    return MagicMock(return_value=mock_response)


class TestFetchElevation:
    """fetch_elevation happy path and base_url override."""

    def test_returns_elevation_from_response(self) -> None:
        """The elevation value at index [0] is returned."""
        with patch(
            "apps.bulletins.services.elevation.requests.get",
            _mock_get({"elevation": [1834.0]}),
        ):
            result = fetch_elevation(46.123, 7.456)
        assert result == 1834.0

    @override_settings(OPEN_METEO_API_BASE_URL="https://api.example/v1")
    def test_uses_configured_host_when_base_url_not_set(self) -> None:
        """The configured host's /elevation endpoint is used when base_url is None."""
        mock_get = _mock_get({"elevation": [1000.0]})
        with patch("apps.bulletins.services.elevation.requests.get", mock_get):
            fetch_elevation(46.0, 7.0)
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.example/v1/elevation"

    def test_base_url_override(self) -> None:
        """A base_url override replaces the configured endpoint base."""
        mock_get = _mock_get({"elevation": [500.0]})
        with patch("apps.bulletins.services.elevation.requests.get", mock_get):
            fetch_elevation(46.0, 7.0, base_url="https://mirror.example/v1")
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://mirror.example/v1/elevation"

    def test_params_include_latitude_and_longitude(self) -> None:
        """latitude/longitude are passed as request params."""
        mock_get = _mock_get({"elevation": [500.0]})
        with patch("apps.bulletins.services.elevation.requests.get", mock_get):
            fetch_elevation(46.123, 7.456)
        called_params = mock_get.call_args.kwargs["params"]
        assert called_params == {"latitude": "46.123", "longitude": "7.456"}


class TestFetchElevationApiKey:
    """The customer-API key is sent only when configured (SNOW-577)."""

    @override_settings(OPEN_METEO_API_KEY="")
    def test_no_apikey_param_when_key_unset(self) -> None:
        """On the free tier no apikey parameter is sent at all."""
        mock_get = _mock_get({"elevation": [500.0]})
        with patch("apps.bulletins.services.elevation.requests.get", mock_get):
            fetch_elevation(46.0, 7.0)
        assert "apikey" not in mock_get.call_args.kwargs["params"]

    @override_settings(OPEN_METEO_API_KEY="sk-test")
    def test_apikey_param_sent_when_key_set(self) -> None:
        """A configured key is appended to the request params."""
        mock_get = _mock_get({"elevation": [500.0]})
        with patch("apps.bulletins.services.elevation.requests.get", mock_get):
            fetch_elevation(46.0, 7.0)
        assert mock_get.call_args.kwargs["params"]["apikey"] == "sk-test"

    @override_settings(OPEN_METEO_API_KEY="sk-test")
    def test_no_apikey_param_for_base_url_override(self) -> None:
        """A mirror override is not the key's host, so no key is sent."""
        mock_get = _mock_get({"elevation": [500.0]})
        with patch("apps.bulletins.services.elevation.requests.get", mock_get):
            fetch_elevation(46.0, 7.0, base_url="https://mirror.example/v1")
        assert "apikey" not in mock_get.call_args.kwargs["params"]

    @override_settings(OPEN_METEO_API_KEY="sk-test")
    def test_key_is_never_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """The debug log records the URL, which must not carry the key."""
        mock_get = _mock_get({"elevation": [500.0]})
        with caplog.at_level(logging.DEBUG, logger="apps.bulletins.services.elevation"):
            with patch("apps.bulletins.services.elevation.requests.get", mock_get):
                fetch_elevation(46.0, 7.0)
        assert caplog.text
        assert "sk-test" not in caplog.text


class TestFetchElevationErrors:
    """fetch_elevation error propagation for HTTP failures and bad payloads."""

    def test_http_error_raises(self) -> None:
        """requests.HTTPError propagates to the caller."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "503 Service Unavailable"
        )
        with patch(
            "apps.bulletins.services.elevation.requests.get",
            return_value=mock_response,
        ):
            with pytest.raises(requests.HTTPError):
                fetch_elevation(46.0, 7.0)

    def test_missing_elevation_key_raises_key_error(self) -> None:
        """A response without an 'elevation' key raises KeyError."""
        with patch(
            "apps.bulletins.services.elevation.requests.get",
            _mock_get({}),
        ):
            with pytest.raises(KeyError):
                fetch_elevation(46.0, 7.0)

    def test_empty_elevation_array_raises_index_error(self) -> None:
        """A response with an empty 'elevation' array raises IndexError."""
        with patch(
            "apps.bulletins.services.elevation.requests.get",
            _mock_get({"elevation": []}),
        ):
            with pytest.raises(IndexError):
                fetch_elevation(46.0, 7.0)
