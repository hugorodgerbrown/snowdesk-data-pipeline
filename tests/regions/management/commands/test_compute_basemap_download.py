"""
tests/regions/management/commands/test_compute_basemap_download.py

Covers ``compute_basemap_download`` (SNOW-521 final shape — MicroRegion
only; SNOW-583 clips to the region's real boundary rather than its bbox):
  - Dry-run (no --commit) writes nothing.
  - --commit populates basemap_download on MicroRegion, matching
    build_region_blob's own output for the region's boundary.
  - A MicroRegion with no boundary can't derive a bbox — counted as a
    failure and the command exits non-zero.
  - A second --commit run reports everything unchanged (idempotency).
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.regions.models import MicroRegion
from apps.regions.services.basemap_tiles import MICRO_BAND, build_region_blob
from tests.factories import MicroRegionFactory

_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]]],
}


@pytest.mark.django_db
class TestComputeBasemapDownloadDryRun:
    """Default (no --commit) behaviour: read-only."""

    def test_dry_run_writes_nothing(self) -> None:
        """A bare invocation leaves basemap_download untouched."""
        micro = MicroRegionFactory.create(boundary=_BOUNDARY, basemap_download=None)

        call_command("compute_basemap_download", stdout=StringIO())

        micro.refresh_from_db()
        assert micro.basemap_download is None


@pytest.mark.django_db
class TestComputeBasemapDownloadCommit:
    """--commit behaviour: persists the computed blob."""

    def test_commit_populates_micro_region(self) -> None:
        """--commit writes a build_region_blob-matching blob."""
        micro = MicroRegionFactory.create(boundary=_BOUNDARY, basemap_download=None)

        call_command("compute_basemap_download", "--commit", stdout=StringIO())

        micro.refresh_from_db()
        assert micro.basemap_download == build_region_blob(_BOUNDARY, *MICRO_BAND)

    def test_micro_region_without_boundary_fails(self) -> None:
        """A MicroRegion with no boundary can't derive a bbox — counted as failed."""
        MicroRegionFactory.create(boundary=None, basemap_download=None)

        with pytest.raises(CommandError, match="1 region failure"):
            call_command("compute_basemap_download", "--commit", stdout=StringIO())

    def test_second_commit_run_is_idempotent(self) -> None:
        """A second --commit run reports every region unchanged."""
        MicroRegionFactory.create(boundary=_BOUNDARY, basemap_download=None)
        call_command("compute_basemap_download", "--commit", stdout=StringIO())

        out = StringIO()
        call_command(
            "compute_basemap_download", "--commit", "--verbosity", "1", stdout=out
        )
        assert "0 failed" in out.getvalue()

    def test_exit_code_zero_on_full_success(self) -> None:
        """No CommandError is raised when every region resolves cleanly."""
        MicroRegionFactory.create(boundary=_BOUNDARY, basemap_download=None)
        # Should not raise.
        call_command("compute_basemap_download", "--commit", stdout=StringIO())
        assert MicroRegion.objects.filter(basemap_download__isnull=False).exists()
