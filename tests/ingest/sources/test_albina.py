"""
tests/ingest/sources/test_albina.py — Tests for apps.ingest.sources.albina.

Moved from tests/bulletins/services/test_albina_fetcher.py with the HTTP code
(SNOW-1249):
  - parse_issued_at: publicationTime, validTime fallback, the fallback value
  - normalise_albina_response: all response shapes + unexpected
  - fetch_albina_for_date: URL construction, 404 tolerance, HTTP errors,
    list/envelope response shapes
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from apps.ingest.sources.albina import (
    fetch_albina_for_date,
    normalise_albina_response,
    parse_issued_at,
)


def _make_raw_bulletin(
    bulletin_id: str = "albina-001",
    publication_time: str = "2026-01-15T18:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal raw ALBINA bulletin dict."""
    base: dict[str, Any] = {
        "bulletinID": bulletin_id,
        "publicationTime": publication_time,
        "validTime": {
            "startTime": "2026-01-15T16:00:00Z",
            "endTime": "2026-01-16T16:00:00Z",
        },
        "lang": "en",
        "unscheduled": False,
        "regions": [{"regionID": "AT-07-01", "name": "Allgäu Alps East"}],
        "dangerRatings": [],
        "avalancheProblems": [],
        "customData": {"ALBINA": {"mainDate": "2026-01-15"}},
    }
    base.update(overrides)
    return base


def _mock_ok(bulletins: list[dict[str, Any]]) -> MagicMock:
    """Return a mock requests.Response with status 200 and given JSON."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = bulletins
    mock.raise_for_status.return_value = None
    return mock


def _mock_404() -> MagicMock:
    """Return a mock requests.Response with status 404."""
    mock = MagicMock()
    mock.status_code = 404
    mock.raise_for_status.return_value = None
    return mock


def _mock_error(status: int = 500) -> MagicMock:
    """Return a mock requests.Response with a non-404 error status."""
    import requests

    mock = MagicMock()
    mock.status_code = status
    mock.raise_for_status.side_effect = requests.HTTPError(
        f"HTTP {status}", response=mock
    )
    return mock


# ---------------------------------------------------------------------------
# _parse_issued_at
# ---------------------------------------------------------------------------


class TestParseIssuedAt:
    """_parse_issued_at prefers publicationTime, falls back to validTime, then default."""

    _FALLBACK = datetime(2000, 1, 1, tzinfo=UTC)

    def test_uses_publication_time_when_present(self) -> None:
        """A valid publicationTime wins over validTime.startTime."""
        raw = {
            "publicationTime": "2026-01-15T18:00:00Z",
            "validTime": {"startTime": "2025-12-01T00:00:00Z"},
        }
        result = parse_issued_at(raw, fallback=self._FALLBACK)
        assert result == datetime(2026, 1, 15, 18, 0, tzinfo=UTC)

    def test_falls_back_to_valid_time_when_publication_time_missing(self) -> None:
        """A missing publicationTime falls through to validTime.startTime."""
        raw = {"validTime": {"startTime": "2026-01-15T16:00:00Z"}}
        result = parse_issued_at(raw, fallback=self._FALLBACK)
        assert result == datetime(2026, 1, 15, 16, 0, tzinfo=UTC)

    def test_falls_back_to_valid_time_when_publication_time_malformed(self) -> None:
        """A malformed publicationTime falls through to validTime.startTime."""
        raw = {
            "publicationTime": "not-a-date",
            "validTime": {"startTime": "2026-01-15T16:00:00Z"},
        }
        result = parse_issued_at(raw, fallback=self._FALLBACK)
        assert result == datetime(2026, 1, 15, 16, 0, tzinfo=UTC)

    def test_returns_fallback_when_both_missing(self) -> None:
        """Both timestamps absent returns the fallback."""
        result = parse_issued_at({}, fallback=self._FALLBACK)
        assert result == self._FALLBACK

    def test_returns_fallback_when_both_malformed(self) -> None:
        """Both timestamps malformed returns the fallback."""
        raw = {
            "publicationTime": "garbage",
            "validTime": {"startTime": "also garbage"},
        }
        result = parse_issued_at(raw, fallback=self._FALLBACK)
        assert result == self._FALLBACK

    def test_handles_missing_valid_time_dict(self) -> None:
        """A missing validTime key is tolerated."""
        raw = {"publicationTime": ""}
        result = parse_issued_at(raw, fallback=self._FALLBACK)
        assert result == self._FALLBACK


# ---------------------------------------------------------------------------
# _normalise_response
# ---------------------------------------------------------------------------


class TestNormaliseResponse:
    """_normalise_response handles all CDN body shapes."""

    def test_list_returned_as_is(self) -> None:
        """A top-level list is returned unchanged."""
        data = [{"bulletinID": "x"}, {"bulletinID": "y"}]
        assert normalise_albina_response(data, "2026-01-01", "AT-07") == data

    def test_envelope_dict_unwrapped(self) -> None:
        """A dict with 'bulletins' key returns the inner list."""
        inner = [{"bulletinID": "x"}]
        data = {"bulletins": inner}
        assert normalise_albina_response(data, "2026-01-01", "AT-07") == inner

    def test_unexpected_shape_returns_empty(self) -> None:
        """An unrecognised shape logs a warning and returns []."""
        data = {"other": "stuff"}
        result = normalise_albina_response(data, "2026-01-01", "AT-07")
        assert result == []

    def test_plain_string_returns_empty(self) -> None:
        """A string response body returns []."""
        result = normalise_albina_response("not json", "2026-01-01", "AT-07")
        assert result == []


# ---------------------------------------------------------------------------
# fetch_albina_for_date
# ---------------------------------------------------------------------------


class TestFetchEuregioForDate:
    """fetch_albina_for_date constructs the correct URL and handles responses."""

    @patch("apps.ingest.sources.albina.requests.get")
    def test_url_construction(self, mock_get: MagicMock) -> None:
        """URL uses {base}/{date}/{date}_{region}_en_CAAMLv6.json shape."""
        mock_get.return_value = _mock_ok([])
        fetch_albina_for_date(date(2026, 1, 15), "AT-07", base_url="https://cdn")
        expected_url = "https://cdn/2026-01-15/2026-01-15_AT-07_en_CAAMLv6.json"
        mock_get.assert_called_once_with(expected_url, timeout=30)

    @patch("apps.ingest.sources.albina.requests.get")
    def test_returns_bulletins_on_200(self, mock_get: MagicMock) -> None:
        """A 200 response returns the list of bulletin dicts."""
        bulletins = [_make_raw_bulletin()]
        mock_get.return_value = _mock_ok(bulletins)
        result = fetch_albina_for_date(
            date(2026, 1, 15), "AT-07", base_url="https://cdn"
        )
        assert result == bulletins

    @patch("apps.ingest.sources.albina.requests.get")
    def test_returns_empty_on_404(self, mock_get: MagicMock) -> None:
        """A 404 response returns [] (off-season gap, not an error)."""
        mock_get.return_value = _mock_404()
        result = fetch_albina_for_date(
            date(2026, 1, 15), "AT-07", base_url="https://cdn"
        )
        assert result == []

    @patch("apps.ingest.sources.albina.requests.get")
    def test_raises_on_non_404_error(self, mock_get: MagicMock) -> None:
        """A 500 response raises requests.HTTPError."""
        import requests

        mock_get.return_value = _mock_error(500)
        with pytest.raises(requests.HTTPError):
            fetch_albina_for_date(date(2026, 1, 15), "AT-07", base_url="https://cdn")

    @patch("apps.ingest.sources.albina.requests.get")
    def test_uses_settings_base_url_when_none(self, mock_get: MagicMock) -> None:
        """When base_url is None, falls back to settings.ALBINA_API_BASE_URL."""
        mock_get.return_value = _mock_ok([])
        with override_settings(ALBINA_API_BASE_URL="https://settings-cdn"):
            fetch_albina_for_date(date(2026, 1, 15), "IT-32-BZ")
        call_url = mock_get.call_args[0][0]
        assert call_url.startswith("https://settings-cdn/")

    @patch("apps.ingest.sources.albina.requests.get")
    def test_unwraps_envelope_response(self, mock_get: MagicMock) -> None:
        """A {"bulletins": [...]} envelope is unwrapped."""
        bulletins = [_make_raw_bulletin()]
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"bulletins": bulletins},
            raise_for_status=lambda: None,
        )
        result = fetch_albina_for_date(
            date(2026, 1, 15), "AT-07", base_url="https://cdn"
        )
        assert result == bulletins
