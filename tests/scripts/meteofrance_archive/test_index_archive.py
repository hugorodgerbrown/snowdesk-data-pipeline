# test_index_archive.py — Tests for Stage 1: mf_bra_index_archive.py
"""Tests for the BRA daily index archiver (Stage 1)."""

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from mf_bra_index_archive import (  # noqa: E402
    fetch_index,
    is_in_season,
    load_existing_dates,
    run,
)


class TestIsInSeason:
    """Tests for the is_in_season() helper."""

    @pytest.mark.parametrize("month", [11, 12, 1, 2, 3, 4])
    def test_season_months_are_in_season(self, month: int) -> None:
        """All avalanche-season months (Nov–Apr) should return True."""
        year = 2025 if month >= 11 else 2026
        assert is_in_season(date(year, month, 15)) is True

    @pytest.mark.parametrize("month", [5, 6, 7, 8, 9, 10])
    def test_off_season_months_are_not_in_season(self, month: int) -> None:
        """Summer/autumn months (May–Oct) should return False."""
        assert is_in_season(date(2025, month, 15)) is False


class TestFetchIndex:
    """Tests for fetch_index() against mocked HTTP responses."""

    def _make_session(
        self, status_code: int, json_body: object | None = None
    ) -> MagicMock:
        """Return a mock requests.Session that returns the given response."""
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = status_code
        if json_body is not None:
            resp.json.return_value = json_body
        if status_code >= 400:
            from requests.exceptions import HTTPError

            resp.raise_for_status.side_effect = HTTPError(response=resp)
        else:
            resp.raise_for_status.return_value = None
        session.get.return_value = resp
        return session

    def test_ok_response_returns_ok_status(self) -> None:
        """A 200 response should produce a record with status='ok'."""
        massifs = [{"massif": "CHABLAIS", "heures": ["20260521140706"]}]
        session = self._make_session(200, massifs)
        record = fetch_index("20260521", session)
        assert record["status"] == "ok"
        assert record["massifs"] == massifs

    def test_404_response_returns_not_found(self) -> None:
        """A 404 response should produce a record with status='not_found'."""
        session = self._make_session(404)
        record = fetch_index("20260521", session)
        assert record["status"] == "not_found"

    def test_500_response_returns_error_with_status_code(self) -> None:
        """A 500 response should produce a record with status starting 'error:'."""
        session = self._make_session(500)
        record = fetch_index("20260521", session)
        assert str(record["status"]).startswith("error:")

    def test_record_always_contains_date(self) -> None:
        """Every record should include the date string."""
        session = self._make_session(404)
        record = fetch_index("20260521", session)
        assert record["date"] == "20260521"

    def test_network_error_returns_error_status(self) -> None:
        """A network exception should produce a record with status starting 'error:'."""
        from requests.exceptions import ConnectionError as ConnError

        session = MagicMock()
        session.get.side_effect = ConnError("unreachable")
        record = fetch_index("20260521", session)
        assert str(record["status"]).startswith("error:")


class TestLoadExistingDates:
    """Tests for load_existing_dates()."""

    def test_empty_file_returns_empty_set(self, tmp_path: Path) -> None:
        """An empty NDJSON file should return an empty set."""
        f = tmp_path / "bra_indexes.ndjson"
        f.write_text("")
        assert load_existing_dates(f) == set()

    def test_missing_file_returns_empty_set(self, tmp_path: Path) -> None:
        """A missing file should return an empty set without raising."""
        f = tmp_path / "does_not_exist.ndjson"
        assert load_existing_dates(f) == set()

    def test_returns_date_strings_from_ndjson(self, tmp_path: Path) -> None:
        """Dates from NDJSON records should be in the returned set."""
        f = tmp_path / "bra_indexes.ndjson"
        records = [
            {"date": "20260101", "status": "ok", "massifs": []},
            {"date": "20260102", "status": "not_found", "massifs": []},
        ]
        f.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        result = load_existing_dates(f)
        assert result == {"20260101", "20260102"}

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        """Malformed JSON lines should be silently skipped."""
        f = tmp_path / "bra_indexes.ndjson"
        f.write_text('{"date": "20260101", "status": "ok", "massifs": []}\nnot-json\n')
        result = load_existing_dates(f)
        assert "20260101" in result


class TestRun:
    """Integration tests for the run() function."""

    def _mock_session(self, responses: dict[str, dict[str, object]]) -> MagicMock:
        """Return a mock session that maps date strings to records."""
        session = MagicMock()

        def _get(url: str, timeout: int = 30) -> MagicMock:
            for date_str, record in responses.items():
                if date_str in url:
                    resp = MagicMock()
                    if record.get("status") == "not_found":
                        resp.status_code = 404
                        resp.raise_for_status.return_value = None
                    else:
                        resp.status_code = 200
                        resp.json.return_value = record.get("massifs", [])
                        resp.raise_for_status.return_value = None
                    return resp
            resp = MagicMock()
            resp.status_code = 404
            resp.raise_for_status.return_value = None
            return resp

        session.get.side_effect = _get
        return session

    @patch("mf_bra_index_archive.time.sleep")
    @patch("mf_bra_index_archive.requests.Session")
    def test_off_season_dates_are_skipped(
        self, mock_session_cls: MagicMock, mock_sleep: MagicMock, tmp_path: Path
    ) -> None:
        """Dates outside Nov–Apr should be counted as skipped_season, not fetched."""
        mock_session_cls.return_value = MagicMock()
        output = tmp_path / "out.ndjson"
        counts = run(
            start=date(2025, 5, 1),
            end=date(2025, 5, 3),
            output_path=output,
            commit=False,
            verbosity=0,
        )
        assert counts["skipped_season"] == 3
        assert counts["fetched"] == 0
        mock_session_cls.return_value.get.assert_not_called()

    @patch("mf_bra_index_archive.time.sleep")
    @patch("mf_bra_index_archive.requests.Session")
    def test_existing_dates_are_skipped(
        self, mock_session_cls: MagicMock, mock_sleep: MagicMock, tmp_path: Path
    ) -> None:
        """Dates already in the output file should be skipped without HTTP requests."""
        existing = tmp_path / "out.ndjson"
        existing.write_text('{"date": "20251101", "status": "ok", "massifs": []}\n')
        mock_session_cls.return_value = MagicMock()

        counts = run(
            start=date(2025, 11, 1),
            end=date(2025, 11, 1),
            output_path=existing,
            commit=False,
            verbosity=0,
        )
        assert counts["skipped_existing"] == 1
        assert counts["fetched"] == 0
        mock_session_cls.return_value.get.assert_not_called()

    @patch("mf_bra_index_archive.time.sleep")
    @patch("mf_bra_index_archive.fetch_index")
    def test_commit_writes_ndjson(
        self, mock_fetch: MagicMock, mock_sleep: MagicMock, tmp_path: Path
    ) -> None:
        """With --commit, fetched records should be written to the output file."""
        mock_fetch.return_value = {
            "date": "20251101",
            "status": "ok",
            "massifs": [{"massif": "CHABLAIS", "heures": ["20251101120000"]}],
        }
        output = tmp_path / "out.ndjson"
        run(
            start=date(2025, 11, 1),
            end=date(2025, 11, 1),
            output_path=output,
            commit=True,
            verbosity=0,
        )
        assert output.exists()
        lines = [ln for ln in output.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["date"] == "20251101"

    @patch("mf_bra_index_archive.time.sleep")
    @patch("mf_bra_index_archive.fetch_index")
    def test_dry_run_does_not_write(
        self, mock_fetch: MagicMock, mock_sleep: MagicMock, tmp_path: Path
    ) -> None:
        """Without --commit, no output file should be written."""
        mock_fetch.return_value = {"date": "20251101", "status": "ok", "massifs": []}
        output = tmp_path / "out.ndjson"
        run(
            start=date(2025, 11, 1),
            end=date(2025, 11, 1),
            output_path=output,
            commit=False,
            verbosity=0,
        )
        assert not output.exists()

    @patch("mf_bra_index_archive.time.sleep")
    @patch("mf_bra_index_archive.fetch_index")
    def test_error_status_increments_errors_count(
        self, mock_fetch: MagicMock, mock_sleep: MagicMock, tmp_path: Path
    ) -> None:
        """Error responses should increment the errors counter."""
        mock_fetch.return_value = {
            "date": "20251101",
            "status": "error:500",
            "massifs": [],
        }
        output = tmp_path / "out.ndjson"
        counts = run(
            start=date(2025, 11, 1),
            end=date(2025, 11, 1),
            output_path=output,
            commit=False,
            verbosity=0,
        )
        assert counts["errors"] == 1

    @patch("mf_bra_index_archive.time.sleep")
    @patch("mf_bra_index_archive.fetch_index")
    def test_output_is_valid_ndjson(
        self, mock_fetch: MagicMock, mock_sleep: MagicMock, tmp_path: Path
    ) -> None:
        """Each line in the output file should be valid JSON."""
        mock_fetch.return_value = {"date": "20251101", "status": "ok", "massifs": []}
        output = tmp_path / "out.ndjson"
        run(
            start=date(2025, 11, 1),
            end=date(2025, 11, 1),
            output_path=output,
            commit=True,
            verbosity=0,
        )
        for line in output.read_text().splitlines():
            if line.strip():
                json.loads(line)  # should not raise
