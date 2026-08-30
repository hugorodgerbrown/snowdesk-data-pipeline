"""
tests/regions/management/commands/test_link_region_centroid_locations.py

Covers ``link_region_centroid_locations`` (SNOW-696):
  - Mints a centroid Location with its elevation resolved.
  - The location is anonymous — a centroid is not a place anyone goes.
  - Regions with no usable ``centre`` are skipped, not failed.
  - A dry run writes nothing (SNOW-719).
  - Idempotent; one failing region does not abort the batch.

SNOW-762 removed this command's forecast-cell half with the weather app;
what is left is the Location and its elevation.

Every Open-Meteo call is patched out.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.locations.models import Location
from apps.regions.models import MicroRegion
from tests.factories import MicroRegionFactory

COMMAND = "link_region_centroid_locations"
_BASE = "apps.regions.management.commands.link_region_centroid_locations"
_ELEVATION = f"{_BASE}.fetch_elevation"

CENTRE = {"lat": 46.1, "lon": 7.4}


@pytest.mark.django_db
class TestLinkRegionCentroidLocations:
    """--commit anchors each region to a centroid Location."""

    def test_mints_a_resolved_centroid_location(self) -> None:
        """The region ends up anchored to a location with a height."""
        region = MicroRegionFactory.create(centre=CENTRE)

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
        MicroRegionFactory.create(centre=CENTRE)

        with patch(_ELEVATION, return_value=2100.0):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        location = Location.objects.get()
        assert location.name == ""
        assert location.kind == ""

    def test_a_region_with_no_centre_is_not_a_candidate(self) -> None:
        """A null ``centre`` is excluded by the queryset."""
        region = MicroRegionFactory.create(centre=None)

        with patch(_ELEVATION) as lookup:
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        lookup.assert_not_called()
        region.refresh_from_db()
        assert region.centroid_location is None

    def test_an_unreadable_centre_is_skipped_not_failed(self) -> None:
        """A malformed ``centre`` is a fixture problem, not a run failure.

        ``centre`` is a JSONField, so its shape is not schema-guaranteed.
        Exiting non-zero would make one bad fixture row fail every
        scheduled run.
        """
        MicroRegionFactory.create(centre={"lat": "north", "lon": 7.4})

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
        region = MicroRegionFactory.create(centre=CENTRE)

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
        MicroRegionFactory.create(centre=CENTRE)

        with patch(_ELEVATION, return_value=2100.0):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())
            with patch(_ELEVATION) as second:
                call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        second.assert_not_called()

    def test_one_failure_does_not_stop_the_batch(self) -> None:
        """A failing region is counted; the rest still link."""
        MicroRegionFactory.create(centre=CENTRE)
        MicroRegionFactory.create(centre={"lat": 47.9, "lon": 8.9})

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
