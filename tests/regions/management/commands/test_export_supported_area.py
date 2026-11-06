"""
tests/regions/management/commands/test_export_supported_area.py

Covers ``export_supported_area`` (SNOW-1444):
  - The file written to --output is a FeatureCollection whose geometry
    covers every region with a boundary, at z12 plus a one-tile ring.
  - A region with no boundary is skipped and counted.
  - stdout carries progress and the summary only, never GeoJSON.
  - A run records one CommandRun and changes no MicroRegion.
  - With no boundary at all it exits non-zero and writes no file.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from shapely.geometry import shape

from apps.core.models import CommandRun
from apps.regions.models import MicroRegion
from tests.factories import MicroRegionFactory

_ZERMATT = {
    "type": "Polygon",
    "coordinates": [
        [[7.70, 45.98], [7.80, 45.98], [7.80, 46.05], [7.70, 46.05], [7.70, 45.98]]
    ],
}
_DAVOS = {
    "type": "Polygon",
    "coordinates": [
        [[9.78, 46.77], [9.88, 46.77], [9.88, 46.83], [9.78, 46.83], [9.78, 46.77]]
    ],
}


def _run(path: Path) -> str:
    """Run the command writing to ``path``, returning its stdout."""
    out = StringIO()
    call_command("export_supported_area", output=str(path), stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_the_file_covers_every_region_with_a_boundary(tmp_path: Path) -> None:
    """Both regions sit inside the written area, with margin to spare."""
    MicroRegionFactory.create(boundary=_ZERMATT)
    MicroRegionFactory.create(boundary=_DAVOS)
    path = tmp_path / "area.geojson"

    _run(path)

    collection = json.loads(path.read_text())
    assert collection["type"] == "FeatureCollection"
    (feature,) = collection["features"]
    area = shape(feature["geometry"])
    # Zermatt and Davos are far apart, so the area is two clusters.
    assert feature["geometry"]["type"] == "MultiPolygon"
    for boundary in (_ZERMATT, _DAVOS):
        region = shape(boundary)
        assert area.contains(region)
        # The ring: about 0.088 degrees of longitude is one z12 tile, so a
        # point half a tile outside the region is still inside the area.
        west, south, _, _ = region.bounds
        assert area.contains(
            shape({"type": "Point", "coordinates": [west - 0.04, south]})
        )
    assert feature["properties"]["regions"] == 2
    assert feature["properties"]["zoom"] == 12


@pytest.mark.django_db
def test_a_region_without_a_boundary_is_skipped_and_counted(tmp_path: Path) -> None:
    """The summary names the skipped region, and the register counts it."""
    MicroRegionFactory.create(boundary=_ZERMATT)
    MicroRegionFactory.create(boundary=None)

    out = _run(tmp_path / "area.geojson")

    assert "1 skipped with no boundary" in out
    run = CommandRun.objects.get(command="export_supported_area")
    assert run.counts["regions_read"] == 1
    assert run.counts["regions_skipped"] == 1
    assert run.counts["tiles"] > 0


@pytest.mark.django_db
def test_stdout_is_progress_and_summary_only(tmp_path: Path) -> None:
    """One progress line per region and the summary; no GeoJSON on stdout."""
    region = MicroRegionFactory.create(boundary=_ZERMATT)

    out = _run(tmp_path / "area.geojson")

    lines = out.strip().splitlines()
    assert lines[0] == f"{region.pk} {region.region_id}"
    assert lines[-1].startswith("Wrote ")
    assert "FeatureCollection" not in out
    assert "coordinates" not in out


@pytest.mark.django_db
def test_a_run_writes_only_its_own_command_run(tmp_path: Path) -> None:
    """The register row is the one database write; no region changes."""
    region = MicroRegionFactory.create(boundary=_ZERMATT)
    before = MicroRegion.objects.values().get(pk=region.pk)

    _run(tmp_path / "area.geojson")

    assert CommandRun.objects.filter(command="export_supported_area").count() == 1
    assert MicroRegion.objects.values().get(pk=region.pk) == before


@pytest.mark.django_db
def test_no_boundaries_exits_non_zero_and_writes_nothing(tmp_path: Path) -> None:
    """With nothing to cover there is no area, so no file and an error."""
    MicroRegionFactory.create(boundary=None)
    path = tmp_path / "area.geojson"

    with pytest.raises(CommandError, match="no supported area"):
        _run(path)

    assert not path.exists()
