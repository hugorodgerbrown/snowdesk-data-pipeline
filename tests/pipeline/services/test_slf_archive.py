"""
tests/pipeline/services/test_slf_archive.py — Tests for the SLF NDJSON archive.

Covers the pure functions in ``pipeline.services.slf_archive``:
  - read_archive: round-trip, missing-file safety, blank-line tolerance
  - merge: dedup by bulletinID with later-wins semantics, sort order
  - write_archive: atomic write, parent-dir creation
"""

import json
from pathlib import Path
from typing import Any

from pipeline.services.slf_archive import merge, read_archive, write_archive


def _record(bulletin_id: str, start_time: str, **overrides: Any) -> dict[str, Any]:
    """Build a minimal CAAML-shaped record for archive tests."""
    base: dict[str, Any] = {
        "bulletinID": bulletin_id,
        "publicationTime": start_time.replace("17:00:00", "08:00:00"),
        "validTime": {
            "startTime": start_time,
            "endTime": start_time.replace("17:00:00", "17:00:01"),
        },
        "lang": "en",
    }
    base.update(overrides)
    return base


class TestReadArchive:
    """Tests for read_archive."""

    def test_missing_file_yields_nothing(self, tmp_path: Path) -> None:
        """A non-existent archive yields no records (no exception)."""
        path = tmp_path / "does_not_exist.ndjson"
        assert list(read_archive(path)) == []

    def test_round_trip_preserves_content_and_order(self, tmp_path: Path) -> None:
        """Records read back match what was written, in stored order."""
        path = tmp_path / "archive.ndjson"
        records = [
            _record("a", "2025-03-14T17:00:00Z"),
            _record("b", "2025-03-15T17:00:00Z"),
            _record("c", "2025-03-16T17:00:00Z"),
        ]
        write_archive(path, records)

        loaded = list(read_archive(path))
        assert loaded == records

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        """Blank lines in the archive are tolerated and skipped."""
        path = tmp_path / "archive.ndjson"
        record = _record("a", "2025-03-14T17:00:00Z")
        path.write_text(f"\n{json.dumps(record)}\n\n", encoding="utf-8")

        assert list(read_archive(path)) == [record]


class TestMerge:
    """Tests for merge."""

    def test_dedups_by_bulletin_id_with_later_wins(self) -> None:
        """When a record's bulletinID exists in both, the new one wins."""
        existing = [_record("a", "2025-03-14T17:00:00Z", lang="en")]
        new = [_record("a", "2025-03-14T17:00:00Z", lang="de")]

        result = merge(existing, new)

        assert len(result) == 1
        assert result[0]["lang"] == "de"

    def test_combines_disjoint_records(self) -> None:
        """Records with distinct ids are all kept."""
        existing = [_record("a", "2025-03-14T17:00:00Z")]
        new = [_record("b", "2025-03-15T17:00:00Z")]

        result = merge(existing, new)

        assert {r["bulletinID"] for r in result} == {"a", "b"}

    def test_sorts_ascending_by_valid_time_start(self) -> None:
        """Result is sorted ascending by validTime.startTime."""
        existing = [
            _record("a", "2025-03-16T17:00:00Z"),
            _record("b", "2025-03-14T17:00:00Z"),
        ]
        new = [_record("c", "2025-03-15T17:00:00Z")]

        result = merge(existing, new)

        assert [r["bulletinID"] for r in result] == ["b", "c", "a"]

    def test_handles_empty_inputs(self) -> None:
        """Both empty → empty result; one empty → the other returned sorted."""
        assert merge([], []) == []
        new = [_record("a", "2025-03-14T17:00:00Z")]
        assert merge([], new) == new


class TestWriteArchive:
    """Tests for write_archive."""

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        """Parent directories are created if they don't yet exist."""
        path = tmp_path / "nested" / "deeper" / "archive.ndjson"
        records = [_record("a", "2025-03-14T17:00:00Z")]

        write_archive(path, records)

        assert path.exists()
        assert list(read_archive(path)) == records

    def test_replaces_existing_file_atomically(self, tmp_path: Path) -> None:
        """Re-writing replaces the file — no leftover .tmp."""
        path = tmp_path / "archive.ndjson"
        write_archive(path, [_record("a", "2025-03-14T17:00:00Z")])
        write_archive(path, [_record("b", "2025-03-15T17:00:00Z")])

        assert list(read_archive(path)) == [_record("b", "2025-03-15T17:00:00Z")]
        assert not path.with_suffix(path.suffix + ".tmp").exists()

    def test_writes_one_record_per_line(self, tmp_path: Path) -> None:
        """Each record serialises onto its own line (NDJSON)."""
        path = tmp_path / "archive.ndjson"
        records = [
            _record("a", "2025-03-14T17:00:00Z"),
            _record("b", "2025-03-15T17:00:00Z"),
        ]

        write_archive(path, records)

        lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["bulletinID"] == "a"
        assert json.loads(lines[1])["bulletinID"] == "b"
