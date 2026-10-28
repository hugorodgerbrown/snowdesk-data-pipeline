"""
tests/ingest/test_ski.py — The service's copy of swisstopo's ski-touring map (SNOW-1276).

``import_ski_archive`` against a small archive built in the test: both
layers stored, the unnamed lines skipped, a second import writing nothing,
a dropped line tombstoned and a changed one rewritten, and the refusals.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from apps.ingest.models import SourceSkiSegment, SourceSkiTour
from apps.ingest.normalise.swisstopo_ski import segment_uuid
from apps.ingest.ski import SKI_NETWORK, SKI_TOURS, SkiArchiveError, import_ski_archive
from tests.ingest.ski_archive import LINE_B, segment_row, tour_row, write_archive

pytestmark = pytest.mark.django_db

OTHER_TOUR = "{0b4c5e7a-2d1f-4f3a-9a77-1f2e3d4c5b6a}"


def _archive(tmp_path: Path, name: str = "a.zip", **kwargs: object) -> Path:
    """Write the default archive: two segments, one tour and one unnamed line."""
    segments = kwargs.get("segments") or [segment_row(1), segment_row(2, line=LINE_B)]
    tours = kwargs.get("tours") or [
        tour_row(5956),
        tour_row(None, route_uuid=OTHER_TOUR),
    ]
    return write_archive(tmp_path / name, segments, tours)  # type: ignore[arg-type]


def test_both_layers_are_stored_and_the_unnamed_lines_skipped(tmp_path: Path) -> None:
    """Two segments under their uuid5s, one tour under swisstopo's uuid."""
    result = import_ski_archive(_archive(tmp_path))
    assert result[SKI_NETWORK]["created"] == 2
    assert result[SKI_TOURS]["created"] == 1
    segment = SourceSkiSegment.objects.get(uuid=segment_uuid(1))
    assert segment.slug == "segment-1"
    assert segment.document["geometry"]["type"] == "MultiLineString"
    tour = SourceSkiTour.objects.get()
    assert str(tour.uuid) == "7e61c1c7-1ec5-41e7-996b-b28db78be4f0"
    assert tour.slug == "piz-buin-von-der-silvrettahutte-5956"


def test_a_second_import_of_the_same_release_writes_nothing(tmp_path: Path) -> None:
    """Every row unchanged, so the feeds' "since" stays quiet."""
    archive = _archive(tmp_path)
    import_ski_archive(archive)
    result = import_ski_archive(archive)
    assert result[SKI_NETWORK] == {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "unchanged": 2,
    }
    assert result[SKI_TOURS]["unchanged"] == 1


def test_a_dropped_line_is_tombstoned_and_a_moved_one_rewritten(tmp_path: Path) -> None:
    """A new release without segment 2 and with segment 1 redrawn."""
    import_ski_archive(_archive(tmp_path))
    result = import_ski_archive(
        _archive(tmp_path, "b.zip", segments=[segment_row(1, line=LINE_B)])
    )
    assert result[SKI_NETWORK]["updated"] == 1
    assert result[SKI_NETWORK]["deleted"] == 1
    assert SourceSkiSegment.objects.get(uuid=segment_uuid(2)).deleted_at is not None


def test_an_unreadable_segment_is_skipped_not_fatal(tmp_path: Path) -> None:
    """One broken line costs only itself."""
    archive = _archive(
        tmp_path, segments=[segment_row(1), segment_row(2, geom=b"junk-bytes")]
    )
    result = import_ski_archive(archive)
    assert result[SKI_NETWORK]["created"] == 1


def test_an_unreadable_tour_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A tour whose uuid cannot be read costs only itself."""
    archive = _archive(tmp_path, tours=[tour_row(1, route_uuid="not-a-uuid")])
    assert import_ski_archive(archive)[SKI_TOURS]["created"] == 0


def test_an_archive_that_is_not_swisstopos_is_refused(tmp_path: Path) -> None:
    """Not a zip, or a zip without both GeoPackages."""
    not_zip = tmp_path / "x.zip"
    not_zip.write_bytes(b"not a zip")
    with pytest.raises(SkiArchiveError, match="not a zip"):
        import_ski_archive(not_zip)
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "")
    with pytest.raises(SkiArchiveError, match="ski_network_2056"):
        import_ski_archive(empty)
