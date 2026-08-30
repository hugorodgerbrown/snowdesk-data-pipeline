"""
tests/regions/management/commands/test_link_region_centroid_locations.py

Covers ``link_region_centroid_locations`` (SNOW-696):
  - Mints a centroid Location with its elevation resolved.
  - The location is anonymous — a centroid is not a place anyone goes.
  - Regions with no usable ``boundary`` are skipped, not failed.
  - A dry run writes nothing (SNOW-719).
  - Idempotent; one failing region does not abort the batch.

SNOW-762 removed this command's forecast-cell half with the weather app;
what is left is the Location and its elevation.

SNOW-765 repointed the centroid derivation from the ``centre`` column onto
``boundary``, so a region needs a boundary — not a centre — to be a
candidate. ``TestCentroidDerivationIsLossless`` is the guard on that
repoint being value-for-value identical to what ``centre`` holds.

Every Open-Meteo call is patched out.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.locations.models import Location
from apps.regions.fixture_utils import centre_from_bbox
from apps.regions.models import MicroRegion
from tests.factories import MicroRegionFactory

COMMAND = "link_region_centroid_locations"
_BASE = "apps.regions.management.commands.link_region_centroid_locations"
_ELEVATION = f"{_BASE}.fetch_elevation"

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES_DIR = REPO_ROOT / "apps" / "regions" / "fixtures"
_EAWS_FIXTURE_NAMES = ["eaws_CH.json", "eaws_FR.json", "eaws_AT.json", "eaws_IT.json"]


def _square(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    """Return a GeoJSON Polygon for the rectangle (x0,y0)→(x1,y1)."""
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


# Bbox midpoint (lat 46.1, lon 7.4) — the coordinates every assertion below
# expects the command to resolve to.
BOUNDARY = _square(7.3, 46.0, 7.5, 46.2)

# Bbox midpoint (lat 47.9, lon 8.9).
OTHER_BOUNDARY = _square(8.8, 47.8, 9.0, 48.0)


@pytest.mark.django_db
class TestLinkRegionCentroidLocations:
    """--commit anchors each region to a centroid Location."""

    def test_mints_a_resolved_centroid_location(self) -> None:
        """The region ends up anchored to a location with a height."""
        region = MicroRegionFactory.create(boundary=BOUNDARY)

        with patch(_ELEVATION, return_value=2100.0):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        region.refresh_from_db()
        assert region.centroid_location is not None
        assert region.centroid_location.latitude == 46.1
        assert region.centroid_location.longitude == 7.4
        assert region.centroid_location.elevation_m == 2100.0

    def test_the_centroid_location_is_anonymous(self) -> None:
        """No name, no kind — a centroid represents the region, not a place.

        Naming it would put it in the curated estate ``import_locations``
        owns, where a curator would then be asked to maintain a point
        nobody goes to.
        """
        MicroRegionFactory.create(boundary=BOUNDARY)

        with patch(_ELEVATION, return_value=2100.0):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        location = Location.objects.get()
        assert location.name == ""
        assert location.kind == ""

    def test_a_region_with_no_boundary_is_not_a_candidate(self) -> None:
        """A null ``boundary`` is excluded by the queryset."""
        region = MicroRegionFactory.create(boundary=None)

        with patch(_ELEVATION) as lookup:
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        lookup.assert_not_called()
        region.refresh_from_db()
        assert region.centroid_location is None

    def test_a_boundary_with_no_centre_still_resolves(self) -> None:
        """The point of SNOW-765 — the ``centre`` column is not consulted.

        An environment whose fixture rows predate ``centre``, or whose
        column has been dropped, still gets its centroid. On the pre-SNOW-765
        code this region was not a candidate at all.
        """
        region = MicroRegionFactory.create(boundary=BOUNDARY, centre=None)

        with patch(_ELEVATION, return_value=2100.0) as lookup:
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        lookup.assert_called_once_with(46.1, 7.4)
        region.refresh_from_db()
        assert region.centroid_location is not None

    def test_an_unreadable_boundary_is_skipped_not_failed(self) -> None:
        """A malformed ``boundary`` is a fixture problem, not a run failure.

        ``boundary`` is a JSONField, so its shape is not schema-guaranteed,
        and ``centre_from_bbox`` raises rather than returning None on a
        geometry it cannot read. Exiting non-zero would make one bad fixture
        row fail every scheduled run.
        """
        MicroRegionFactory.create(
            boundary={"type": "Point", "coordinates": [7.4, 46.1]}
        )

        out = StringIO()
        with patch(_ELEVATION, return_value=2100.0):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=out)

        assert "1 skipped" in out.getvalue()
        assert not Location.objects.exists()

    def test_a_boundary_with_no_coordinates_is_skipped_not_failed(self) -> None:
        """An empty coordinate list degrades the same way.

        Distinct from the unsupported-type case above: this one reaches
        ``min()`` on an empty sequence rather than the type guard.
        """
        MicroRegionFactory.create(boundary={"type": "Polygon", "coordinates": []})

        out = StringIO()
        with patch(_ELEVATION, return_value=2100.0):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=out)

        assert "1 skipped" in out.getvalue()
        assert not Location.objects.exists()

    def test_dry_run_writes_nothing(self) -> None:
        """SNOW-719: the preview reports without persisting anything.

        The elevation call still happens on this path — it is what proves
        the region can resolve, and what the report counts — but neither
        the Location nor the FK is written.
        """
        region = MicroRegionFactory.create(boundary=BOUNDARY)

        out = StringIO()
        with patch(_ELEVATION, return_value=2100.0) as lookup:
            call_command(COMMAND, "--delay", "0", stdout=out)

        lookup.assert_called_once_with(46.1, 7.4)
        assert not Location.objects.exists()
        region.refresh_from_db()
        assert region.centroid_location is None
        assert "1 region(s) would be linked" in out.getvalue()
        assert "No data written" in out.getvalue()

    def test_second_run_selects_nothing(self) -> None:
        """Idempotent — a linked region is out of the candidate set."""
        MicroRegionFactory.create(boundary=BOUNDARY)

        with patch(_ELEVATION, return_value=2100.0):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())
            with patch(_ELEVATION) as second:
                call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        second.assert_not_called()

    def test_one_failure_does_not_stop_the_batch(self) -> None:
        """A failing region is counted; the rest still link."""
        MicroRegionFactory.create(boundary=BOUNDARY)
        MicroRegionFactory.create(boundary=OTHER_BOUNDARY)

        with (
            patch(_ELEVATION, side_effect=[RuntimeError("boom"), 2100.0]),
            pytest.raises(CommandError, match="1 region failure"),
        ):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        assert MicroRegion.objects.filter(centroid_location__isnull=False).count() == 1

    def test_negative_delay_is_rejected(self) -> None:
        """--delay validates as non-negative."""
        with pytest.raises(CommandError):
            call_command(COMMAND, "--delay", "-1", stdout=StringIO())


class TestCentroidDerivationIsLossless:
    """SNOW-765: deriving from ``boundary`` reproduces the ``centre`` column.

    This is what makes the repoint provable rather than plausible, and it is
    the guard that fails if a future fixture rebuild lets the two diverge.
    Read as JSON with no database, mirroring
    ``tests/regions/test_region_aliases_fixture.py`` — all four fixtures
    parse in well under a second.
    """

    @pytest.mark.parametrize("fixture_name", _EAWS_FIXTURE_NAMES)
    def test_derived_centre_matches_the_stored_column(self, fixture_name: str) -> None:
        """Every L4 region's boundary reduces to exactly its stored centre."""
        rows: list[dict[str, Any]] = json.loads(
            (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        )
        micro_regions = [r for r in rows if r["model"] == "regions.microregion"]
        assert micro_regions, f"{fixture_name} carries no micro-regions"

        for row in micro_regions:
            fields = row["fields"]
            boundary, centre = fields.get("boundary"), fields.get("centre")
            region_id = fields["region_id"]

            # Both must be present: a region with no boundary would have no
            # route to a centroid at all once ``centre`` is dropped.
            assert boundary, f"{region_id} has no boundary"
            assert centre, f"{region_id} has no centre"

            derived = centre_from_bbox(boundary)
            assert derived["lat"] == pytest.approx(centre["lat"]), region_id
            assert derived["lon"] == pytest.approx(centre["lon"]), region_id
