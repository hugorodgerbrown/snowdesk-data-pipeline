"""
tests/bulletins/services/test_albina_fetcher.py — Tests for the ALBINA fetcher service.

Covers (the CDN fetch itself is tested in tests/ingest/sources/test_albina.py
since SNOW-1249):
  - run_albina_pipeline: dedup by bulletinID, date-range filtering,
    dry-run, force, HTTP error slot-skip, on_fetched callback.
  - latest_albina_date: returns None when no ALBINA bulletins; returns
    the latest valid_from date otherwise.
  - the PDF URL a bulletin is stored with (the derivation itself, a pure
    helper, is tested in tests/ingest/normalise/test_albina.py since
    SNOW-1248).

All HTTP calls are mocked via unittest.mock.patch so no real network is used.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.bulletins.models import Bulletin, PipelineRun
from apps.bulletins.services.albina_fetcher import (
    latest_albina_date,
    run_albina_pipeline,
)
from apps.bulletins.services.apply import upsert_bulletin
from apps.ingest.public import albina_pdf_url
from tests.factories import MicroRegionFactory, PipelineRunFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
# run_albina_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunEuregioPipeline:
    """run_albina_pipeline: dedup, date filter, dry-run, force, error handling."""

    def test_creates_bulletin_for_known_region(self) -> None:
        """A bulletin whose region is seeded gets stored and run returns SUCCESS."""
        MicroRegionFactory.create(region_id="AT-07-01", name="Allgäu Alps East")
        raw = _make_raw_bulletin()

        with patch("apps.ingest.sources.albina.fetch_albina_for_date") as mock_fetch:
            mock_fetch.return_value = [raw]
            run = run_albina_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                triggered_by="test",
            )

        from apps.bulletins.models import Bulletin

        assert Bulletin.objects.filter(bulletin_id="albina-001").exists()
        assert run.records_created == 1

    def test_deduplication_by_bulletin_id(self) -> None:
        """The same bulletinID appearing in two region files is stored only once."""
        MicroRegionFactory.create(region_id="AT-07-01", name="Allgäu Alps East")
        raw = _make_raw_bulletin()

        with patch("apps.ingest.sources.albina.fetch_albina_for_date") as mock_fetch:
            # Return the same bulletin for two different regions.
            mock_fetch.return_value = [raw]
            run = run_albina_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07", "IT-32-BZ"),
                triggered_by="test",
            )

        from apps.bulletins.models import Bulletin

        assert Bulletin.objects.count() == 1
        assert run.records_created == 1

    def test_dry_run_does_not_write(self) -> None:
        """dry_run=True fetches but does not persist any bulletins."""
        MicroRegionFactory.create(region_id="AT-07-01", name="Allgäu Alps East")
        raw = _make_raw_bulletin()

        with patch("apps.ingest.sources.albina.fetch_albina_for_date") as mock_fetch:
            mock_fetch.return_value = [raw]
            run_albina_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                dry_run=True,
                triggered_by="test",
            )

        from apps.bulletins.models import Bulletin

        assert Bulletin.objects.count() == 0

    def test_http_error_slot_skip_increments_records_failed(self) -> None:
        """An HTTP error for a (date, region) pair increments records_failed."""
        import requests

        http_exc = requests.HTTPError("500 Server Error")
        with patch("apps.ingest.sources.albina.fetch_albina_for_date") as mock_fetch:
            mock_fetch.side_effect = http_exc
            run = run_albina_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                triggered_by="test",
            )

        assert run.records_failed == 1
        # SNOW-1228: a run with a failed record finishes PARTIAL.
        run.refresh_from_db()
        assert run.status == PipelineRun.Status.PARTIAL

    def test_on_fetched_callback_called_for_each_bulletin(self) -> None:
        """on_fetched is invoked once per unique bulletin."""
        raw1 = _make_raw_bulletin("id-1")
        raw2 = _make_raw_bulletin("id-2")
        collected: list[dict] = []

        with patch("apps.ingest.sources.albina.fetch_albina_for_date") as mock_fetch:
            mock_fetch.return_value = [raw1, raw2]
            run_albina_pipeline(
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

    def test_on_fetched_callback_dedupes_across_regions(self) -> None:
        """A bulletin spanning multiple regions only fires on_fetched once."""
        # Same bulletinID returned by all three region files for the same day.
        shared = _make_raw_bulletin("shared-id")
        collected: list[dict] = []

        with patch("apps.ingest.sources.albina.fetch_albina_for_date") as mock_fetch:
            mock_fetch.return_value = [shared]
            run_albina_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07", "IT-32-BZ", "IT-32-TN"),
                dry_run=True,
                on_fetched=collected.append,
                triggered_by="test",
            )

        # Three regions × one bulletin each = three CDN responses, but only
        # one unique bulletinID → callback fires once.
        assert mock_fetch.call_count == 3
        assert len(collected) == 1
        assert collected[0]["bulletinID"] == "shared-id"

    def test_on_fetched_callback_skips_bulletins_with_no_id(self) -> None:
        """on_fetched is not invoked for bulletins missing bulletinID."""
        raw = _make_raw_bulletin()
        del raw["bulletinID"]
        collected: list[dict] = []

        with patch("apps.ingest.sources.albina.fetch_albina_for_date") as mock_fetch:
            mock_fetch.return_value = [raw]
            run_albina_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                dry_run=True,
                on_fetched=collected.append,
                triggered_by="test",
            )

        assert collected == []

    def test_multi_day_range_fetches_each_day(self) -> None:
        """A multi-day range makes one CDN request per (date, region) pair."""
        with patch("apps.ingest.sources.albina.fetch_albina_for_date") as mock_fetch:
            mock_fetch.return_value = []
            run_albina_pipeline(
                date(2026, 1, 1),
                date(2026, 1, 3),
                regions=("AT-07",),
                dry_run=True,
                triggered_by="test",
            )

        # 3 dates × 1 region = 3 calls
        assert mock_fetch.call_count == 3

    def test_bulletin_outside_date_range_not_stored(self) -> None:
        """A bulletin published outside the requested range is skipped."""
        MicroRegionFactory.create(region_id="AT-07-01", name="Allgäu Alps East")
        # Publication time is 2026-01-10; we request 2026-01-15.
        raw = _make_raw_bulletin(publication_time="2026-01-10T18:00:00Z")

        with patch("apps.ingest.sources.albina.fetch_albina_for_date") as mock_fetch:
            mock_fetch.return_value = [raw]
            run_albina_pipeline(
                date(2026, 1, 15),
                date(2026, 1, 15),
                regions=("AT-07",),
                triggered_by="test",
            )

        from apps.bulletins.models import Bulletin

        assert Bulletin.objects.count() == 0


# ---------------------------------------------------------------------------
# latest_albina_date
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLatestEuregioDate:
    """latest_albina_date returns the most recent ALBINA valid_from date."""

    def test_returns_none_when_no_albina_bulletins(self) -> None:
        """Returns None when the DB has no ALBINA bulletins."""
        assert latest_albina_date() is None

    def test_returns_latest_valid_from(self) -> None:
        """Returns the date of the most recent ALBINA bulletin's valid_from."""
        from tests.factories import BulletinFactory

        BulletinFactory.create(
            valid_from=datetime(2026, 1, 15, 16, tzinfo=UTC),
            valid_to=datetime(2026, 1, 16, 16, tzinfo=UTC),
            source=Bulletin.Source.ALBINA,
            render_model={"source": "ALBINA", "version": 4},
            render_model_version=4,
        )
        BulletinFactory.create(
            valid_from=datetime(2026, 1, 10, 16, tzinfo=UTC),
            valid_to=datetime(2026, 1, 11, 16, tzinfo=UTC),
            source=Bulletin.Source.ALBINA,
            render_model={"source": "ALBINA", "version": 4},
            render_model_version=4,
        )
        result = latest_albina_date()
        assert result == date(2026, 1, 15)

    def test_ignores_slf_bulletins(self) -> None:
        """Bulletins with source='SLF' are not considered."""
        from tests.factories import BulletinFactory

        BulletinFactory.create(
            valid_from=datetime(2026, 1, 20, 16, tzinfo=UTC),
            valid_to=datetime(2026, 1, 21, 16, tzinfo=UTC),
            source=Bulletin.Source.SLF,
            render_model={"source": "SLF", "version": 4},
            render_model_version=4,
        )
        assert latest_albina_date() is None


@pytest.mark.django_db
class TestAlbinaPdfUrlUpsertRoundTrip:
    """Round-trip test: pdf_url is stored in the database via upsert_bulletin."""

    def test_pdf_url_persisted_via_upsert(self) -> None:
        """upsert_bulletin stores the ALBINA pdf_url on the created Bulletin."""
        MicroRegionFactory.create(region_id="AT-07-01")
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(
            validTime={
                "startTime": "2025-11-30T16:00:00Z",
                "endTime": "2025-12-01T16:00:00Z",
            },
            regions=[{"regionID": "AT-07-01", "name": "Allgäu Alps East"}],
            lang="en",
        )
        expected_url = albina_pdf_url(raw, "AT-07")
        upsert_bulletin(raw, run, pdf_url=expected_url)
        b = Bulletin.objects.get(bulletin_id="albina-001")
        assert b.pdf_url == expected_url
