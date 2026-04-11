"""
tests/pipeline/services/test_data_fetcher.py — Tests for the data_fetcher service.

Covers:
  - _normalise_response: all three API response shapes + empty cases
  - _parse_dt: ISO-8601 parsing
  - _get_or_create_region: creation, idempotency, name updates
  - upsert_bulletin: creation, update, region linking
  - fetch_bulletin_page: HTTP call with mocked responses
  - run_pipeline: full orchestration with mocked API pages
"""

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from pipeline.models import Bulletin, PipelineRun, Region, RegionBulletin
from pipeline.services.data_fetcher import (
    _get_or_create_region,
    _normalise_response,
    _parse_dt,
    fetch_bulletin_page,
    run_pipeline,
    upsert_bulletin,
)
from tests.factories import PipelineRunFactory


def _make_raw_bulletin(
    bulletin_id: str = "test-001",
    publication_time: str = "2025-03-15T08:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """
    Build a raw bulletin dict matching the SLF CAAML API shape.

    Args:
        bulletin_id: The bulletin identifier.
        publication_time: ISO-8601 publication timestamp.
        **overrides: Additional keys to merge into the bulletin dict.

    Returns:
        A dict matching the shape returned by the SLF CAAML API.

    """
    base: dict[str, Any] = {
        "bulletinID": bulletin_id,
        "publicationTime": publication_time,
        "validTime": {
            "startTime": "2025-03-15T17:00:00Z",
            "endTime": "2025-03-16T17:00:00Z",
        },
        "nextUpdate": "2025-03-16T08:00:00Z",
        "lang": "en",
        "unscheduled": False,
        "regions": [
            {"regionID": "CH-4115", "name": "Piz Buin"},
            {"regionID": "CH-7111", "name": "Engadin"},
        ],
        "dangerRatings": [],
        "avalancheProblems": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _normalise_response
# ---------------------------------------------------------------------------


class TestNormaliseResponse:
    """Tests for _normalise_response."""

    def test_flat_list(self):
        """A flat list of bulletin dicts is returned as-is."""
        bulletins = [{"bulletinID": "a"}, {"bulletinID": "b"}]
        assert _normalise_response(bulletins) == bulletins

    def test_single_collection_object(self):
        """A dict with a 'bulletins' key unwraps to the inner list."""
        data = {"bulletins": [{"bulletinID": "a"}]}
        assert _normalise_response(data) == [{"bulletinID": "a"}]

    def test_list_of_collection_objects(self):
        """A list of collection objects is flattened."""
        data = [
            {"bulletins": [{"bulletinID": "a"}]},
            {"bulletins": [{"bulletinID": "b"}, {"bulletinID": "c"}]},
        ]
        result = _normalise_response(data)
        assert len(result) == 3
        assert [b["bulletinID"] for b in result] == ["a", "b", "c"]

    def test_empty_list(self):
        """An empty list returns an empty list."""
        assert _normalise_response([]) == []

    def test_empty_dict(self):
        """A dict without 'bulletins' returns an empty list."""
        assert _normalise_response({}) == []

    def test_none_returns_empty(self):
        """None returns an empty list."""
        assert _normalise_response(None) == []

    def test_string_returns_empty(self):
        """An unexpected string returns an empty list."""
        assert _normalise_response("unexpected") == []


# ---------------------------------------------------------------------------
# _parse_dt
# ---------------------------------------------------------------------------


class TestParseDt:
    """Tests for _parse_dt."""

    def test_utc_timestamp(self):
        """Parses a Z-suffixed ISO-8601 string to a UTC-aware datetime."""
        result = _parse_dt("2025-03-15T08:00:00Z")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)

    def test_offset_timestamp_is_converted_to_utc(self):
        """An ISO-8601 string with a +01:00 offset is converted to UTC."""
        result = _parse_dt("2025-03-15T09:00:00+01:00")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_negative_offset_timestamp_is_converted_to_utc(self):
        """An ISO-8601 string with a -05:00 offset is converted to UTC."""
        result = _parse_dt("2025-03-15T03:00:00-05:00")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_naive_timestamp_is_assumed_utc(self):
        """A naive ISO-8601 string is assumed to be UTC."""
        result = _parse_dt("2025-03-15T08:00:00")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert result.tzinfo is UTC


# ---------------------------------------------------------------------------
# _get_or_create_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetOrCreateRegion:
    """Tests for _get_or_create_region."""

    def test_creates_new_region(self):
        """Creates a Region when it doesn't exist."""
        region = _get_or_create_region("CH-4115", "Piz Buin")
        assert region.region_id == "CH-4115"
        assert region.name == "Piz Buin"
        assert region.slug == "ch-4115"
        assert Region.objects.count() == 1

    def test_returns_existing_region(self):
        """Returns the existing Region on second call."""
        r1 = _get_or_create_region("CH-4115", "Piz Buin")
        r2 = _get_or_create_region("CH-4115", "Piz Buin")
        assert r1.pk == r2.pk
        assert Region.objects.count() == 1

    def test_updates_name_if_changed(self):
        """Updates the name when the region exists but name differs."""
        _get_or_create_region("CH-4115", "Old Name")
        region = _get_or_create_region("CH-4115", "New Name")
        assert region.name == "New Name"
        assert Region.objects.count() == 1


# ---------------------------------------------------------------------------
# upsert_bulletin
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpsertBulletin:
    """Tests for upsert_bulletin."""

    def test_creates_bulletin(self):
        """Creates a new Bulletin and returns True."""
        run = PipelineRunFactory()
        raw = _make_raw_bulletin()
        created = upsert_bulletin(raw, run)

        assert created is True
        assert Bulletin.objects.count() == 1

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.issued_at == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert bulletin.lang == "en"
        assert bulletin.unscheduled is False
        assert bulletin.pipeline_run == run

    def test_wraps_raw_data_in_geojson_feature(self):
        """Raw data is wrapped in a GeoJSON Feature envelope."""
        run = PipelineRunFactory()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.raw_data["type"] == "Feature"
        assert bulletin.raw_data["geometry"] is None
        assert bulletin.raw_data["properties"]["bulletinID"] == "test-001"

    def test_creates_regions(self):
        """Creates Region and RegionBulletin records for each region."""
        run = PipelineRunFactory()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        assert Region.objects.count() == 2
        assert RegionBulletin.objects.count() == 2

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        region_ids = list(
            bulletin.regions.order_by("region_id").values_list("region_id", flat=True)
        )
        assert region_ids == ["CH-4115", "CH-7111"]

    def test_stores_region_name_at_time(self):
        """RegionBulletin records store the name from the bulletin."""
        run = PipelineRunFactory()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        link = RegionBulletin.objects.get(region__region_id="CH-4115")
        assert link.region_name_at_time == "Piz Buin"

    def test_update_existing_bulletin(self):
        """Updating an existing bulletin returns False and refreshes data."""
        run = PipelineRunFactory()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        # Update with a new publication time
        raw_updated = _make_raw_bulletin(
            publication_time="2025-03-15T12:00:00Z",
        )
        created = upsert_bulletin(raw_updated, run)

        assert created is False
        assert Bulletin.objects.count() == 1
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.issued_at == datetime(2025, 3, 15, 12, 0, 0, tzinfo=UTC)

    def test_update_replaces_region_links(self):
        """Updating a bulletin clears and re-creates region links."""
        run = PipelineRunFactory()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)
        assert RegionBulletin.objects.count() == 2

        # Update with only one region
        raw_updated = _make_raw_bulletin(
            regions=[{"regionID": "CH-9999", "name": "New Region"}],
        )
        upsert_bulletin(raw_updated, run)

        assert RegionBulletin.objects.count() == 1
        assert RegionBulletin.objects.first().region.region_id == "CH-9999"

    def test_handles_missing_next_update(self):
        """Bulletin without nextUpdate stores None."""
        run = PipelineRunFactory()
        raw = _make_raw_bulletin()
        del raw["nextUpdate"]
        upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.next_update is None

    def test_handles_empty_regions(self):
        """Bulletin with no regions creates no RegionBulletin rows."""
        run = PipelineRunFactory()
        raw = _make_raw_bulletin(regions=[])
        upsert_bulletin(raw, run)

        assert RegionBulletin.objects.count() == 0


# ---------------------------------------------------------------------------
# fetch_bulletin_page
# ---------------------------------------------------------------------------


class TestFetchBulletinPage:
    """Tests for fetch_bulletin_page."""

    @patch("pipeline.services.data_fetcher.requests.get")
    def test_returns_normalised_bulletins(self, mock_get: MagicMock):
        """Fetches a page from the API and normalises the response."""
        mock_response = MagicMock()
        mock_response.json.return_value = [_make_raw_bulletin()]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_bulletin_page("en", 50, 0)

        assert len(result) == 1
        assert result[0]["bulletinID"] == "test-001"
        mock_get.assert_called_once_with(
            "https://aws.slf.ch/api/bulletin-list/caaml/en/json",
            params={"limit": 50, "offset": 0},
            timeout=30,
        )

    @patch("pipeline.services.data_fetcher.requests.get")
    def test_raises_on_http_error(self, mock_get: MagicMock):
        """Raises HTTPError when the API returns a non-2xx status."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500")
        mock_get.return_value = mock_response

        with pytest.raises(requests.HTTPError):
            fetch_bulletin_page("en", 50, 0)

    @patch("pipeline.services.data_fetcher.requests.get")
    def test_passes_lang_in_url(self, mock_get: MagicMock):
        """The language code is included in the URL path."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetch_bulletin_page("de", 10, 5)

        url = mock_get.call_args[0][0]
        assert "/de/json" in url


# ---------------------------------------------------------------------------
# run_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunPipeline:
    """Tests for run_pipeline."""

    @patch("pipeline.services.data_fetcher.fetch_bulletin_page")
    def test_creates_bulletins_in_date_range(self, mock_fetch: MagicMock):
        """Bulletins within the date range are stored."""
        mock_fetch.return_value = [
            _make_raw_bulletin("b1", "2025-03-15T08:00:00Z"),
            _make_raw_bulletin("b2", "2025-03-14T08:00:00Z"),
        ]

        run = run_pipeline(
            start=date(2025, 3, 14),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.status == PipelineRun.Status.SUCCESS
        assert run.records_created == 2
        assert Bulletin.objects.count() == 2

    @patch("pipeline.services.data_fetcher.fetch_bulletin_page")
    def test_skips_bulletins_newer_than_end_date(self, mock_fetch: MagicMock):
        """Bulletins newer than the end date are skipped."""
        mock_fetch.return_value = [
            _make_raw_bulletin("future", "2025-04-01T08:00:00Z"),
            _make_raw_bulletin("in-range", "2025-03-15T08:00:00Z"),
        ]

        run = run_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.records_created == 1
        assert Bulletin.objects.filter(bulletin_id="in-range").exists()
        assert not Bulletin.objects.filter(bulletin_id="future").exists()

    @patch("pipeline.services.data_fetcher.fetch_bulletin_page")
    def test_stops_at_start_date_boundary(self, mock_fetch: MagicMock):
        """Pagination stops when a bulletin older than start date is hit."""
        mock_fetch.return_value = [
            _make_raw_bulletin("in-range", "2025-03-15T08:00:00Z"),
            _make_raw_bulletin("too-old", "2025-03-13T08:00:00Z"),
        ]

        run = run_pipeline(
            start=date(2025, 3, 14),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.records_created == 1
        assert not Bulletin.objects.filter(bulletin_id="too-old").exists()

    @patch("pipeline.services.data_fetcher.fetch_bulletin_page")
    def test_dry_run_does_not_write(self, mock_fetch: MagicMock):
        """Dry run fetches data but does not persist bulletins."""
        mock_fetch.return_value = [
            _make_raw_bulletin("b1", "2025-03-15T08:00:00Z"),
        ]

        run = run_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
            dry_run=True,
        )

        assert run.status == PipelineRun.Status.SUCCESS
        assert run.records_created == 0
        assert Bulletin.objects.count() == 0

    @patch("pipeline.services.data_fetcher.fetch_bulletin_page")
    def test_skips_existing_without_force(self, mock_fetch: MagicMock):
        """Without --force, existing bulletins are skipped."""
        # Pre-create the bulletin
        pre_run = PipelineRunFactory()
        upsert_bulletin(
            _make_raw_bulletin("existing", "2025-03-15T08:00:00Z"),
            pre_run,
        )

        mock_fetch.return_value = [
            _make_raw_bulletin("existing", "2025-03-15T08:00:00Z"),
        ]

        run = run_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
            force=False,
        )

        assert run.records_created == 0
        assert run.records_updated == 0

    @patch("pipeline.services.data_fetcher.fetch_bulletin_page")
    def test_updates_existing_with_force(self, mock_fetch: MagicMock):
        """With --force, existing bulletins are upserted."""
        pre_run = PipelineRunFactory()
        upsert_bulletin(
            _make_raw_bulletin("existing", "2025-03-15T08:00:00Z"),
            pre_run,
        )

        mock_fetch.return_value = [
            _make_raw_bulletin("existing", "2025-03-15T08:00:00Z"),
        ]

        run = run_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
            force=True,
        )

        assert run.records_updated == 1
        assert Bulletin.objects.count() == 1

    @patch("pipeline.services.data_fetcher.fetch_bulletin_page")
    def test_marks_run_failed_on_exception(self, mock_fetch: MagicMock):
        """Run is marked FAILED if fetch raises an exception."""
        mock_fetch.side_effect = requests.ConnectionError("timeout")

        run = run_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.status == PipelineRun.Status.FAILED
        assert "timeout" in run.error_message

    @patch("pipeline.services.data_fetcher.PAGE_SIZE", 1)
    @patch("pipeline.services.data_fetcher.fetch_bulletin_page")
    def test_paginates_until_empty_page(self, mock_fetch: MagicMock):
        """Pages until the API returns an empty list."""
        # With PAGE_SIZE=1, a page with 1 result does NOT trigger the
        # "fewer than requested" early exit, so a second fetch occurs.
        mock_fetch.side_effect = [
            [_make_raw_bulletin("b1", "2025-03-15T08:00:00Z")],
            [],
        ]

        run = run_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.status == PipelineRun.Status.SUCCESS
        assert run.records_created == 1
        assert mock_fetch.call_count == 2

    @patch("pipeline.services.data_fetcher.fetch_bulletin_page")
    def test_run_records_triggered_by(self, mock_fetch: MagicMock):
        """The triggered_by label is stored on the PipelineRun."""
        mock_fetch.return_value = []

        run = run_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="backfill_data command",
        )

        assert run.triggered_by == "backfill_data command"
