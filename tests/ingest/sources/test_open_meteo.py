"""
tests/ingest/sources/test_open_meteo.py — Tests for Open-Meteo addressing.

Covers the two addressing helpers in ``apps.ingest.sources.open_meteo``
(SNOW-577), moved from ``apps.locations`` by SNOW-1249:

  - request_url: elevation resolves against ``OPEN_METEO_API_BASE_URL``,
    and a ``base_url`` override wins.
  - with_api_key: the key is appended only when the operator has moved the
    host off its free default (SNOW-579).

SNOW-762 stripped the weather app, so the forecast and archive endpoints
this module used to address are gone with it; elevation is what remains.

Pure functions over settings — no HTTP, no database.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from django.test import override_settings

from apps.ingest.sources import open_meteo


class TestRequestUrl:
    """request_url resolves each endpoint against the right configured host."""

    @override_settings(
        OPEN_METEO_API_BASE_URL="https://api.example/v1",
    )
    def test_elevation_uses_api_host(self) -> None:
        """Elevation is served from the main API host."""
        assert (
            open_meteo.request_url(open_meteo.ELEVATION)
            == "https://api.example/v1/elevation"
        )

    @override_settings(
        OPEN_METEO_API_BASE_URL="https://api.example/v1",
    )
    def test_base_url_override_wins(self) -> None:
        """An explicit base_url beats the configured host."""
        mirror = "http://localhost:8000/dev/openmeteo-mirror/v1"
        assert (
            open_meteo.request_url(open_meteo.ELEVATION, mirror)
            == f"{mirror}/elevation"
        )


class TestWithApiKey:
    """with_api_key appends the key only for configured customer hosts."""

    CUSTOMER_API = "https://customer-api.example/v1"

    @override_settings(
        OPEN_METEO_API_KEY="sk-test",
        OPEN_METEO_API_BASE_URL=CUSTOMER_API,
    )
    def test_appends_key_for_a_customer_host(self) -> None:
        """A request to the configured customer host carries the key."""
        assert open_meteo.with_api_key(
            {"latitude": "46.0"}, f"{self.CUSTOMER_API}/elevation"
        ) == {"latitude": "46.0", "apikey": "sk-test"}

    @override_settings(OPEN_METEO_API_KEY="sk-test")
    def test_no_key_for_a_free_host(self) -> None:
        """With the shipped defaults every host is free, so no key is sent."""
        params = {"latitude": "46.0"}
        for endpoint in (open_meteo.ELEVATION,):
            url = open_meteo.request_url(endpoint)
            assert open_meteo.with_api_key(params, url) is params

    @override_settings(
        OPEN_METEO_API_KEY="sk-test",
        OPEN_METEO_API_BASE_URL=CUSTOMER_API,
    )
    def test_no_key_for_the_dev_mirror(self) -> None:
        """A mirror host is in neither set, so it never sees the key."""
        params = {"latitude": "46.0"}
        mirror = "http://localhost:8000/dev/openmeteo-mirror/v1/forecast"
        assert open_meteo.with_api_key(params, mirror) is params

    @override_settings(
        OPEN_METEO_API_KEY="",
        OPEN_METEO_API_BASE_URL=CUSTOMER_API,
    )
    def test_no_key_when_none_configured(self) -> None:
        """A customer host without a key configured still sends nothing."""
        params = {"latitude": "46.0"}
        assert (
            open_meteo.with_api_key(params, f"{self.CUSTOMER_API}/elevation") is params
        )

    @override_settings(
        OPEN_METEO_API_KEY="sk-test",
        OPEN_METEO_API_BASE_URL="https://customer-api.example/v1/",
    )
    def test_host_match_ignores_path_and_trailing_slash(self) -> None:
        """Matching is on hostname, so URL cosmetics cannot defeat it."""
        result = open_meteo.with_api_key(
            {"latitude": "46.0"}, "https://customer-api.example/v2/forecast"
        )
        assert result["apikey"] == "sk-test"

    @override_settings(
        OPEN_METEO_API_KEY="sk-test",
        OPEN_METEO_API_BASE_URL=CUSTOMER_API,
    )
    def test_does_not_mutate_the_caller_mapping(self) -> None:
        """The caller's dict is left alone; a new one is returned."""
        params = {"latitude": "46.0"}
        result = open_meteo.with_api_key(params, f"{self.CUSTOMER_API}/elevation")
        assert params == {"latitude": "46.0"}
        assert result is not params


class TestFreeHostnames:
    """FREE_HOSTNAMES must stay in step with the settings defaults."""

    def test_shipped_defaults_are_all_free_hosts(self) -> None:
        """A default that drifted off this list would start leaking the key."""
        assert open_meteo._customer_hostnames() == frozenset()


class TestFetchForecast:
    """fetch_forecast — the one request the live fetch and the backfill share."""

    @override_settings(
        OPEN_METEO_API_BASE_URL="https://api.example/v1",
        OPEN_METEO_HISTORY_BASE_URL="https://history.example/v1",
    )
    def test_sends_the_window_and_the_elevation(self) -> None:
        """The coordinate, the window and the height reach the request."""
        response = MagicMock()
        response.json.return_value = {"daily": {}}
        with patch(
            "apps.ingest.sources.open_meteo.requests.get", return_value=response
        ) as get:
            payload = open_meteo.fetch_forecast(
                46.1, 7.2, 2400.0, date(2026, 1, 10), date(2026, 1, 16)
            )
        assert payload == {"daily": {}}
        url = get.call_args.args[0]
        params = get.call_args.kwargs["params"]
        assert url == "https://api.example/v1/forecast"
        assert params["start_date"] == "2026-01-10"
        assert params["end_date"] == "2026-01-16"
        assert params["elevation"] == "2400.0"
        assert params["timezone"] == "auto"
        assert open_meteo.history_base_url() == "https://history.example/v1"

    def test_a_point_with_no_height_sends_none(self) -> None:
        """No elevation is no parameter, so the forecast is the model cell's."""
        response = MagicMock()
        response.json.return_value = {}
        with patch(
            "apps.ingest.sources.open_meteo.requests.get", return_value=response
        ) as get:
            open_meteo.fetch_forecast(
                46.1,
                7.2,
                None,
                date(2026, 1, 10),
                date(2026, 1, 10),
                base_url="https://mirror.example",
            )
        assert "elevation" not in get.call_args.kwargs["params"]
        assert get.call_args.args[0] == "https://mirror.example/forecast"
