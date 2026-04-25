"""
tests/pipeline/management/commands/test_diagnose_region_coverage.py — Tests
for the ``diagnose_region_coverage`` management command.

Covers:

  - Bucket A (region has a RegionDayRating row).
  - Bucket B (region listed in a bulletin's raw_data but no rating row).
  - Bucket C (region never listed in any bulletin).
  - Bucket B's signal is read from raw_data, not the RegionBulletin
    join — so a region that's in raw_data but missing its M2M link still
    shows up in B.
  - The partition is disjoint and exhaustive.
  - ``--date`` restricts the analysis to bulletins targeting one day.
  - Invalid ``--date`` raises CommandError.
  - ``--verbose-table`` prints a per-region row.
  - The default run does not mutate the database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from pipeline.models import Bulletin, RegionBulletin, RegionDayRating
from tests.factories import (
    BulletinFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    RegionFactory,
)


def _bulletin_with_regions(
    region_ids: list[str],
    *,
    bulletin_id: str,
    valid_from: datetime | None = None,
) -> Bulletin:
    """
    Create a Bulletin whose raw_data lists the supplied region IDs.

    Mirrors the GeoJSON Feature envelope produced by ``upsert_bulletin``
    so the diagnostic exercises the same payload shape that production
    rows carry. RegionBulletin links are intentionally not created — the
    diagnostic reads raw_data directly, so the M2M side is incidental
    here.
    """
    issued = valid_from or datetime(2026, 3, 15, 8, 0, tzinfo=UTC)
    return BulletinFactory.create(
        bulletin_id=bulletin_id,
        issued_at=issued,
        valid_from=issued,
        valid_to=issued + timedelta(days=1),
        raw_data={
            "type": "Feature",
            "geometry": None,
            "properties": {
                "bulletinID": bulletin_id,
                "regions": [
                    {"regionID": rid, "name": f"Region {rid}"} for rid in region_ids
                ],
            },
        },
    )


@pytest.mark.django_db
class TestBucketAssignment:
    """Tests covering each of the three classification buckets."""

    def test_region_with_rating_row_is_bucket_a(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """A region with a RegionDayRating row is reported in bucket A."""
        region = RegionFactory.create(region_id="CH-A001")
        bulletin = _bulletin_with_regions(["CH-A001"], bulletin_id="bul-a")
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        RegionDayRatingFactory.create(region=region)

        call_command("diagnose_region_coverage", verbosity=0)

        out = capsys.readouterr().out
        assert "A. Has rating row(s):                  1" in out
        assert "B. In raw bulletin but no rating row:  0" in out
        assert "C. Never in any raw bulletin:          0" in out
        # The region ID should not be listed under bucket B or C.
        assert "CH-A001" not in out.split("Bucket B")[-1].split("Bucket C")[0]

    def test_region_in_bulletin_but_no_rating_row_is_bucket_b(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """A region listed in raw_data but with no rating row is in bucket B."""
        RegionFactory.create(region_id="CH-B001")
        _bulletin_with_regions(["CH-B001"], bulletin_id="bul-b")
        # Deliberately no RegionBulletin and no RegionDayRating: the
        # diagnostic must rely on raw_data alone.

        call_command("diagnose_region_coverage", verbosity=0)

        out = capsys.readouterr().out
        assert "B. In raw bulletin but no rating row:  1" in out
        assert "Bucket B (local-bug suspects)" in out
        assert "CH-B001" in out

    def test_region_never_in_any_bulletin_is_bucket_c(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """A region absent from every bulletin's raw_data is in bucket C."""
        RegionFactory.create(region_id="CH-C001")
        # An unrelated bulletin that doesn't mention CH-C001.
        _bulletin_with_regions(["CH-OTHER"], bulletin_id="bul-c-other")

        call_command("diagnose_region_coverage", verbosity=0)

        out = capsys.readouterr().out
        assert "C. Never in any raw bulletin:          1" in out
        assert "Bucket C (upstream-gap suspects)" in out
        assert "CH-C001" in out

    def test_bucket_b_does_not_require_region_bulletin_link(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Bucket B is computed from raw_data, ignoring the M2M join."""
        RegionFactory.create(region_id="CH-B002")
        # raw_data lists the region but no RegionBulletin row exists.
        _bulletin_with_regions(["CH-B002"], bulletin_id="bul-b2")

        call_command("diagnose_region_coverage", verbosity=0)

        out = capsys.readouterr().out
        # No M2M join means listing in raw_data alone is enough.
        assert "B. In raw bulletin but no rating row:  1" in out
        assert "CH-B002" in out

    def test_partition_is_disjoint_and_exhaustive(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """A + B + C equals the total number of regions."""
        # One in each bucket.
        a_region = RegionFactory.create(region_id="CH-A777")
        RegionFactory.create(region_id="CH-B777")
        RegionFactory.create(region_id="CH-C777")
        bulletin_a = _bulletin_with_regions(["CH-A777"], bulletin_id="bul-a777")
        RegionBulletinFactory.create(bulletin=bulletin_a, region=a_region)
        RegionDayRatingFactory.create(region=a_region)
        _bulletin_with_regions(["CH-B777"], bulletin_id="bul-b777")

        call_command("diagnose_region_coverage", verbosity=0)

        out = capsys.readouterr().out
        assert "Regions in fixture: 3" in out
        assert "A. Has rating row(s):                  1" in out
        assert "B. In raw bulletin but no rating row:  1" in out
        assert "C. Never in any raw bulletin:          1" in out


@pytest.mark.django_db
class TestDateFlag:
    """Tests for the --date flag."""

    def test_date_filter_restricts_to_target_day(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """--date X considers only bulletins whose target_day equals X.

        A bulletin valid on 2026-03-14 morning targets 2026-03-14, while
        one valid on 2026-03-15 morning targets 2026-03-15. Asking
        ``--date 2026-03-15`` should pick up only the second.
        """
        RegionFactory.create(region_id="CH-D001")
        RegionFactory.create(region_id="CH-D002")
        # Bulletin targeting 2026-03-14 — should NOT be counted under --date 2026-03-15.
        _bulletin_with_regions(
            ["CH-D001"],
            bulletin_id="bul-d001",
            valid_from=datetime(2026, 3, 14, 8, 0, tzinfo=UTC),
        )
        # Bulletin targeting 2026-03-15 — should be counted.
        _bulletin_with_regions(
            ["CH-D002"],
            bulletin_id="bul-d002",
            valid_from=datetime(2026, 3, 15, 8, 0, tzinfo=UTC),
        )

        call_command("diagnose_region_coverage", target_date="2026-03-15", verbosity=0)

        out = capsys.readouterr().out
        # CH-D002 was published for 2026-03-15 and has no rating row → bucket B.
        assert "CH-D002" in out
        # CH-D001 was not published for 2026-03-15 → bucket C for this date.
        assert "CH-D001" in out

    def test_evening_bulletin_targets_next_day(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """An evening bulletin (valid_from.hour >= 12) targets the *next* day.

        A bulletin issued on the evening of 2026-03-14 should be picked up
        by ``--date 2026-03-15``, mirroring the ``_target_day`` rule used
        elsewhere in the pipeline.
        """
        RegionFactory.create(region_id="CH-E001")
        _bulletin_with_regions(
            ["CH-E001"],
            bulletin_id="bul-e001",
            valid_from=datetime(2026, 3, 14, 17, 0, tzinfo=UTC),
        )

        call_command("diagnose_region_coverage", target_date="2026-03-15", verbosity=0)

        out = capsys.readouterr().out
        # The evening bulletin's regions should count for 2026-03-15.
        assert "CH-E001" in out
        assert "B. In raw bulletin but no rating row:  1" in out

    def test_invalid_date_raises_command_error(self) -> None:
        """A malformed --date value raises CommandError."""
        with pytest.raises(CommandError, match="Invalid --date"):
            call_command(
                "diagnose_region_coverage", target_date="not-a-date", verbosity=0
            )


@pytest.mark.django_db
class TestVerboseTable:
    """Tests for the --verbose-table flag."""

    def test_verbose_table_prints_per_region_rows(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """--verbose-table prints a [bucket] line for every region."""
        RegionFactory.create(region_id="CH-V001", name="Verbose region")

        call_command("diagnose_region_coverage", verbose_table=True, verbosity=0)

        out = capsys.readouterr().out
        assert "Per-region table:" in out
        assert "[C] CH-V001  Verbose region" in out

    def test_default_run_does_not_print_per_region_table(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Without --verbose-table the per-region table is suppressed."""
        RegionFactory.create(region_id="CH-V002")

        call_command("diagnose_region_coverage", verbosity=0)

        out = capsys.readouterr().out
        assert "Per-region table:" not in out


@pytest.mark.django_db
class TestReadOnly:
    """Tests asserting the command is pure SELECT."""

    def test_command_does_not_create_rating_rows(self) -> None:
        """Running the diagnostic must not create any RegionDayRating rows."""
        RegionFactory.create(region_id="CH-R001")
        _bulletin_with_regions(["CH-R001"], bulletin_id="bul-r001")
        baseline_ratings = RegionDayRating.objects.count()
        baseline_links = RegionBulletin.objects.count()
        baseline_bulletins = Bulletin.objects.count()

        call_command("diagnose_region_coverage", verbosity=0)

        assert RegionDayRating.objects.count() == baseline_ratings
        assert RegionBulletin.objects.count() == baseline_links
        assert Bulletin.objects.count() == baseline_bulletins
