"""
tests/bulletins/services/test_euregio_fetcher.py — Tests for the EUREGIO fetcher service.

Covers:
  - fetch_euregio_for_date: URL construction, 404 tolerance, HTTP errors,
    list/envelope response shapes, unexpected shapes.
  - _normalise_response: all response shapes + unexpected.
  - run_euregio_pipeline: dedup by bulletinID, date-range filtering,
    dry-run, force, HTTP error slot-skip, on_fetched callback.
  - latest_euregio_date: returns None when no EUREGIO bulletins; returns
    the latest valid_from date otherwise.

All HTTP calls are mocked via unittest.mock.patch so no real network is used.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bulletins.services.euregio_fetcher import (
    _normalise_response,
    fetch_euregio_for_date,
    latest_euregio_date,
    run_euregio_pipeline,
)
from tests.factories import MicroRegionFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_bulletin(
    bulletin_id: str = "euregio-001",
    publication_time: str = "2026-01-15T18:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal raw EUREGIO bulletin dict."""
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
# _normalise_response
# ---------------------------------------------------------------------------


class TestNormaliseResponse:
    """_normalise_response handles all CDN body shapes."""

    def test_list_returned_as_is(self):
        """A top-level list is returned unchanged."""
        data = [{"bulletinID": "x"}, {"bulletinID": "y"}]
        assert _normalise_response(data, "2026-01-01", "AT-07") == data

    def test_envelope_dict_unwrapped(self):
        """A dict with 'bulletins' key returns the inner list."""
        inner = [{"bulletinID": "x"}]
        data = {"bulletins": inner}
        assert _normalise_response(data, "2026-01-01", "AT-07") == inner

    def test_unexpected_shape_returns_empty(self):
        """An unrecognised shape logs a warning and returns []."""
        data = {"other": "stuff"}
        result = _normalise_response(data, "2026-01-01", "AT-07")
        assert result == []

    def test_plain_string_returns_empty(self):
        """A string response body returns []."""
        result = _normalise_response("not json", "2026-01-01", "AT-07")
        assert result == []


# ---------------------------------------------------------------------------
# fetch_euregio_for_date
# ---------------------------------------------------------------------------


class TestFetchEuregioForDate:
    """fetch_euregio_for_date constructs the correct URL and handles responses."""

    @patch("bulletins.services.euregio_fetcher.requests.get")
    def test_url_construction(self, mock_get):
        """URL uses {base}/{date}/{date}_{region}_en_CAAMLv6.json shape."""
        mock_get.return_value = _mock_ok([])
        fetch_euregio_for_date(date(2026, 1, 15), "AT-07", base_url="https://cdn")
        expected_url = "https://cdn/2026-01-15/2026-01-15_AT-07_en_CAAMLv6.json"
        mock_get.assert_called_once_with(expected_url, timeout=30)

    @patch("bulletins.services.euregio_fetcher.requests.get")
    def test_returns_bulletins_on_200(self, mock_get):
        """A 200 response returns the list of bulletin dicts."""
        bulletins = [_make_raw_bulletin()]
        mock_get.return_value = _mock_ok(bulletins)
        result = fetch_euregio_for_date(
            date(2026, 1, 15), "AT-07", base_url="https://cdn"
        )
        assert result == bulletins

    @patch("bulletins.services.euregio_fetcher.requests.get")
    def test_returns_empty_on_404(self, mock_get):
        """A 404 response returns [] (off-season gap, not an error)."""
        mock_get.return_value = _mock_404()
        result = fetch_euregio_for_date(
            date(2026, 1, 15), "AT-07", base_url="https://cdn"
        )
        assert result == []

    @patch("bulletins.services.euregio_fetcher.requests.get")
    def test_raises_on_non_404_error(self, mock_get):
        """A 500 response raises requests.HTTPError."""
        import requests

        mock_get.return_value = _mock_error(500)
        with pytest.raises(requests.HTTPError):
            fetch_euregio_for_date(date(2026, 1, 15), "AT-07", base_url="https://cdn")

    @patch("bulletins.services.euregio_fetcher.requests.get")
    def test_uses_settings_base_url_when_none(self, mock_get):
        """When base_url is None, falls back to settings.EUREGIO_API_BASE_URL."""
        mock_get.return_value = _mock_ok([])
        with patch("bulletins.services.euregio_fetcher.settings") as mock_settings:
            mock_settings.EUREGIO_API_BASE_URL = "https://settings-cdn"
            fetch_euregio_for_date(date(2026, 1, 15), "IT-32-BZ")
        call_url = mock_get.call_args[0][0]
        assert call_url.startswith("https://settings-cdn/")

    @patch("bulletins.services.euregio_fetcher.requests.get")
    def test_unwraps_envelope_response(self, mock_get):
        """A {"bulletins": [...]} envelope is unwrapped."""
        bulletins = [_make_raw_bulletin()]
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"bulletins": bulletins},
            raise_for_status=lambda: None,
        )
        result = fetch_euregio_for_date(
            date(2026, 1, 15), "AT-07", base_url="https://cdn"
        )
        assert result == bulletins


# ---------------------------------------------------------------------------
# run_euregio_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunEuregioPipeline:
    """run_euregio_pipeline: dedup, date filter, dry-run, force, error handling."""

    def test_creates_bulletin_for_known_region(self):
        """A bulletin whose region is seeded gets stored and run returns SUCCESS."""
        MicroRegionFactory.create(region_id="AT-07-01", name="Allgäu Alps East")
        raw = _make_raw_bulletin()

        with patch(
            "bulletins.services.euregio_fetcher.fetch_euregio_for_date"
        ) as mock_fetch:
            mock_fetch.return_value = [raw]
            run = run_euregio_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                triggered_by="test",
            )

        from bulletins.models import Bulletin

        assert Bulletin.objects.filter(bulletin_id="euregio-001").exists()
        assert run.records_created == 1

    def test_deduplication_by_bulletin_id(self):
        """The same bulletinID appearing in two region files is stored only once."""
        MicroRegionFactory.create(region_id="AT-07-01", name="Allgäu Alps East")
        raw = _make_raw_bulletin()

        with patch(
            "bulletins.services.euregio_fetcher.fetch_euregio_for_date"
        ) as mock_fetch:
            # Return the same bulletin for two different regions.
            mock_fetch.return_value = [raw]
            run = run_euregio_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07", "IT-32-BZ"),
                triggered_by="test",
            )

        from bulletins.models import Bulletin

        assert Bulletin.objects.count() == 1
        assert run.records_created == 1

    def test_dry_run_does_not_write(self):
        """dry_run=True fetches but does not persist any bulletins."""
        MicroRegionFactory.create(region_id="AT-07-01", name="Allgäu Alps East")
        raw = _make_raw_bulletin()

        with patch(
            "bulletins.services.euregio_fetcher.fetch_euregio_for_date"
        ) as mock_fetch:
            mock_fetch.return_value = [raw]
            run_euregio_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                dry_run=True,
                triggered_by="test",
            )

        from bulletins.models import Bulletin

        assert Bulletin.objects.count() == 0

    def test_http_error_slot_skip_increments_records_failed(self):
        """An HTTP error for a (date, region) pair increments records_failed."""
        import requests

        http_exc = requests.HTTPError("500 Server Error")
        with patch(
            "bulletins.services.euregio_fetcher.fetch_euregio_for_date"
        ) as mock_fetch:
            mock_fetch.side_effect = http_exc
            run = run_euregio_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                triggered_by="test",
            )

        assert run.records_failed == 1

    def test_on_fetched_callback_called_for_each_bulletin(self):
        """on_fetched is invoked once per raw bulletin, before dedup."""
        raw1 = _make_raw_bulletin("id-1")
        raw2 = _make_raw_bulletin("id-2")
        collected: list[dict] = []

        with patch(
            "bulletins.services.euregio_fetcher.fetch_euregio_for_date"
        ) as mock_fetch:
            mock_fetch.return_value = [raw1, raw2]
            run_euregio_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                dry_run=True,
                on_fetched=collected.append,
                triggered_by="test",
            )

        assert len(collected) == 2
        assert collected[0]["bulletinID"] == "id-1"
        assert collected[1]["bulletinID"] == "id-2"

    def test_multi_day_range_fetches_each_day(self):
        """A multi-day range makes one CDN request per (date, region) pair."""
        with patch(
            "bulletins.services.euregio_fetcher.fetch_euregio_for_date"
        ) as mock_fetch:
            mock_fetch.return_value = []
            run_euregio_pipeline(
                date(2026, 1, 1),
                date(2026, 1, 3),
                regions=("AT-07",),
                dry_run=True,
                triggered_by="test",
            )

        # 3 dates × 1 region = 3 calls
        assert mock_fetch.call_count == 3

    def test_bulletin_outside_date_range_not_stored(self):
        """A bulletin published outside the requested range is skipped."""
        MicroRegionFactory.create(region_id="AT-07-01", name="Allgäu Alps East")
        # Publication time is 2026-01-10; we request 2026-01-15.
        raw = _make_raw_bulletin(publication_time="2026-01-10T18:00:00Z")

        with patch(
            "bulletins.services.euregio_fetcher.fetch_euregio_for_date"
        ) as mock_fetch:
            mock_fetch.return_value = [raw]
            run_euregio_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                triggered_by="test",
            )

        from bulletins.models import Bulletin

        assert Bulletin.objects.count() == 0


# ---------------------------------------------------------------------------
# latest_euregio_date
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLatestEuregioDate:
    """latest_euregio_date returns the most recent EUREGIO valid_from date."""

    def test_returns_none_when_no_euregio_bulletins(self):
        """Returns None when the DB has no EUREGIO bulletins."""
        assert latest_euregio_date() is None

    def test_returns_latest_valid_from(self):
        """Returns the date of the most recent EUREGIO bulletin's valid_from."""
        from tests.factories import BulletinFactory

        BulletinFactory.create(
            valid_from=datetime(2026, 1, 15, 16, tzinfo=UTC),
            valid_to=datetime(2026, 1, 16, 16, tzinfo=UTC),
            render_model={"source": "euregio", "version": 4},
            render_model_version=4,
        )
        BulletinFactory.create(
            valid_from=datetime(2026, 1, 10, 16, tzinfo=UTC),
            valid_to=datetime(2026, 1, 11, 16, tzinfo=UTC),
            render_model={"source": "euregio", "version": 4},
            render_model_version=4,
        )
        result = latest_euregio_date()
        assert result == date(2026, 1, 15)

    def test_ignores_slf_bulletins(self):
        """Bulletins with source='slf' are not considered."""
        from tests.factories import BulletinFactory

        BulletinFactory.create(
            valid_from=datetime(2026, 1, 20, 16, tzinfo=UTC),
            valid_to=datetime(2026, 1, 21, 16, tzinfo=UTC),
            render_model={"source": "slf", "version": 4},
            render_model_version=4,
        )
        assert latest_euregio_date() is None
