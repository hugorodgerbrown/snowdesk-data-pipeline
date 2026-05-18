"""
tests/bulletins/services/test_meteofrance_fetcher.py — Tests for the MeteoFrance fetcher.

Covers:
  - fetch_meteofrance_bulletin: HTTP success, 404 → None, HTTP error, live URL
    construction, missing API key error, file:// local mirror path.
  - _read_local_mirror: file present → bytes, file absent → None.
  - run_meteofrance_pipeline: dry-run no writes, force vs. dedup, delegated
    skip without failure count, translation error increments records_failed,
    HTTP error increments records_failed, on_fetched callback, PipelineRun
    lifecycle.
  - latest_meteofrance_date: returns None when DB empty, latest date otherwise.
  - meteofrance_stash_writer: merge, dedup, sort, atomic write.

DB tests use MicroRegionFactory to seed the regions referenced by test XMLs
(FR-01 = Chablais). HTTP calls are mocked via unittest.mock.patch.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from bulletins.services.meteofrance_fetcher import (
    _read_local_mirror,
    fetch_meteofrance_bulletin,
    latest_meteofrance_date,
    meteofrance_stash_writer,
    run_meteofrance_pipeline,
)
from tests.factories import MicroRegionFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "research"
    / "meteofrance"
    / "bulletins-2026-05-18"
)


def _sample(filename: str) -> bytes:
    """Return raw bytes from the research sample directory."""
    return (_SAMPLE_DIR / filename).read_bytes()


def _mock_ok(content: bytes) -> MagicMock:
    """Build a mock requests.Response with status 200 and given content."""
    mock = MagicMock()
    mock.status_code = 200
    mock.content = content
    mock.raise_for_status.return_value = None
    return mock


def _mock_404() -> MagicMock:
    """Build a mock requests.Response with status 404."""
    mock = MagicMock()
    mock.status_code = 404
    mock.raise_for_status.return_value = None
    return mock


def _mock_http_error(status: int = 500) -> MagicMock:
    """Build a mock requests.Response that raises on raise_for_status."""
    mock = MagicMock()
    mock.status_code = status
    mock.raise_for_status.side_effect = requests.HTTPError(
        f"HTTP {status}", response=mock
    )
    return mock


# ---------------------------------------------------------------------------
# fetch_meteofrance_bulletin — HTTP path
# ---------------------------------------------------------------------------


class TestFetchMeteofrance:
    """fetch_meteofrance_bulletin handles HTTP and file:// paths."""

    @patch("bulletins.services.meteofrance_fetcher.requests.get")
    def test_returns_bytes_on_200(self, mock_get: MagicMock) -> None:
        """A 200 response returns the content bytes."""
        mock_get.return_value = _mock_ok(b"<xml/>")
        result = fetch_meteofrance_bulletin(1, "https://api.example.com", "key123")
        assert result == b"<xml/>"

    @patch("bulletins.services.meteofrance_fetcher.requests.get")
    def test_returns_none_on_404(self, mock_get: MagicMock) -> None:
        """A 404 response returns None (no bulletin today)."""
        mock_get.return_value = _mock_404()
        result = fetch_meteofrance_bulletin(1, "https://api.example.com", "key123")
        assert result is None

    @patch("bulletins.services.meteofrance_fetcher.requests.get")
    def test_raises_on_http_error(self, mock_get: MagicMock) -> None:
        """A 500 response raises requests.HTTPError."""
        mock_get.return_value = _mock_http_error(500)
        with pytest.raises(requests.HTTPError):
            fetch_meteofrance_bulletin(1, "https://api.example.com", "key123")

    @patch("bulletins.services.meteofrance_fetcher.requests.get")
    def test_url_uses_massif_id(self, mock_get: MagicMock) -> None:
        """The request URL includes the massif ID at the correct path."""
        mock_get.return_value = _mock_ok(b"<xml/>")
        fetch_meteofrance_bulletin(7, "https://api.example.com", "key")
        call_url = mock_get.call_args[0][0]
        assert "/massif/7/BRA" in call_url

    @patch("bulletins.services.meteofrance_fetcher.requests.get")
    def test_api_key_sent_as_header(self, mock_get: MagicMock) -> None:
        """The apikey header is sent with the request."""
        mock_get.return_value = _mock_ok(b"<xml/>")
        fetch_meteofrance_bulletin(1, "https://api.example.com", "my-secret-key")
        headers = mock_get.call_args[1]["headers"]
        assert headers.get("apikey") == "my-secret-key"

    def test_raises_when_no_api_key_and_live_url(self) -> None:
        """An empty api_key with a live URL raises RuntimeError."""
        with pytest.raises(RuntimeError, match="METEOFRANCE_API_KEY"):
            fetch_meteofrance_bulletin(1, "https://api.example.com", "")


# ---------------------------------------------------------------------------
# _read_local_mirror
# ---------------------------------------------------------------------------


class TestReadLocalMirror:
    """_read_local_mirror reads files from a file:// directory."""

    def test_returns_bytes_when_file_exists(self) -> None:
        """When massif-001.xml is present, returns its bytes."""
        with tempfile.TemporaryDirectory() as tmp:
            massif_file = Path(tmp) / "massif-001.xml"
            massif_file.write_bytes(b"<test/>")
            result = _read_local_mirror(1, f"file://{tmp}")
            assert result == b"<test/>"

    def test_returns_none_when_file_absent(self) -> None:
        """Missing file for a massif returns None (mirrors HTTP 404 semantics)."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _read_local_mirror(99, f"file://{tmp}")
            assert result is None

    def test_file_path_uses_three_digit_padding(self) -> None:
        """The file name is massif-{id:03d}.xml (zero-padded to 3 digits)."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create massif-005.xml (not massif-5.xml)
            (Path(tmp) / "massif-005.xml").write_bytes(b"<padded/>")
            result = _read_local_mirror(5, f"file://{tmp}")
            assert result == b"<padded/>"


# ---------------------------------------------------------------------------
# fetch_meteofrance_bulletin — local-mirror path
# ---------------------------------------------------------------------------


class TestFetchMeteofrancoLocalMirror:
    """fetch_meteofrance_bulletin reads from file:// without network I/O."""

    def test_returns_bytes_from_local_mirror(self) -> None:
        """A file:// base_url reads from the local directory, no HTTP call."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(b"<local/>")
            result = fetch_meteofrance_bulletin(1, f"file://{tmp}", "")
            assert result == b"<local/>"

    def test_no_api_key_required_for_local_mirror(self) -> None:
        """Empty api_key is fine for file:// mirrors."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-002.xml").write_bytes(b"<local/>")
            # Must not raise even though api_key is ""
            result = fetch_meteofrance_bulletin(2, f"file://{tmp}", "")
            assert result == b"<local/>"


# ---------------------------------------------------------------------------
# run_meteofrance_pipeline — dry-run (no DB needed)
# ---------------------------------------------------------------------------


class TestRunMeteofrance:
    """run_meteofrance_pipeline: pipeline lifecycle and outcome counting."""

    @pytest.mark.django_db
    def test_dry_run_does_not_write_bulletins(self) -> None:
        """dry_run=True fetches, translates, but does not write to the DB."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        from bulletins.models import Bulletin

        assert Bulletin.objects.count() == 0
        assert run.records_created == 0

    @pytest.mark.django_db
    def test_delegated_massif_is_skipped_silently(self) -> None:
        """A delegated massif (massif-071) does not increment records_failed."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-071.xml").write_bytes(_sample("massif-071.xml"))
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(71,),
                base_url=f"file://{tmp}",
            )

        assert run.records_failed == 0

    @pytest.mark.django_db
    def test_translation_error_increments_records_failed(self) -> None:
        """Invalid XML increments records_failed on the PipelineRun."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(b"<invalid xml<<>")
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        assert run.records_failed == 1

    @pytest.mark.django_db
    def test_http_error_increments_records_failed(self) -> None:
        """An HTTP error for a massif increments records_failed."""
        with patch(
            "bulletins.services.meteofrance_fetcher.fetch_meteofrance_bulletin"
        ) as mock_fetch:
            mock_fetch.side_effect = requests.HTTPError("500 Server Error")
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(1,),
                base_url="https://api.example.com",
            )

        assert run.records_failed == 1

    @pytest.mark.django_db
    def test_on_fetched_callback_called_per_bulletin(self) -> None:
        """on_fetched is invoked once for each successfully translated bulletin."""
        collected: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            (Path(tmp) / "massif-064.xml").write_bytes(_sample("massif-064.xml"))
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(1, 64),
                base_url=f"file://{tmp}",
                on_fetched=collected.append,
            )

        assert len(collected) == 2
        ids = {r["bulletinID"] for r in collected}
        assert "FR-01-2026-05-18" in ids
        assert "FR-64-2026-05-18" in ids

    @pytest.mark.django_db
    def test_creates_bulletin_for_known_region(self) -> None:
        """A bulletin whose region is seeded in the DB is persisted."""
        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        from bulletins.models import Bulletin

        assert Bulletin.objects.filter(bulletin_id="FR-01-2026-05-18").exists()
        assert run.records_created == 1

    @pytest.mark.django_db
    def test_dedup_skips_existing_bulletin(self) -> None:
        """A bulletin that already exists in the DB is skipped (no force)."""
        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            # First run creates the bulletin.
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )
            # Second run should skip it (force=False).
            run2 = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        from bulletins.models import Bulletin

        # Still only one bulletin in the DB.
        assert Bulletin.objects.filter(bulletin_id="FR-01-2026-05-18").count() == 1
        assert run2.records_created == 0


# ---------------------------------------------------------------------------
# latest_meteofrance_date
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLatestMeteofrancoDate:
    """latest_meteofrance_date returns None or the most recent FR- bulletin date."""

    def test_returns_none_when_no_bulletins(self) -> None:
        """Empty DB returns None."""
        result = latest_meteofrance_date()
        assert result is None

    def test_returns_latest_date_when_bulletins_exist(self) -> None:
        """Returns the valid_from date of the most recent FR- bulletin."""
        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        result = latest_meteofrance_date()
        assert result is not None
        # massif-001 bulletin is valid from 2026-05-17T16:00 Paris (UTC+2)
        # = 2026-05-17T14:00:00Z, so .date() == 2026-05-17
        assert result == date(2026, 5, 17)

    def test_ignores_non_fr_bulletins(self) -> None:
        """Non-FR- bulletin IDs (e.g. SLF) are not counted."""
        from bulletins.models import Bulletin, PipelineRun

        run = PipelineRun.objects.create(triggered_by="test")
        Bulletin.objects.create(
            bulletin_id="SLF-001",
            lang="en",
            issued_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
            valid_to=datetime(2026, 5, 19, 0, 0, tzinfo=UTC),
            pipeline_run=run,
            raw_data={"type": "Feature", "geometry": None, "properties": {}},
            render_model={},
            render_model_version=0,
        )
        result = latest_meteofrance_date()
        assert result is None


# ---------------------------------------------------------------------------
# meteofrance_stash_writer
# ---------------------------------------------------------------------------


class TestMeteofranceStashWriter:
    """meteofrance_stash_writer merges and dedupes records into an NDJSON archive."""

    def test_creates_new_archive_when_path_absent(self) -> None:
        """A non-existent path is created with the supplied records."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "archive.ndjson"
            records = [
                {
                    "bulletinID": "FR-01-2026-05-18",
                    "validTime": {"startTime": "2026-05-17T14:00:00Z"},
                }
            ]
            count = meteofrance_stash_writer(records, path)

            assert count == 1
            assert path.exists()
            lines = [json.loads(ln) for ln in path.read_text().splitlines()]
            assert lines[0]["bulletinID"] == "FR-01-2026-05-18"

    def test_merges_new_records_with_existing(self) -> None:
        """New records are merged with existing archive contents."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.ndjson"
            existing = {
                "bulletinID": "FR-01-2026-05-17",
                "validTime": {"startTime": "2026-05-16T14:00:00Z"},
            }
            path.write_text(json.dumps(existing) + "\n")

            new_record = {
                "bulletinID": "FR-01-2026-05-18",
                "validTime": {"startTime": "2026-05-17T14:00:00Z"},
            }
            count = meteofrance_stash_writer([new_record], path)

            assert count == 2

    def test_dedupes_by_bulletin_id(self) -> None:
        """A new record with the same bulletinID replaces the existing one."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.ndjson"
            old = {
                "bulletinID": "FR-01-2026-05-18",
                "validTime": {"startTime": "2026-05-17T14:00:00Z"},
                "v": 1,
            }
            path.write_text(json.dumps(old) + "\n")

            updated = {
                "bulletinID": "FR-01-2026-05-18",
                "validTime": {"startTime": "2026-05-17T14:00:00Z"},
                "v": 2,
            }
            count = meteofrance_stash_writer([updated], path)

            assert count == 1
            lines = [json.loads(ln) for ln in path.read_text().splitlines()]
            assert lines[0]["v"] == 2

    def test_sorted_ascending_by_valid_time(self) -> None:
        """Archive is sorted ascending by validTime.startTime."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.ndjson"
            r1 = {
                "bulletinID": "FR-01-2026-05-18",
                "validTime": {"startTime": "2026-05-17T14:00:00Z"},
            }
            r2 = {
                "bulletinID": "FR-01-2026-05-17",
                "validTime": {"startTime": "2026-05-16T14:00:00Z"},
            }
            meteofrance_stash_writer([r1, r2], path)

            lines = [json.loads(ln) for ln in path.read_text().splitlines()]
            assert lines[0]["bulletinID"] == "FR-01-2026-05-17"
            assert lines[1]["bulletinID"] == "FR-01-2026-05-18"

    def test_returns_total_record_count(self) -> None:
        """Return value is the total archive size after the merge."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.ndjson"
            records = [
                {
                    "bulletinID": f"FR-0{i}-2026-05-18",
                    "validTime": {"startTime": "2026-05-17T14:00:00Z"},
                }
                for i in range(1, 6)
            ]
            count = meteofrance_stash_writer(records, path)

            assert count == 5
