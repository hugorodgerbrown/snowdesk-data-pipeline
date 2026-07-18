"""
tests/bulletins/services/test_elevation.py — Tests for the elevation service.

Covers:
  - fetch_elevation: happy path, base_url override, HTTP error propagation,
    malformed payload (missing key / empty array).

All outbound HTTP calls are mocked via ``unittest.mock.patch`` so no network
traffic is required, mirroring test_weather_fetcher.py's pattern.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from bulletins.services.elevation import ELEVATION_URL, fetch_elevation


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
            "bulletins.services.elevation.requests.get",
            _mock_get({"elevation": [1834.0]}),
        ):
            result = fetch_elevation(46.123, 7.456)
        assert result == 1834.0

    def test_uses_default_url_when_base_url_not_set(self) -> None:
        """The live ELEVATION_URL is used when base_url is None."""
        mock_get = _mock_get({"elevation": [1000.0]})
        with patch("bulletins.services.elevation.requests.get", mock_get):
            fetch_elevation(46.0, 7.0)
        called_url = mock_get.call_args[0][0]
        assert called_url == ELEVATION_URL

    def test_base_url_override(self) -> None:
        """A base_url override replaces the default request URL."""
        mock_get = _mock_get({"elevation": [500.0]})
        with patch("bulletins.services.elevation.requests.get", mock_get):
            fetch_elevation(46.0, 7.0, base_url="https://mirror.example/elevation")
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://mirror.example/elevation"

    def test_params_include_latitude_and_longitude(self) -> None:
        """latitude/longitude are passed as request params."""
        mock_get = _mock_get({"elevation": [500.0]})
        with patch("bulletins.services.elevation.requests.get", mock_get):
            fetch_elevation(46.123, 7.456)
        called_params = mock_get.call_args.kwargs["params"]
        assert called_params == {"latitude": "46.123", "longitude": "7.456"}


class TestFetchElevationErrors:
    """fetch_elevation error propagation for HTTP failures and bad payloads."""

    def test_http_error_raises(self) -> None:
        """requests.HTTPError propagates to the caller."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "503 Service Unavailable"
        )
        with patch(
            "bulletins.services.elevation.requests.get",
            return_value=mock_response,
        ):
            with pytest.raises(requests.HTTPError):
                fetch_elevation(46.0, 7.0)

    def test_missing_elevation_key_raises_key_error(self) -> None:
        """A response without an 'elevation' key raises KeyError."""
        with patch(
            "bulletins.services.elevation.requests.get",
            _mock_get({}),
        ):
            with pytest.raises(KeyError):
                fetch_elevation(46.0, 7.0)

    def test_empty_elevation_array_raises_index_error(self) -> None:
        """A response with an empty 'elevation' array raises IndexError."""
        with patch(
            "bulletins.services.elevation.requests.get",
            _mock_get({"elevation": []}),
        ):
            with pytest.raises(IndexError):
                fetch_elevation(46.0, 7.0)
