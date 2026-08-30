"""
tests/regions/management/commands/test_link_region_centroid_locations.py

Covers ``link_region_centroid_locations`` (SNOW-696):
  - Mints a centroid Location at the region's derived coordinate.
  - The location is anonymous — a centroid is not a place anyone goes.
  - Regions with no usable ``boundary`` are skipped, not failed.
  - A dry run writes nothing (SNOW-719).
  - Idempotent; one bad region does not abort the batch.

SNOW-765 repointed the coordinate from the ``centre`` column onto
``boundary``; ``TestCentroidDerivationIsLossless`` guards that being
value-for-value identical to what ``centre`` held.

SNOW-771 then took the last network call out: the elevation is read from
``MicroRegion.centroid_elevation_m``, which the fixtures now carry, so the
command is wholly offline and ``bin/build.sh`` runs it on every deploy.

``TestSurvivesALoaddata`` is why that matters, and is the regression guard
for the bug it fixes: ``loaddata`` resets every column its fixtures do not
carry, so each deploy NULLed all 461 ``centroid_location`` FKs and orphaned
the Location rows behind them. It was silent — the command had reported
"461 linked, 0 failed" hours earlier.
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
from django.utils import timezone

from apps.locations.models import Location
from apps.regions.fixture_utils import centre_from_bbox
from apps.regions.models import MicroRegion
from apps.weather.models import Weather
from tests.factories import LocationFactory, MicroRegionFactory, WeatherFactory

COMMAND = "link_region_centroid_locations"

# Patched at its source module, not at the command's import site — the point
# of the assertion below is that the command does not import it at all.
_ELEVATION = "apps.locations.services.elevation.fetch_elevation"

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES_DIR = REPO_ROOT / "apps" / "regions" / "fixtures"
_EAWS_FIXTURE_NAMES = ["eaws_CH.json", "eaws_FR.json", "eaws_AT.json", "eaws_IT.json"]
CH_FIXTURE = "apps/regions/fixtures/eaws_CH.json"


def _square(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    """Return a GeoJSON Polygon for the rectangle (x0,y0)→(x1,y1)."""
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


# Bbox midpoint (lat 46.1, lon 7.4).
BOUNDARY = _square(7.3, 46.0, 7.5, 46.2)

# Bbox midpoint (lat 47.9, lon 8.9).
OTHER_BOUNDARY = _square(8.8, 47.8, 9.0, 48.0)


@pytest.mark.django_db
class TestLinkRegionCentroidLocations:
    """--commit anchors each region to a centroid Location."""

    def test_mints_a_location_at_the_derived_coordinate(self) -> None:
        """The region ends up anchored to a location with a height."""
        region = MicroRegionFactory.create(
            boundary=BOUNDARY, centroid_elevation_m=2100.0
        )

        call_command(COMMAND, "--commit", stdout=StringIO())

        region.refresh_from_db()
        assert region.centroid_location is not None
        assert region.centroid_location.latitude == 46.1
        assert region.centroid_location.longitude == 7.4
        assert region.centroid_location.elevation_m == 2100.0

    def test_makes_no_network_call(self) -> None:
        """SNOW-771: the elevation is read, not fetched.

        This is what makes a per-deploy run affordable, so it is asserted
        rather than left to the docstring. Patched at the source module, so
        the assertion holds however the command might reach it.
        """
        MicroRegionFactory.create(boundary=BOUNDARY, centroid_elevation_m=2100.0)

        with patch(_ELEVATION) as lookup:
            call_command(COMMAND, "--commit", stdout=StringIO())

        lookup.assert_not_called()

    def test_a_region_with_no_stored_elevation_still_links(self) -> None:
        """A missing elevation is not a reason to leave a region unanchored.

        Weather needs a coordinate, not a height; the map label already
        omits a null elevation, and ``Location.objects.unresolved()`` is how
        one gets filled in later.
        """
        region = MicroRegionFactory.create(boundary=BOUNDARY, centroid_elevation_m=None)

        call_command(COMMAND, "--commit", stdout=StringIO())

        region.refresh_from_db()
        assert region.centroid_location is not None
        assert region.centroid_location.elevation_m is None
        assert region.centroid_location in Location.objects.unresolved()

    def test_the_centroid_location_is_anonymous(self) -> None:
        """No name, no kind — a centroid represents the region, not a place."""
        MicroRegionFactory.create(boundary=BOUNDARY, centroid_elevation_m=2100.0)

        call_command(COMMAND, "--commit", stdout=StringIO())

        location = Location.objects.get()
        assert location.name == ""
        assert location.kind == ""

    def test_a_region_with_no_boundary_is_not_a_candidate(self) -> None:
        """A null ``boundary`` is excluded by the queryset."""
        region = MicroRegionFactory.create(boundary=None)

        call_command(COMMAND, "--commit", stdout=StringIO())

        region.refresh_from_db()
        assert region.centroid_location is None
        assert not Location.objects.exists()

    def test_an_unreadable_boundary_is_skipped_not_failed(self) -> None:
        """A malformed ``boundary`` is a fixture problem, not a run failure.

        Exiting non-zero would fail every deploy on one bad fixture row,
        now that build.sh runs this command.
        """
        MicroRegionFactory.create(
            boundary={"type": "Point", "coordinates": [7.4, 46.1]}
        )

        out = StringIO()
        call_command(COMMAND, "--commit", stdout=out)

        assert "1 skipped" in out.getvalue()
        assert not Location.objects.exists()

    def test_a_boundary_with_no_coordinates_is_skipped_not_failed(self) -> None:
        """An empty coordinate list degrades the same way."""
        MicroRegionFactory.create(boundary={"type": "Polygon", "coordinates": []})

        out = StringIO()
        call_command(COMMAND, "--commit", stdout=out)

        assert "1 skipped" in out.getvalue()
        assert not Location.objects.exists()

    def test_dry_run_writes_nothing(self) -> None:
        """SNOW-719: the preview reports without persisting anything."""
        region = MicroRegionFactory.create(
            boundary=BOUNDARY, centroid_elevation_m=2100.0
        )

        out = StringIO()
        call_command(COMMAND, stdout=out)

        assert not Location.objects.exists()
        region.refresh_from_db()
        assert region.centroid_location is None
        assert "1 region(s) would be linked" in out.getvalue()
        assert "No data written" in out.getvalue()

    def test_second_run_selects_nothing(self) -> None:
        """Idempotent — a linked region is out of the candidate set."""
        MicroRegionFactory.create(boundary=BOUNDARY, centroid_elevation_m=2100.0)

        call_command(COMMAND, "--commit", stdout=StringIO())
        out = StringIO()
        call_command(COMMAND, "--commit", stdout=out)

        assert Location.objects.count() == 1
        assert "0 region(s) linked" in out.getvalue()

    def test_a_write_failure_does_not_abort_the_batch(self) -> None:
        """SNOW-771 follow-up: build.sh runs this under ``set -o errexit``.

        An exception escaping one region would take down a deploy of three
        services sharing a database. The region is counted and skipped
        instead; the rest still link.
        """
        MicroRegionFactory.create(boundary=BOUNDARY, centroid_elevation_m=2100.0)
        MicroRegionFactory.create(boundary=OTHER_BOUNDARY, centroid_elevation_m=1800.0)

        real = Location.objects.create
        calls = {"n": 0}

        def _one_bad_write(*args: Any, **kwargs: Any) -> Location:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real(*args, **kwargs)

        with patch.object(Location.objects, "create", side_effect=_one_bad_write):
            out = StringIO()
            call_command(COMMAND, "--commit", stdout=out)

        assert "1 failed" in out.getvalue()
        assert MicroRegion.objects.filter(centroid_location__isnull=False).count() == 1

    def test_a_total_failure_does_exit_non_zero(self) -> None:
        """The one case worth blocking a deploy on — nothing linked at all.

        A partial failure lets the deploy finish; every candidate failing
        means something systemic, and a silent success there would hide it.
        """
        MicroRegionFactory.create(boundary=BOUNDARY, centroid_elevation_m=2100.0)

        with (
            patch.object(Location.objects, "create", side_effect=RuntimeError("boom")),
            pytest.raises(CommandError, match="linked nothing"),
        ):
            call_command(COMMAND, "--commit", stdout=StringIO())

    def test_one_unreadable_region_does_not_stop_the_batch(self) -> None:
        """A skipped region is counted; the rest still link."""
        MicroRegionFactory.create(boundary=BOUNDARY, centroid_elevation_m=2100.0)
        MicroRegionFactory.create(boundary=OTHER_BOUNDARY, centroid_elevation_m=1800.0)
        MicroRegionFactory.create(boundary={"type": "Point", "coordinates": [1, 2]})

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert MicroRegion.objects.filter(centroid_location__isnull=False).count() == 2


@pytest.mark.django_db
class TestSurvivesALoaddata:
    """SNOW-771 — the regression guard for the deploy-time data loss.

    ``bin/build.sh`` runs ``loaddata`` on every deploy, and ``loaddata``
    builds each instance from the fixture's fields alone and saves the whole
    row — so a column no fixture carries (``centroid_location``) is reset to
    its default every time. On staging that silently unlinked all 461
    regions and orphaned their Location rows, hours after the command had
    reported success.

    These use the real committed fixture rather than factories, because the
    bug is a property of what the fixture does and does not carry.
    """

    def test_loaddata_nulls_the_link(self) -> None:
        """The mechanism itself, asserted so it cannot be forgotten.

        If this starts failing, a fixture has gained the column and the
        re-link step in build.sh may no longer be load-bearing — a change to
        make deliberately, not to discover.
        """
        call_command("loaddata", CH_FIXTURE, verbosity=0)
        call_command(COMMAND, "--commit", stdout=StringIO())
        assert MicroRegion.objects.filter(centroid_location__isnull=False).exists()

        call_command("loaddata", CH_FIXTURE, verbosity=0)

        assert MicroRegion.objects.filter(centroid_location__isnull=False).count() == 0

    def test_relinking_after_a_loaddata_restores_every_region(self) -> None:
        """The deploy sequence: loaddata, then re-link. This is build.sh.

        The second link must restore every region the first one had, which
        is what makes the wipe harmless rather than merely detected.
        """
        call_command("loaddata", CH_FIXTURE, verbosity=0)
        call_command(COMMAND, "--commit", stdout=StringIO())
        expected = MicroRegion.objects.filter(centroid_location__isnull=False).count()
        assert expected > 0

        call_command("loaddata", CH_FIXTURE, verbosity=0)
        call_command(COMMAND, "--commit", stdout=StringIO())

        assert (
            MicroRegion.objects.filter(centroid_location__isnull=False).count()
            == expected
        )

    def test_relinking_reuses_the_same_location_rows(self) -> None:
        """The deploy cycle must not mint a new generation each time.

        Minting fresh rows orphans the previous ones AND the Weather
        hanging off them, so the map goes blank after every deploy and both
        tables grow by 461 rows per deploy. Staging reproduced exactly that
        on 2026-08-30 — 467 locations with weather before a deploy, 6
        after.
        """
        call_command("loaddata", CH_FIXTURE, verbosity=0)
        call_command(COMMAND, "--commit", stdout=StringIO())
        first_ids = set(
            MicroRegion.objects.filter(centroid_location__isnull=False).values_list(
                "centroid_location_id", flat=True
            )
        )
        first_count = Location.objects.count()
        assert first_ids

        call_command("loaddata", CH_FIXTURE, verbosity=0)
        call_command(COMMAND, "--commit", stdout=StringIO())

        second_ids = set(
            MicroRegion.objects.filter(centroid_location__isnull=False).values_list(
                "centroid_location_id", flat=True
            )
        )
        assert second_ids == first_ids
        assert Location.objects.count() == first_count

    def test_weather_survives_a_deploy(self) -> None:
        """The reason reuse matters: a row's Weather stays reachable.

        Asserted through ``public()`` because that is what the map feed
        reads — a Weather row on an orphaned location is invisible even
        though it still exists.
        """
        call_command("loaddata", CH_FIXTURE, verbosity=0)
        call_command(COMMAND, "--commit", stdout=StringIO())
        region = MicroRegion.objects.filter(centroid_location__isnull=False).first()
        assert region is not None
        WeatherFactory.create(
            location=region.centroid_location, observed_on=timezone.localdate()
        )

        call_command("loaddata", CH_FIXTURE, verbosity=0)
        call_command(COMMAND, "--commit", stdout=StringIO())

        visible = Weather.objects.filter(location__in=Location.objects.public())
        assert visible.count() == 1

    def test_a_named_location_at_the_same_coordinate_is_not_reused(self) -> None:
        """Reuse is anonymous-only.

        A curated place may sit exactly on a region's centroid. Rebinding
        the centroid onto it would put that name on the map where a
        centroid belongs, and would hand a curated row a second owner.
        """
        region = MicroRegionFactory.create(
            boundary=BOUNDARY, centroid_elevation_m=2100.0
        )
        curated = LocationFactory.create(
            name="Mont Fort", latitude=46.1, longitude=7.4, elevation_m=2100.0
        )

        call_command(COMMAND, "--commit", stdout=StringIO())

        region.refresh_from_db()
        assert region.centroid_location is not None
        assert region.centroid_location.pk != curated.pk
        assert region.centroid_location.name == ""

    def test_the_fixture_carries_an_elevation_for_every_region(self) -> None:
        """Without this the re-link is offline but produces heightless rows.

        Guards the committed data, not the code: a newly added region whose
        elevation was never resolved would link with a null elevation, and
        nothing else would say so.
        """
        call_command("loaddata", CH_FIXTURE, verbosity=0)

        missing = MicroRegion.objects.filter(
            boundary__isnull=False, centroid_elevation_m__isnull=True
        )
        assert not missing.exists(), (
            "regions with no centroid_elevation_m: "
            f"{sorted(missing.values_list('region_id', flat=True))[:10]} — "
            "run refresh_centroid_elevations --commit"
        )


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

    @pytest.mark.parametrize("fixture_name", _EAWS_FIXTURE_NAMES)
    def test_every_region_carries_a_centroid_elevation(self, fixture_name: str) -> None:
        """SNOW-771: the committed elevations are what keep the re-link offline.

        A region missing one still links, but with no height — so this
        guards the data across all four fixtures rather than only the CH one
        the DB-backed test above loads.
        """
        rows: list[dict[str, Any]] = json.loads(
            (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        )
        missing = [
            r["fields"]["region_id"]
            for r in rows
            if r["model"] == "regions.microregion"
            and r["fields"].get("boundary")
            and r["fields"].get("centroid_elevation_m") is None
        ]
        assert not missing, (
            f"{fixture_name}: {len(missing)} region(s) with no "
            f"centroid_elevation_m, e.g. {missing[:5]} — "
            "run refresh_centroid_elevations --commit"
        )
