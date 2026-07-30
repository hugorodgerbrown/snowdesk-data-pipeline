# tests/bulletins/test_meteofrance_archive_integrity.py — Invariants for the
# committed Météo-France archive.
#
# apps/bulletins/local_mirrors/meteofrance_archive.ndjson is a generated
# artefact, 4,671 records and ~25 MB, so it is not reviewed by its diff. These
# tests are the review: they assert the properties a rebuild must preserve, so a
# regression in the offline extractor is caught here rather than by noticing
# months later that bulletins look thin or are dated in the future (SNOW-559).
#
# Rebuild procedure: docs/runbooks/rebuild-meteofrance-archive.md
"""Invariants for the committed Météo-France archive NDJSON."""

from __future__ import annotations

import datetime
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from apps.bulletins.services.meteofrance_identity import compact_publication_stamp

ARCHIVE = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "bulletins"
    / "local_mirrors"
    / "meteofrance_archive.ndjson"
)

# The archive is a closed backfill of the 2025-26 season.
FIRST_COVERED = datetime.date(2025, 11, 3)
LAST_COVERED = datetime.date(2026, 5, 22)

# A BRA covers the day it was written or the day after.
MAX_COVER_LAG_DAYS = 2


@pytest.fixture(scope="module")
def records() -> list[dict[str, Any]]:
    """Return every record's ``properties`` dict, parsed once per module.

    Returns:
        The parsed ``properties`` sub-dict of each archive line.

    """
    # The archive is git-tracked, so absence is a real failure — not a reason to
    # skip. Skipping here would turn every assertion below into a silent no-op,
    # which is exactly the class of hole these tests exist to close.
    assert ARCHIVE.exists(), f"Archive not present: {ARCHIVE}"
    out: list[dict[str, Any]] = []
    with ARCHIVE.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line)["properties"])
    return out


class TestIdentity:
    """Every record must be individually identifiable."""

    def test_every_record_has_a_publication_time(
        self, records: list[dict[str, Any]]
    ) -> None:
        """A record without one cannot be told apart from the day's other issue.

        Args:
            records: Parsed archive records.

        """
        missing = [
            r["customData"]["MF"].get("source_file")
            for r in records
            if not r.get("publicationTime")
        ]

        assert not missing, (
            f"{len(missing)} record(s) lack publicationTime: {missing[:5]}"
        )

    def test_publication_times_are_parseable(
        self, records: list[dict[str, Any]]
    ) -> None:
        """Every timestamp must yield a compact stamp for the id.

        Args:
            records: Parsed archive records.

        """
        bad = [
            r["customData"]["MF"].get("source_file")
            for r in records
            if compact_publication_stamp(r.get("publicationTime")) is None
        ]

        assert not bad, f"{len(bad)} unparseable publicationTime: {bad[:5]}"

    def test_no_two_distinct_bulletins_share_an_identity(
        self, records: list[dict[str, Any]]
    ) -> None:
        """Records that coalesce onto one id must forecast the same thing.

        32 pairs in the archive are one bulletin downloaded twice, and they
        correctly coalesce. Two records with *different* forecast content on one
        id is the failure this identity scheme exists to prevent.

        ``source_file`` is excluded from the comparison for the same reason the
        loader excludes it: it is the only field that differs between those 32
        pairs, being the name of the file each was read from, and it says nothing
        about the forecast.

        Args:
            records: Parsed archive records.

        """
        by_id: dict[str, set[str]] = {}
        for record in records:
            mf = record["customData"]["MF"]
            key = (
                f"{mf['massif']}-{mf['date']}-"
                f"{compact_publication_stamp(record.get('publicationTime'))}"
            )
            comparable = {k: v for k, v in record.items() if k != "bulletinID"}
            comparable["customData"] = {
                **record["customData"],
                "MF": {k: v for k, v in mf.items() if k != "source_file"},
            }
            by_id.setdefault(key, set()).add(
                json.dumps(comparable, sort_keys=True, ensure_ascii=False)
            )

        clashes = {key: len(v) for key, v in by_id.items() if len(v) > 1}

        assert not clashes, f"ids covering >1 distinct payload: {list(clashes)[:5]}"


class TestDates:
    """Covered dates must be plausible relative to publication."""

    def test_no_record_is_dated_beyond_the_archive_window(
        self, records: list[dict[str, Any]]
    ) -> None:
        """No bulletin may claim to cover a day outside the backfilled season.

        The original extractor guessed the year from ``date.today()`` and dated
        267 records a year ahead, which read as forecasts for a season that had
        not happened.

        Args:
            records: Parsed archive records.

        """
        out_of_range = [
            (r["customData"]["MF"]["source_file"], r["customData"]["MF"]["date"])
            for r in records
            if not (
                FIRST_COVERED
                <= datetime.date.fromisoformat(r["customData"]["MF"]["date"])
                <= LAST_COVERED
            )
        ]

        assert not out_of_range, (
            f"{len(out_of_range)} record(s) dated outside "
            f"{FIRST_COVERED}..{LAST_COVERED}: {out_of_range[:5]}"
        )

    def test_covered_date_follows_publication(
        self, records: list[dict[str, Any]]
    ) -> None:
        """A bulletin covers the day it was published or the day after.

        Args:
            records: Parsed archive records.

        """
        implausible = []
        for record in records:
            mf = record["customData"]["MF"]
            covered = datetime.date.fromisoformat(mf["date"])
            published = datetime.datetime.fromisoformat(
                str(record["publicationTime"]).replace("Z", "+00:00")
            ).date()
            lag = (covered - published).days
            if not 0 <= lag <= MAX_COVER_LAG_DAYS:
                implausible.append((mf["source_file"], mf["date"], lag))

        assert not implausible, (
            f"{len(implausible)} record(s) with an implausible publication lag: "
            f"{implausible[:5]}"
        )

    def test_valid_from_is_not_synthetic_midnight(
        self, records: list[dict[str, Any]]
    ) -> None:
        """``validTime.startTime`` must be the real issue time.

        A midnight start means every issue of a day sorts equally, which left
        ``recompute_region_day`` unable to order them.

        Args:
            records: Parsed archive records.

        """
        midnights = sum(
            1 for r in records if str(r["validTime"]["startTime"])[11:16] == "00:00"
        )

        # A genuine 00:00 issue is possible but should be vanishingly rare.
        assert midnights < len(records) * 0.01, (
            f"{midnights} of {len(records)} records start at midnight — "
            "validTime.startTime looks synthetic again"
        )


class TestProse:
    """The stability narrative must not be clipped."""

    def test_prose_is_not_column_clipped(self, records: list[dict[str, Any]]) -> None:
        """Most records must carry a line longer than the old ~80-char clip.

        The old `crop_left` bound cut every line at roughly 80 characters, so an
        archive where no record exceeds it has regressed. Soft wrapping means a
        line ending without punctuation is normal, which is why this measures
        width rather than looking for mid-word endings.

        Args:
            records: Parsed archive records.

        """
        wide = 0
        for record in records:
            comment = str((record.get("snowpackStructure") or {}).get("comment", ""))
            # Tags stand in for line breaks in the stored HTML.
            longest = max(
                (len(part) for part in comment.replace("<", "\n<").split("\n")),
                default=0,
            )
            if longest > 90:
                wide += 1

        assert wide > len(records) * 0.5, (
            f"only {wide} of {len(records)} records have a prose line over 90 "
            "chars — the column crop has regressed"
        )

    def test_most_records_have_an_activity_comment(
        self, records: list[dict[str, Any]]
    ) -> None:
        """The activity split must recognise both heading wordings.

        Handling only "Déclenchements provoqués" left 143 records (3.1%) with an
        empty comment.

        Args:
            records: Parsed archive records.

        """
        empty = sum(
            1
            for r in records
            if not str((r.get("avalancheActivity") or {}).get("comment", "")).strip()
        )

        assert empty < len(records) * 0.01, (
            f"{empty} of {len(records)} records have no avalancheActivity comment"
        )


class TestCoverage:
    """Sanity checks on the archive as a whole."""

    def test_source_files_are_unique(self, records: list[dict[str, Any]]) -> None:
        """One record per source PDF.

        Args:
            records: Parsed archive records.

        """
        counts = Counter(r["customData"]["MF"]["source_file"] for r in records)
        repeated = {name: n for name, n in counts.items() if n > 1}

        assert not repeated, f"source_file appears more than once: {list(repeated)[:5]}"

    def test_every_record_names_a_massif_and_date(
        self, records: list[dict[str, Any]]
    ) -> None:
        """The two fields the id is built from must always be present.

        Args:
            records: Parsed archive records.

        """
        incomplete = [
            r["customData"]["MF"].get("source_file")
            for r in records
            if not r["customData"]["MF"].get("massif")
            or not r["customData"]["MF"].get("date")
        ]

        assert not incomplete, f"{len(incomplete)} record(s) incomplete"
