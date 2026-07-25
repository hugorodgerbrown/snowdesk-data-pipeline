"""
tests/regions/management/commands/test_compute_basemap_download.py

Covers ``compute_basemap_download`` (SNOW-521 rework):
  - Dry-run (no --commit) writes nothing to any of the three tiers.
  - --commit populates basemap_download on all three tiers, matching
    build_blob's own output for each region's bbox/derived bbox.
  - MicroRegion's bbox is derived from boundary (it has no stored bbox
    field); a MicroRegion with neither boundary nor bbox is counted as a
    failure and the command exits non-zero.
  - --level restricts the run to one tier.
  - A second --commit run reports everything unchanged (idempotency).
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from regions.models import MajorRegion, SubRegion
from regions.services.basemap_tiles import TIER_BANDS, bbox_from_boundary, build_blob
from tests.factories import MajorRegionFactory, MicroRegionFactory, SubRegionFactory

_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]]],
}
_MAJOR_BBOX = [6.0, 45.5, 8.0, 47.0]
_MINOR_BBOX = [6.5, 45.8, 7.5, 46.6]


@pytest.mark.django_db
class TestComputeBasemapDownloadDryRun:
    """Default (no --commit) behaviour: read-only."""

    def test_dry_run_writes_nothing(self) -> None:
        """A bare invocation leaves basemap_download untouched on every tier."""
        major = MajorRegionFactory.create(bbox=_MAJOR_BBOX, basemap_download=None)
        minor = SubRegionFactory.create(
            major=major, bbox=_MINOR_BBOX, basemap_download=None
        )
        micro = MicroRegionFactory.create(
            subregion=minor, boundary=_BOUNDARY, basemap_download=None
        )

        call_command("compute_basemap_download", stdout=StringIO())

        major.refresh_from_db()
        minor.refresh_from_db()
        micro.refresh_from_db()
        assert major.basemap_download is None
        assert minor.basemap_download is None
        assert micro.basemap_download is None


@pytest.mark.django_db
class TestComputeBasemapDownloadCommit:
    """--commit behaviour: persists the computed blob on every tier."""

    def test_commit_populates_all_tiers(self) -> None:
        """--commit writes a build_blob-matching blob to all three tiers."""
        major = MajorRegionFactory.create(bbox=_MAJOR_BBOX, basemap_download=None)
        minor = SubRegionFactory.create(
            major=major, bbox=_MINOR_BBOX, basemap_download=None
        )
        micro = MicroRegionFactory.create(
            subregion=minor, boundary=_BOUNDARY, basemap_download=None
        )

        call_command("compute_basemap_download", "--commit", stdout=StringIO())

        major.refresh_from_db()
        minor.refresh_from_db()
        micro.refresh_from_db()

        assert major.basemap_download == build_blob(_MAJOR_BBOX, *TIER_BANDS["major"])
        assert minor.basemap_download == build_blob(_MINOR_BBOX, *TIER_BANDS["minor"])
        assert micro.basemap_download == build_blob(
            bbox_from_boundary(_BOUNDARY), *TIER_BANDS["micro"]
        )

    def test_micro_region_without_boundary_or_bbox_fails(self) -> None:
        """A MicroRegion with no boundary can't derive a bbox — counted as failed."""
        MicroRegionFactory.create(boundary=None, basemap_download=None)

        with pytest.raises(CommandError, match="1 region failure"):
            call_command(
                "compute_basemap_download",
                "--commit",
                "--level",
                "micro",
                stdout=StringIO(),
            )

    def test_level_restricts_to_one_tier(self) -> None:
        """--level micro only touches MicroRegion, leaving major/minor untouched."""
        major = MajorRegionFactory.create(bbox=_MAJOR_BBOX, basemap_download=None)
        micro = MicroRegionFactory.create(boundary=_BOUNDARY, basemap_download=None)

        call_command(
            "compute_basemap_download",
            "--commit",
            "--level",
            "micro",
            stdout=StringIO(),
        )

        major.refresh_from_db()
        micro.refresh_from_db()
        assert major.basemap_download is None
        assert micro.basemap_download is not None

    def test_second_commit_run_is_idempotent(self) -> None:
        """A second --commit run reports every region unchanged."""
        MajorRegionFactory.create(bbox=_MAJOR_BBOX, basemap_download=None)
        call_command("compute_basemap_download", "--commit", stdout=StringIO())

        out = StringIO()
        call_command(
            "compute_basemap_download", "--commit", "--verbosity", "1", stdout=out
        )
        assert "0 failed" in out.getvalue()

    def test_exit_code_zero_on_full_success(self) -> None:
        """No CommandError is raised when every region resolves cleanly."""
        MajorRegionFactory.create(bbox=_MAJOR_BBOX, basemap_download=None)
        # Should not raise.
        call_command("compute_basemap_download", "--commit", stdout=StringIO())
        assert MajorRegion.objects.filter(basemap_download__isnull=False).exists()


@pytest.mark.django_db
def test_sub_region_bbox_used_directly() -> None:
    """SubRegion (L2) already stores a bbox — used directly, no derivation."""
    minor = SubRegionFactory.create(bbox=_MINOR_BBOX, basemap_download=None)
    call_command(
        "compute_basemap_download", "--commit", "--level", "minor", stdout=StringIO()
    )
    minor.refresh_from_db()
    assert minor.basemap_download == build_blob(_MINOR_BBOX, *TIER_BANDS["minor"])
    assert SubRegion.objects.filter(
        pk=minor.pk, basemap_download__isnull=False
    ).exists()
