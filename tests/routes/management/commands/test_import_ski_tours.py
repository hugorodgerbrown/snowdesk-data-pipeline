"""
tests/routes/management/commands/test_import_ski_tours.py — import_ski_tours (SNOW-1276).

The command end to end against a small archive built in the test: a
preview that writes nothing, a commit that fills the service and this
instance, a second commit that changes nothing, the download path, and
the refusals and non-zero exits.
"""

from __future__ import annotations

from collections import Counter
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import CommandRun
from apps.ingest.public import SourceSkiSegment, SourceSkiTour, SwisstopoError
from apps.routes.models import SkiSegment, SkiTour
from tests.ingest.ski_archive import LINE_B, segment_row, tour_row, write_archive

pytestmark = pytest.mark.django_db

COMMAND = "apps.routes.management.commands.import_ski_tours"


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """Two segments and one SAC tour."""
    return write_archive(
        tmp_path / "skitouren.zip",
        [segment_row(1), segment_row(2, line=LINE_B)],
        [tour_row(5956)],
    )


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("import_ski_tours", *args, stdout=out)
    return out.getvalue()


def _counts() -> dict[str, int]:
    """The counts of the most recent recorded run."""
    run = CommandRun.objects.for_command("import_ski_tours").latest("started_at")
    counts: dict[str, int] = run.counts
    return counts


def test_a_preview_writes_nothing(archive: Path) -> None:
    """The counts are a commit's; the tables stay empty."""
    output = _run("--archive", str(archive))
    assert "[READ-ONLY]" in output
    counts = _counts()
    assert counts["service_ski-network_created"] == 2
    assert counts["ski-network_created"] == 2
    assert counts["ski-tours_created"] == 1
    assert not SourceSkiSegment.objects.exists()
    assert not SkiSegment.objects.exists()


def test_a_commit_fills_the_service_and_this_instance(archive: Path) -> None:
    """Both sides hold both layers; a second run writes nothing."""
    _run("--archive", str(archive), "--commit")
    assert SourceSkiSegment.objects.count() == SkiSegment.objects.count() == 2
    assert SourceSkiTour.objects.count() == SkiTour.objects.count() == 1
    assert _counts()["failed"] == 0
    _run("--archive", str(archive), "--commit")
    counts = _counts()
    assert counts["service_ski-network_unchanged"] == 2
    assert counts["service_ski-network_created"] == 0
    assert counts["ski-network_created"] == counts["ski-network_updated"] == 0


def test_with_no_archive_it_downloads_swisstopos(archive: Path) -> None:
    """The bare invocation reads the STAC item's GeoPackage."""

    def download(destination: Path) -> Path:
        """Copy the fixture where the command asked."""
        destination.write_bytes(archive.read_bytes())
        return destination

    with mock.patch(f"{COMMAND}.download_ski_archive", side_effect=download):
        _run("--commit")
    assert SkiTour.objects.count() == 1


def test_refusals(tmp_path: Path) -> None:
    """A missing file, a bad archive and an unreachable STAC all exit non-zero."""
    with pytest.raises(CommandError, match="is not a file"):
        _run("--archive", str(tmp_path / "missing.zip"))
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(CommandError, match="not a zip"):
        _run("--archive", str(bad))
    with mock.patch(
        f"{COMMAND}.download_ski_archive", side_effect=SwisstopoError("down")
    ):
        with pytest.raises(CommandError, match="down"):
            _run()


def test_a_failed_apply_exits_non_zero(archive: Path) -> None:
    """An item this instance cannot apply is counted and fails the run."""
    with mock.patch(
        f"{COMMAND}.apply_from_service",
        return_value={"ski-network": Counter(failed=1), "ski-tours": Counter()},
    ):
        with pytest.raises(CommandError, match="1 item"):
            _run("--archive", str(archive), "--commit")
