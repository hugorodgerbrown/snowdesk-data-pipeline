"""
tests/trips/test_slope.py — the ground under a trip's snapshot (SNOW-962).

Covers the trip half of the terrain record:

  - ``_snapshot_fields`` / ``create_trip``: the source route's record is
    copied at creation, a null is copied as a null, and the copy is never
    re-read from the route afterwards;
  - ``enqueue_trip_slope_sampling``: a no-op when the snapshot already
    carries a record, and a real enqueue when it does not — the race a
    trip made from a just-uploaded route sees;
  - ``_worker_sample_trip_slopes``: the write, the one-column save, and a
    trip deleted between the enqueue and the run being an expected race;
  - ``_trip_map_payload``: the two keys the page draws from, each omitted
    entirely rather than nulled for a trip nothing has sampled.

``sample_slope`` is patched rather than the transport, exactly as
``tests/routes/test_slope_segments.py`` does — the walk itself is
established there and this module is about what a trip does with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.db import connection

from apps.locations.services.terrain import TerrainSlope, TerrainUnknown
from apps.locations.services.terrain_grid import TerrainGrid, grid_from_payload
from apps.trips.models import Trip
from apps.trips.services.slope import (
    _worker_sample_trip_slopes,
    enqueue_trip_slope_sampling,
)
from apps.trips.views import _trip_map_payload
from tests.factories import RouteFactory, TripFactory, UserFactory

_GRID_FIXTURE = (
    Path(__file__).parent.parent / "locations" / "fixtures" / "terrain" / "grid.json"
)

# A short meridian track: two coordinates about 1.1 km apart, which is
# enough to hold several 25 m strides.
MERIDIAN_TRACK = [[7.0, 46.0, 1000.0], [7.0, 46.01, 1200.0]]


@pytest.fixture(autouse=True)
def _silent_crux_probes() -> Any:
    """Answer every SNOW-911 uphill probe with "we did not see".

    ``apps.routes.services.cruxes`` imports ``sample_slope`` itself, so
    patching the walk's copy does not reach the probes, and an unpatched
    one would walk out to the real tile origin from a unit test. What the
    probes conclude is ``tests/routes/test_cruxes.py``'s subject.
    """
    with patch(
        "apps.routes.services.cruxes.sample_slope",
        return_value=TerrainSlope(
            angle_deg=None,
            aspect_deg=None,
            window_m=10.0,
            unknown=TerrainUnknown.OUTSIDE_COVERAGE,
            source=None,
        ),
    ):
        yield


def _grid() -> TerrainGrid:
    """Return the committed grid definition, as the sampler loads it."""
    return grid_from_payload(json.loads(_GRID_FIXTURE.read_text(encoding="utf-8")))


def _known(angle_deg: float) -> TerrainSlope:
    """Return a sampler answer carrying an angle and an aspect."""
    return TerrainSlope(
        angle_deg=angle_deg,
        aspect_deg=180.0,
        window_m=10.0,
        unknown=None,
        source=None,
    )


def _record(angle_deg: float = 34.2) -> dict[str, Any]:
    """Return a stored record of one segment at ``angle_deg``."""
    return {
        "window_m": 10.0,
        "stride_m": 25.0,
        "grid": "snowdesk-terrain-5m-3035",
        "points": [[7.0, 46.0], [7.0, 46.01]],
        "segments": [{"angle_deg": angle_deg, "aspect_deg": 180.0}],
        "summary": {
            "sampled_m": 1112.0,
            "surveyed_m": 1112.0,
            "steep_m": 1112.0,
            "bands": {"slope-30": 1112.0},
            "steepest_deg": angle_deg,
        },
    }


@pytest.mark.django_db
class TestTheSnapshotCarriesTheRecord:
    """What ``create_trip`` copies, and what it leaves null."""

    def test_a_sampled_route_hands_its_record_to_the_trip(self) -> None:
        """The terrain is snapshotted beside the geometry it describes."""
        import datetime

        from apps.trips.services.trips import create_trip

        organiser = UserFactory.create()
        route = RouteFactory.create(
            user=organiser, points=MERIDIAN_TRACK, slope_samples=_record(41.0)
        )

        trip = create_trip(
            user=organiser,
            route_uuid=route.uuid,
            date=datetime.date(2026, 3, 1),
            start_time=datetime.time(8, 0),
            name="Dawn start",
            description="",
            latitude=None,
            longitude=None,
        )

        assert trip.slope_samples is not None
        assert trip.slope_samples["segments"][0]["angle_deg"] == 41.0

    def test_a_later_change_to_the_route_does_not_reach_the_trip(self) -> None:
        """The snapshot rule, applied to the terrain as to the geometry."""
        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=_record(41.0))
        route = trip.route
        assert route is not None

        route.slope_samples = _record(12.0)
        route.save(update_fields=["slope_samples"])
        trip.refresh_from_db()

        assert trip.slope_samples is not None
        assert trip.slope_samples["segments"][0]["angle_deg"] == 41.0


@pytest.mark.django_db
class TestEnqueueTripSlopeSampling:
    """Whose walk a trip pays for, and when."""

    def test_a_trip_that_already_has_a_record_is_not_sampled(self) -> None:
        """The common case, and the whole saving the column buys."""
        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=_record())

        with patch("apps.trips.services.slope._worker_sample_trip_slopes") as worker:
            enqueue_trip_slope_sampling(trip)

        worker.enqueue.assert_not_called()

    def test_a_trip_with_no_record_is_sampled(self) -> None:
        """The race: a trip made before its route's own sampler landed."""
        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=None)

        with patch("apps.trips.services.slope._worker_sample_trip_slopes") as worker:
            enqueue_trip_slope_sampling(trip)

        worker.enqueue.assert_called_once_with(trip.pk)

    def test_create_trip_enqueues_outside_the_transaction(self) -> None:
        """Under ImmediateBackend the worker runs INLINE, so placement is
        the difference between a queued walk and one holding the cap's row
        lock through a tile-origin round trip per terrain tile.
        """
        import datetime

        from apps.trips.services.trips import create_trip

        organiser = UserFactory.create()
        route = RouteFactory.create(
            user=organiser, points=MERIDIAN_TRACK, slope_samples=None
        )
        depths: list[int] = []

        def _record_depth(trip: Trip) -> None:
            depths.append(len(connection.savepoint_ids))

        with patch(
            "apps.trips.services.trips.enqueue_trip_slope_sampling",
            side_effect=_record_depth,
        ):
            create_trip(
                user=organiser,
                route_uuid=route.uuid,
                date=datetime.date(2026, 3, 1),
                start_time=datetime.time(8, 0),
                name="Dawn start",
                description="",
                latitude=None,
                longitude=None,
            )

        assert depths == [0]


@pytest.mark.django_db
class TestWorkerSampleTripSlopes:
    """The write."""

    def test_it_stores_a_record_on_the_trip(self) -> None:
        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=None)

        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(34.25),
            ),
        ):
            _worker_sample_trip_slopes.func(trip.pk)

        trip.refresh_from_db()
        assert trip.slope_samples is not None
        assert (
            len(trip.slope_samples["points"]) == len(trip.slope_samples["segments"]) + 1
        )
        assert trip.slope_samples["summary"]["steepest_deg"] == 34.2

    def test_a_walk_that_learned_nothing_leaves_the_field_null(self) -> None:
        """Null is "never sampled", and the backfill selects on it."""
        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=None)

        with patch("apps.routes.services.slope_segments.load_grid", return_value=None):
            _worker_sample_trip_slopes.func(trip.pk)

        trip.refresh_from_db()
        assert trip.slope_samples is None

    def test_a_deleted_trip_is_an_expected_race(self) -> None:
        """Deleted between the enqueue and the run — not a failure."""
        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=None)
        pk = trip.pk
        trip.delete()

        # No exception is the assertion.
        _worker_sample_trip_slopes.func(pk)


@pytest.mark.django_db
class TestTripMapPayload:
    """What the page is handed to draw."""

    def test_a_sampled_trip_carries_both_keys(self) -> None:
        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=_record(41.0))

        properties = _trip_map_payload(trip)["route"]["properties"]

        assert properties["slope"]["angles"] == [41.0]
        assert properties["terrain"]["steepest_deg"] == 41.0

    def test_an_unsampled_trip_carries_neither(self) -> None:
        """Omitted, not nulled — the layers read the key's PRESENCE."""
        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=None)

        properties = _trip_map_payload(trip)["route"]["properties"]

        assert "slope" not in properties
        assert "terrain" not in properties

    def test_a_malformed_record_draws_nothing_rather_than_the_wrong_ground(
        self,
    ) -> None:
        malformed = _record()
        malformed["points"] = [[7.0, 46.0]]
        del malformed["summary"]
        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=malformed)

        properties = _trip_map_payload(trip)["route"]["properties"]

        assert "slope" not in properties
        assert "terrain" not in properties


@pytest.mark.django_db
class TestTripBulletinPanel:
    """What the trip page says about this day's bulletin (SNOW-839)."""

    def _sampled_trip(self, aspect_deg: float | None = 0.0) -> Any:
        """Return a trip on forecast ground, sampled at one aspect."""
        import datetime

        from apps.regions.models import MicroRegion
        from tests.factories import BulletinFactory, MicroRegionFactory

        region = MicroRegionFactory.create(
            region_id="CH-B01",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[7.0, 46.0], [7.1, 46.0], [7.1, 46.1], [7.0, 46.1], [7.0, 46.0]]
                ],
            },
        )
        bulletin = BulletinFactory.create(
            valid_from=datetime.datetime(2026, 3, 1, 6, tzinfo=datetime.UTC),
            valid_to=datetime.datetime(2026, 3, 1, 23, tzinfo=datetime.UTC),
            render_model_version=1,
            render_model={
                "version": 1,
                "traits": [
                    {
                        "problems": [
                            {
                                "problem_type": "persistent_weak_layers",
                                "danger_rating_value": "considerable",
                                "aspects": ["N"],
                                "elevation": None,
                            }
                        ]
                    }
                ],
            },
        )
        bulletin.regions.add(region)
        assert MicroRegion.objects.filter(pk=region.pk).exists()

        coordinates = [(7.02, 46.02), (7.03, 46.03)]
        return TripFactory.create(
            date=datetime.date(2026, 3, 1),
            points=[[lon, lat, 2500.0] for lon, lat in coordinates],
            point_count=2,
            slope_samples={
                "window_m": 10.0,
                "stride_m": 25.0,
                "grid": "g",
                "points": [[lon, lat] for lon, lat in coordinates],
                "segments": [{"angle_deg": 34.0, "aspect_deg": aspect_deg}]
                if aspect_deg is not None
                else [{"unknown": "outside_coverage"}],
                "cruxes": [],
            },
        )

    def test_the_panel_names_the_problem_the_line_enters(self) -> None:
        from apps.trips.views import _bulletin_readings

        readings = _bulletin_readings(self._sampled_trip())

        assert len(readings) == 1
        assert readings[0]["overlaps"][0].problem_label == "Persistent weak layers"
        assert readings[0]["overlaps"][0].aspects == "N"
        assert readings[0]["bulletin_url"]

    def test_a_line_outside_every_problem_reports_no_overlaps(self) -> None:
        """And the panel says so rather than staying silent — a reader
        shown nothing assumes the day was clear.
        """
        from apps.trips.views import _bulletin_readings

        # South-facing, against a problem listed for N alone.
        readings = _bulletin_readings(self._sampled_trip(aspect_deg=180.0))

        assert len(readings) == 1
        assert readings[0]["overlaps"] == []
        assert readings[0]["bulletin"] is not None

    def test_an_unsampled_trip_renders_no_panel(self) -> None:
        """No aspect, no join — and no claim that the line meets nothing."""
        from apps.trips.views import _bulletin_readings

        trip = TripFactory.create(points=MERIDIAN_TRACK, slope_samples=None)

        assert _bulletin_readings(trip) == []
