"""
tests/bulletins/services/test_openmeteo_archive.py — Tests for the Open-Meteo NDJSON archive.

Covers the pure functions in ``bulletins.services.openmeteo_archive``:
  - read_archive: round-trip, missing-file safety, blank-line tolerance
  - merge: dedup by (region_id, date) with later-captured_at-wins semantics,
    sort order, empty inputs
  - write_archive: atomic write, parent-dir creation, no leftover .tmp
"""

import json
from pathlib import Path
from typing import Any

from bulletins.services.openmeteo_archive import merge, read_archive, write_archive


def _record(
    region_id: str,
    date: str,
    weather_code: int = 3,
    captured_at: str = "2026-05-09T12:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal Open-Meteo archive record for tests."""
    base: dict[str, Any] = {
        "region_id": region_id,
        "date": date,
        "weather_code": weather_code,
        "sunrise": f"{date}T05:32+02:00",
        "sunset": f"{date}T20:14+02:00",
        "captured_at": captured_at,
    }
    base.update(overrides)
    return base


class TestReadArchive:
    """Tests for read_archive."""

    def test_missing_file_yields_nothing(self, tmp_path: Path) -> None:
        """A non-existent archive yields no records (no exception)."""
        path = tmp_path / "does_not_exist.ndjson"
        assert list(read_archive(path)) == []

    def test_empty_file_yields_nothing(self, tmp_path: Path) -> None:
        """An empty archive file yields no records."""
        path = tmp_path / "empty.ndjson"
        path.write_text("", encoding="utf-8")
        assert list(read_archive(path)) == []

    def test_round_trip_preserves_content_and_order(self, tmp_path: Path) -> None:
        """Records read back match what was written, in stored order."""
        path = tmp_path / "archive.ndjson"
        records = [
            _record("CH-1000", "2026-05-01"),
            _record("CH-1000", "2026-05-02"),
            _record("CH-2000", "2026-05-01"),
        ]
        write_archive(path, records)

        loaded = list(read_archive(path))
        assert loaded == records

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        """Blank lines in the archive are tolerated and skipped."""
        path = tmp_path / "archive.ndjson"
        record = _record("CH-1000", "2026-05-01")
        path.write_text(f"\n{json.dumps(record)}\n\n", encoding="utf-8")

        assert list(read_archive(path)) == [record]


class TestMerge:
    """Tests for merge."""

    def test_dedups_by_region_id_and_date_with_later_captured_at_wins(
        self,
    ) -> None:
        """When (region_id, date) matches, the later-captured_at record wins."""
        existing = [_record("CH-1000", "2026-05-01", weather_code=0, captured_at="2026-05-09T10:00:00Z")]
        new = [_record("CH-1000", "2026-05-01", weather_code=3, captured_at="2026-05-09T12:00:00Z")]

        result = merge(existing, new)

        assert len(result) == 1
        assert result[0]["weather_code"] == 3

    def test_existing_wins_if_newer_captured_at(self) -> None:
        """The existing record wins if its captured_at is strictly later than the new one."""
        existing = [_record("CH-1000", "2026-05-01", weather_code=99, captured_at="2026-05-09T14:00:00Z")]
        new = [_record("CH-1000", "2026-05-01", weather_code=0, captured_at="2026-05-09T12:00:00Z")]

        result = merge(existing, new)

        # new has earlier captured_at → existing wins
        assert len(result) == 1
        assert result[0]["weather_code"] == 99

    def test_new_wins_on_equal_captured_at(self) -> None:
        """When captured_at values are equal, the new record wins (processed last)."""
        ts = "2026-05-09T12:00:00Z"
        existing = [_record("CH-1000", "2026-05-01", weather_code=0, captured_at=ts)]
        new = [_record("CH-1000", "2026-05-01", weather_code=5, captured_at=ts)]

        result = merge(existing, new)

        assert len(result) == 1
        assert result[0]["weather_code"] == 5

    def test_combines_disjoint_records(self) -> None:
        """Records with distinct (region_id, date) keys are all kept."""
        existing = [_record("CH-1000", "2026-05-01")]
        new = [_record("CH-2000", "2026-05-01")]

        result = merge(existing, new)

        assert {(r["region_id"], r["date"]) for r in result} == {
            ("CH-1000", "2026-05-01"),
            ("CH-2000", "2026-05-01"),
        }

    def test_different_dates_same_region_both_kept(self) -> None:
        """Two records for the same region on different dates are both preserved."""
        existing = [_record("CH-1000", "2026-05-01")]
        new = [_record("CH-1000", "2026-05-02")]

        result = merge(existing, new)

        assert len(result) == 2

    def test_sorts_ascending_by_region_id_then_date(self) -> None:
        """Result is sorted ascending by (region_id, date)."""
        existing = [
            _record("CH-2000", "2026-05-01"),
            _record("CH-1000", "2026-05-02"),
        ]
        new = [_record("CH-1000", "2026-05-01")]

        result = merge(existing, new)

        assert [(r["region_id"], r["date"]) for r in result] == [
            ("CH-1000", "2026-05-01"),
            ("CH-1000", "2026-05-02"),
            ("CH-2000", "2026-05-01"),
        ]

    def test_handles_empty_inputs(self) -> None:
        """Both empty → empty result; one empty → the other returned sorted."""
        assert merge([], []) == []
        new = [_record("CH-1000", "2026-05-01")]
        assert merge([], new) == new
        assert merge(new, []) == new


class TestWriteArchive:
    """Tests for write_archive."""

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        """Parent directories are created if they don't yet exist."""
        path = tmp_path / "nested" / "deeper" / "archive.ndjson"
        records = [_record("CH-1000", "2026-05-01")]

        write_archive(path, records)

        assert path.exists()
        assert list(read_archive(path)) == records

    def test_replaces_existing_file_atomically(self, tmp_path: Path) -> None:
        """Re-writing replaces the file — no leftover .tmp."""
        path = tmp_path / "archive.ndjson"
        write_archive(path, [_record("CH-1000", "2026-05-01")])
        write_archive(path, [_record("CH-2000", "2026-05-02")])

        loaded = list(read_archive(path))
        assert len(loaded) == 1
        assert loaded[0]["region_id"] == "CH-2000"
        assert not path.with_suffix(path.suffix + ".tmp").exists()

    def test_writes_one_record_per_line(self, tmp_path: Path) -> None:
        """Each record serialises onto its own line (NDJSON)."""
        path = tmp_path / "archive.ndjson"
        records = [
            _record("CH-1000", "2026-05-01"),
            _record("CH-1000", "2026-05-02"),
        ]

        write_archive(path, records)

        lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["region_id"] == "CH-1000"
        assert json.loads(lines[0])["date"] == "2026-05-01"
        assert json.loads(lines[1])["date"] == "2026-05-02"

    def test_empty_records_writes_empty_file(self, tmp_path: Path) -> None:
        """Writing an empty list produces an empty file (no partial lines)."""
        path = tmp_path / "archive.ndjson"
        write_archive(path, [])

        assert path.exists()
        assert path.read_text(encoding="utf-8") == ""
