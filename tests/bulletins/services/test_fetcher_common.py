"""
tests/bulletins/services/test_fetcher_common.py — Tests for fetcher_common helpers.

Covers:
  - merge_ndjson_records: dedup by bulletinID (later wins), sort order,
    empty inputs, records without bulletinID are dropped.
  - write_ndjson_archive: creates file, merges with existing content,
    returns total count, atomic write (no leftover .tmp), parent-dir creation.
  - normalise_bulletin_response: all three envelope shapes (flat list,
    {"bulletins": [...]}, list-of-collections), plus invalid/unexpected
    input behaviour.
  - OUTCOME_* constants: correct string values.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from apps.bulletins.services.fetcher_common import (
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_UPDATED,
    merge_ndjson_records,
    normalise_bulletin_response,
    write_ndjson_archive,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    bulletin_id: str,
    start_time: str = "2025-03-14T17:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal bulletin record for archive tests."""
    base: dict[str, Any] = {
        "bulletinID": bulletin_id,
        "validTime": {"startTime": start_time},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# OUTCOME constants
# ---------------------------------------------------------------------------


class TestOutcomeConstants:
    """The shared outcome constants carry the expected string values."""

    def test_created(self) -> None:
        """OUTCOME_CREATED is 'created'."""
        assert OUTCOME_CREATED == "created"

    def test_updated(self) -> None:
        """OUTCOME_UPDATED is 'updated'."""
        assert OUTCOME_UPDATED == "updated"

    def test_skipped(self) -> None:
        """OUTCOME_SKIPPED is 'skipped'."""
        assert OUTCOME_SKIPPED == "skipped"

    def test_failed(self) -> None:
        """OUTCOME_FAILED is 'failed'."""
        assert OUTCOME_FAILED == "failed"


# ---------------------------------------------------------------------------
# merge_ndjson_records
# ---------------------------------------------------------------------------


class TestMergeNdjsonRecords:
    """merge_ndjson_records dedupes by bulletinID and sorts by validTime."""

    def test_later_record_wins_on_duplicate_id(self) -> None:
        """A record in ``new`` overwrites an existing one with the same bulletinID."""
        existing = [_record("a", "2025-03-14T17:00:00Z", lang="en")]
        new = [_record("a", "2025-03-14T17:00:00Z", lang="de")]

        result = merge_ndjson_records(existing, new)

        assert len(result) == 1
        assert result[0]["lang"] == "de"

    def test_disjoint_records_all_kept(self) -> None:
        """Records with distinct ids from both inputs are all preserved."""
        existing = [_record("a", "2025-03-14T17:00:00Z")]
        new = [_record("b", "2025-03-15T17:00:00Z")]

        result = merge_ndjson_records(existing, new)

        assert {r["bulletinID"] for r in result} == {"a", "b"}

    def test_sorted_ascending_by_valid_time_start(self) -> None:
        """Result is sorted ascending by validTime.startTime."""
        existing = [
            _record("a", "2025-03-16T17:00:00Z"),
            _record("b", "2025-03-14T17:00:00Z"),
        ]
        new = [_record("c", "2025-03-15T17:00:00Z")]

        result = merge_ndjson_records(existing, new)

        assert [r["bulletinID"] for r in result] == ["b", "c", "a"]

    def test_both_empty(self) -> None:
        """Both inputs empty returns an empty list."""
        assert merge_ndjson_records([], []) == []

    def test_empty_existing(self) -> None:
        """Empty existing with new records returns the new records sorted."""
        new = [_record("a", "2025-03-15T17:00:00Z")]
        result = merge_ndjson_records([], new)
        assert result == new

    def test_empty_new(self) -> None:
        """Existing records with empty new returns the existing records sorted."""
        existing = [_record("a", "2025-03-15T17:00:00Z")]
        result = merge_ndjson_records(existing, [])
        assert result == existing

    def test_records_without_bulletin_id_dropped(self) -> None:
        """Records lacking bulletinID are silently excluded from the merge."""
        existing = [{"validTime": {"startTime": "2025-03-14T17:00:00Z"}}]
        new = [_record("a", "2025-03-15T17:00:00Z")]

        result = merge_ndjson_records(existing, new)

        assert len(result) == 1
        assert result[0]["bulletinID"] == "a"

    def test_new_records_without_bulletin_id_dropped(self) -> None:
        """New records lacking bulletinID are dropped even if existing are present."""
        existing = [_record("a", "2025-03-14T17:00:00Z")]
        new = [{"validTime": {"startTime": "2025-03-15T17:00:00Z"}}]

        result = merge_ndjson_records(existing, new)

        assert len(result) == 1
        assert result[0]["bulletinID"] == "a"

    def test_missing_valid_time_sorts_to_front(self) -> None:
        """A record with no validTime.startTime gets an empty sort key (sorts first)."""
        record_with_no_time: dict[str, Any] = {"bulletinID": "no-time"}
        normal = _record("a", "2025-03-15T17:00:00Z")

        result = merge_ndjson_records([record_with_no_time], [normal])

        assert result[0]["bulletinID"] == "no-time"
        assert result[1]["bulletinID"] == "a"

    def test_multiple_new_records(self) -> None:
        """Multiple new records are all added when they have distinct IDs."""
        new = [
            _record("x", "2025-03-15T17:00:00Z"),
            _record("y", "2025-03-16T17:00:00Z"),
            _record("z", "2025-03-17T17:00:00Z"),
        ]
        result = merge_ndjson_records([], new)
        assert len(result) == 3
        assert [r["bulletinID"] for r in result] == ["x", "y", "z"]


# ---------------------------------------------------------------------------
# write_ndjson_archive
# ---------------------------------------------------------------------------


class TestWriteNdjsonArchive:
    """write_ndjson_archive reads, merges, and writes atomically."""

    def test_creates_new_file_when_absent(self, tmp_path: Path) -> None:
        """A non-existent path is created containing the supplied records."""
        path = tmp_path / "archive.ndjson"
        records = [_record("a", "2025-03-14T17:00:00Z")]

        count = write_ndjson_archive(records, path)

        assert count == 1
        assert path.exists()
        lines = [json.loads(ln) for ln in path.read_text().splitlines()]
        assert lines[0]["bulletinID"] == "a"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        """Parent directories are created automatically."""
        path = tmp_path / "nested" / "deeper" / "archive.ndjson"
        records = [_record("a", "2025-03-14T17:00:00Z")]

        write_ndjson_archive(records, path)

        assert path.exists()

    def test_merges_new_records_with_existing(self, tmp_path: Path) -> None:
        """New records are overlaid on top of existing archive contents."""
        path = tmp_path / "archive.ndjson"
        existing = _record("old", "2025-03-10T17:00:00Z")
        path.write_text(json.dumps(existing) + "\n", encoding="utf-8")

        new_record = _record("new", "2025-03-15T17:00:00Z")
        count = write_ndjson_archive([new_record], path)

        assert count == 2
        lines = [json.loads(ln) for ln in path.read_text().splitlines()]
        assert {r["bulletinID"] for r in lines} == {"old", "new"}

    def test_dedupes_by_bulletin_id(self, tmp_path: Path) -> None:
        """A new record with the same bulletinID replaces the existing one."""
        path = tmp_path / "archive.ndjson"
        old = _record("a", "2025-03-14T17:00:00Z", v=1)
        path.write_text(json.dumps(old) + "\n", encoding="utf-8")

        updated = _record("a", "2025-03-14T17:00:00Z", v=2)
        count = write_ndjson_archive([updated], path)

        assert count == 1
        lines = [json.loads(ln) for ln in path.read_text().splitlines()]
        assert lines[0]["v"] == 2

    def test_sorted_ascending_by_valid_time(self, tmp_path: Path) -> None:
        """Archive is sorted ascending by validTime.startTime after merge."""
        path = tmp_path / "archive.ndjson"
        r1 = _record("late", "2025-03-16T17:00:00Z")
        r2 = _record("early", "2025-03-14T17:00:00Z")

        write_ndjson_archive([r1, r2], path)

        lines = [json.loads(ln) for ln in path.read_text().splitlines()]
        assert lines[0]["bulletinID"] == "early"
        assert lines[1]["bulletinID"] == "late"

    def test_returns_total_record_count(self, tmp_path: Path) -> None:
        """Return value is the total archive size after the merge."""
        path = tmp_path / "archive.ndjson"
        records = [
            _record(f"r{i}", f"2025-03-{14 + i:02d}T17:00:00Z") for i in range(5)
        ]

        count = write_ndjson_archive(records, path)

        assert count == 5

    def test_no_leftover_tmp_file(self, tmp_path: Path) -> None:
        """The .tmp sibling file is removed after a successful write."""
        path = tmp_path / "archive.ndjson"

        write_ndjson_archive([_record("a", "2025-03-14T17:00:00Z")], path)

        assert not path.with_suffix(path.suffix + ".tmp").exists()

    def test_overwrites_existing_cleanly(self, tmp_path: Path) -> None:
        """Re-writing replaces the file — no duplicate entries."""
        path = tmp_path / "archive.ndjson"
        write_ndjson_archive([_record("a", "2025-03-14T17:00:00Z")], path)
        write_ndjson_archive([_record("b", "2025-03-15T17:00:00Z")], path)

        lines = [json.loads(ln) for ln in path.read_text().splitlines()]
        assert {r["bulletinID"] for r in lines} == {"a", "b"}

    def test_source_label_does_not_affect_output(self, tmp_path: Path) -> None:
        """source_label is only used for logging; output is identical regardless."""
        path_with = tmp_path / "with_label.ndjson"
        path_without = tmp_path / "without_label.ndjson"
        records = [_record("a", "2025-03-14T17:00:00Z")]

        write_ndjson_archive(records, path_with, source_label="test")
        write_ndjson_archive(records, path_without)

        assert path_with.read_text() == path_without.read_text()

    def test_skips_blank_lines_in_existing(self, tmp_path: Path) -> None:
        """Blank lines in the existing archive are tolerated and skipped."""
        path = tmp_path / "archive.ndjson"
        record = _record("a", "2025-03-14T17:00:00Z")
        path.write_text(f"\n{json.dumps(record)}\n\n", encoding="utf-8")

        count = write_ndjson_archive([], path)

        assert count == 1

    def test_existing_records_without_bulletin_id_dropped(self, tmp_path: Path) -> None:
        """Records in the existing archive that lack bulletinID are excluded."""
        path = tmp_path / "archive.ndjson"
        invalid = {"validTime": {"startTime": "2025-03-14T17:00:00Z"}}
        valid = _record("a", "2025-03-14T17:00:00Z")
        content = json.dumps(invalid) + "\n" + json.dumps(valid) + "\n"
        path.write_text(content, encoding="utf-8")

        count = write_ndjson_archive([], path)

        assert count == 1
        lines = [json.loads(ln) for ln in path.read_text().splitlines()]
        assert lines[0]["bulletinID"] == "a"

    def test_one_record_per_line(self, tmp_path: Path) -> None:
        """Each record serialises onto its own line (NDJSON format)."""
        path = tmp_path / "archive.ndjson"
        records = [
            _record("a", "2025-03-14T17:00:00Z"),
            _record("b", "2025-03-15T17:00:00Z"),
        ]

        write_ndjson_archive(records, path)

        lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["bulletinID"] == "a"
        assert json.loads(lines[1])["bulletinID"] == "b"


# ---------------------------------------------------------------------------
# normalise_bulletin_response
# ---------------------------------------------------------------------------


class TestNormaliseBulletinResponse:
    """normalise_bulletin_response unwraps all known envelope shapes."""

    def test_flat_list_returned_as_is(self) -> None:
        """A top-level list of bulletin dicts is returned unchanged."""
        data = [{"bulletinID": "a"}, {"bulletinID": "b"}]
        result = normalise_bulletin_response(data, "TEST")
        assert result == data

    def test_empty_list_returned_as_is(self) -> None:
        """An empty list is returned without error."""
        assert normalise_bulletin_response([], "TEST") == []

    def test_dict_with_bulletins_key_unwrapped(self) -> None:
        """A dict with a 'bulletins' key returns the inner list."""
        inner = [{"bulletinID": "a"}]
        data = {"bulletins": inner}
        assert normalise_bulletin_response(data, "TEST") == inner

    def test_list_of_collection_objects_flattened(self) -> None:
        """A list of {'bulletins': [...]} objects is flattened into one list."""
        data = [
            {"bulletins": [{"bulletinID": "a"}]},
            {"bulletins": [{"bulletinID": "b"}, {"bulletinID": "c"}]},
        ]
        result = normalise_bulletin_response(data, "TEST")
        assert len(result) == 3
        assert [b["bulletinID"] for b in result] == ["a", "b", "c"]

    def test_none_returns_empty_with_warning(self) -> None:
        """None returns an empty list (unexpected shape warning is logged)."""
        result = normalise_bulletin_response(None, "TEST")
        assert result == []

    def test_string_returns_empty_with_warning(self) -> None:
        """A string body returns an empty list."""
        result = normalise_bulletin_response("unexpected", "TEST")
        assert result == []

    def test_dict_without_bulletins_key_returns_empty(self) -> None:
        """A dict without a 'bulletins' key returns an empty list."""
        result = normalise_bulletin_response({"other": "stuff"}, "TEST")
        assert result == []

    def test_source_label_appears_in_warning(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The source_label is included in the warning message for unexpected shapes.

        config/settings/base.py sets propagate=False on the bulletins logger so
        pytest's root caplog does not see records by default.  We flip propagate
        for the duration of this test so caplog can verify the warning.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("apps.bulletins"), "propagate", True)

        with caplog.at_level(
            logging.WARNING, logger="apps.bulletins.services.fetcher_common"
        ):
            normalise_bulletin_response({"no_bulletins": True}, "MY_SOURCE")

        assert any("MY_SOURCE" in rec.getMessage() for rec in caplog.records)

    def test_list_of_plain_bulletins_not_mistaken_for_collections(self) -> None:
        """A list whose first element has no 'bulletins' key is not treated as collections."""
        data = [{"bulletinID": "x"}, {"bulletinID": "y"}]
        result = normalise_bulletin_response(data, "TEST")
        assert result == data

    def test_collection_list_with_missing_bulletins_key(self) -> None:
        """A list-of-collections where some entries lack 'bulletins' yields empty lists."""
        data = [
            {"bulletins": [{"bulletinID": "a"}]},
            {"other": "thing"},  # no "bulletins" key — .get() returns []
        ]
        result = normalise_bulletin_response(data, "TEST")
        # The second entry's absence is tolerated; only "a" is returned.
        assert len(result) == 1
        assert result[0]["bulletinID"] == "a"
