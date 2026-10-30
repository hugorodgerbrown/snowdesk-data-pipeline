"""
tests/routes/test_ski_tours.py — finding SAC's ski tours (SNOW-1278).

The services in apps/routes/services/ski_tours.py: which end of a tour is
its target, where a summit stands and what it counts, the hut and way-down
rules, the guide time, the tour as route points and an unsaved Route, the
terrain kept on the tour, saving a copy, and every tour thinned for a
zoomed-out map (SNOW-1285).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.routes.models import Route, SkiTour
from apps.routes.services.routes import RouteLimitReached
from apps.routes.services.ski_tours import (
    build_summits,
    cut_legs,
    draft_tour_route,
    ends,
    far_end,
    is_descent,
    is_hut,
    leg_parts,
    localised,
    needs_only_marking,
    overview_geometry,
    runs_reversed,
    sac_time,
    save_ski_tour,
    summit_key,
    summits,
    target_end,
    tour_overview,
    tour_points,
    tour_properties,
    tour_slope_samples,
    tour_title,
    tours_to_sample,
)
from tests.factories import RouteFactory, SkiTourFactory, UserFactory

pytestmark = pytest.mark.django_db

TOP = [10.1, 46.85]
VALLEY = [10.08, 46.83]
RECORD: dict[str, Any] = {"v": 1, "segments": []}
# What a walk keeps since SNOW-1306: the record, marked with the order it
# was walked in — SAC's own, for a way up drawn upwards.
KEPT: dict[str, Any] = {**RECORD, "walked_reversed": False}


def _line(*points: list[float]) -> dict[str, Any]:
    """A one-part MultiLineString through ``points``."""
    return {"type": "MultiLineString", "coordinates": [list(points)]}


def _tour(**fields: Any) -> SkiTour:
    """A tour to Signalhorn, up from the valley unless told otherwise."""
    defaults: dict[str, Any] = {
        "target_name": "Signalhorn (GR)",
        "target_altitude_m": 3207,
        "geometry": _line(VALLEY, TOP),
    }
    defaults.update(fields)
    return SkiTourFactory.create(**defaults)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Start each test with no cached summits."""
    cache.clear()


class TestPureRules:
    """The rules that read one tour's own fields."""

    @pytest.mark.parametrize(
        ("name", "hut"),
        [
            ("Silvrettahütte SAC", True),
            ("Chamonna Tuoi CAS", True),
            ("Cabane de Moiry", True),
            ("Signalhorn (GR)", False),
        ],
    )
    def test_a_hut_is_known_by_its_name(self, name: str, hut: bool) -> None:
        """Hut words in four languages."""
        assert is_hut(name) is hut

    def test_a_way_down_has_a_descent_and_no_ascent(self) -> None:
        """SAC's "Abfahrt" lines."""
        assert is_descent(_tour(ascent_m=None, descent_m=1800)) is True
        assert is_descent(_tour(ascent_m=900, descent_m=900)) is False

    @pytest.mark.parametrize(
        ("name", "names"),
        [
            ("Descente à la cabane de Chanrion par le versant SE", {}),
            ("Zur Cabane de Chanrion", {"fr": "Descente à la cabane de Chanrion"}),
            ("Abfahrt nach Lavin", {}),
            ("Discesa a Bivio", {}),
        ],
    )
    def test_a_way_down_is_what_sac_names_one_whatever_its_figures(
        self, name: str, names: dict[str, str]
    ) -> None:
        """A named descent with an ascent figure too is still a way down (SNOW-1304)."""
        tour = _tour(name=name, names=names, ascent_m=420, descent_m=1660)
        assert is_descent(tour) is True

    def test_a_way_up_whose_name_mentions_a_descent_later_is_not_one(self) -> None:
        """Only the word that opens the name counts."""
        tour = _tour(
            name="Von der Hütte, Abfahrt wie Aufstieg", ascent_m=900, descent_m=900
        )
        assert is_descent(tour) is False

    def test_the_summit_key_keeps_two_summits_of_one_name_apart(self) -> None:
        """Name and height."""
        assert summit_key("Piz Buin", 3312) == "piz-buin-3312"
        assert summit_key("Piz Buin", 3312) != summit_key("Piz Buin", 2800)
        assert summit_key("", None) == "target-0"

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("03:30 h", "3 h 30"),
            ("04:00 h", "4 h"),
            ("00:45 h", "45 min"),
            ("2-3 h", "2-3 h"),
            ("", ""),
        ],
    )
    def test_the_guide_time_is_written_as_the_app_writes_one(
        self, label: str, expected: str
    ) -> None:
        """Write "03:30 h" as "3 h 30", and anything else as SAC wrote it."""
        assert sac_time(label) == expected

    def test_the_target_end_is_the_last_point_whichever_way_the_tour_goes(
        self,
    ) -> None:
        """SAC draws its lines valley to summit, ways down too (SNOW-1304)."""
        up = _tour()
        down = _tour(ascent_m=None, descent_m=1000)
        assert target_end(up) == tuple(TOP)
        assert target_end(down) == tuple(TOP)
        assert far_end(up, (TOP[0], TOP[1])) == tuple(VALLEY)

    def test_a_tour_drawn_backwards_is_read_by_the_end_nearer_its_summit(self) -> None:
        """``near`` overrides the direction."""
        backwards = _tour(geometry=_line(TOP, VALLEY))
        assert target_end(backwards) == tuple(VALLEY)
        assert target_end(backwards, near=(10.1001, 46.8501)) == tuple(TOP)

    def test_a_tour_with_no_line_has_no_ends(self) -> None:
        """A ValueError, not an IndexError."""
        with pytest.raises(ValueError, match="no line"):
            ends(_tour(geometry={"type": "MultiLineString", "coordinates": []}))

    def test_the_name_follows_the_language(self) -> None:
        """The active language's name, else the tour's own."""
        assert localised({"en": "From the hut"}, "Von der Hütte") == "From the hut"
        assert localised({"fr": "De la cabane"}, "Von der Hütte") == "Von der Hütte"


class TestSummits:
    """Where a summit stands and what it counts."""

    def test_a_summit_stands_at_its_tours_target_ends(self) -> None:
        """Two ways up and one down, placed at the top whichever way drawn."""
        _tour(geometry=_line(VALLEY, TOP))
        _tour(geometry=_line([10.12, 46.86], [10.1002, 46.8502]))
        _tour(ascent_m=None, descent_m=1500, geometry=_line(TOP, [10.0, 46.8]))
        (summit,) = build_summits(list(SkiTour.objects.all()))
        # The slug, as the summit list URL carries it.
        slug = summit.key
        assert slug == "signalhorn-gr-3207"
        assert (summit.up, summit.down, summit.hut) == (2, 1, False)
        assert summit.lon == pytest.approx(10.1, abs=0.001)
        assert summit.lat == pytest.approx(46.85, abs=0.001)
        assert summit.as_properties()["kind"] == "summit"

    def test_a_tour_with_no_target_has_no_summit(self) -> None:
        """No name, no handle."""
        _tour(target_name="")
        assert build_summits(list(SkiTour.objects.all())) == []

    def test_summits_are_cached_until_the_tours_change(self) -> None:
        """A second read queries only the stamp; a new tour reads afresh."""
        _tour()
        assert set(summits()) == {"signalhorn-gr-3207"}
        _tour(target_name="Piz Buin", target_altitude_m=3312)
        assert set(summits()) == {"signalhorn-gr-3207", "piz-buin-3312"}


class TestTheOverview:
    """Every tour at once, thinned, for a map zoomed out past z12."""

    def test_a_line_is_thinned_rounded_and_flattened(self) -> None:
        """A point within the tolerance goes; heights go; five places stay."""
        geometry = overview_geometry(
            {
                "type": "MultiLineString",
                "coordinates": [
                    [[10.0, 46.8, 1800.0], [10.05, 46.80001], [10.1, 46.8]],
                    [[10.123456789, 46.9], [10.2, 47.0]],
                    [[10.3, 47.1]],
                ],
            }
        )
        assert geometry == {
            "type": "MultiLineString",
            "coordinates": [
                [[10.0, 46.8], [10.1, 46.8]],
                [[10.12346, 46.9], [10.2, 47.0]],
            ],
        }

    def test_a_line_with_nothing_to_draw_is_none(self) -> None:
        """No part with two points."""
        assert (
            overview_geometry({"type": "LineString", "coordinates": [[1, 2]]}) is None
        )
        assert overview_geometry({}) is None

    def test_the_overview_is_cached_until_the_tours_change(self) -> None:
        """A tour with no line is left out; a new tour reads afresh."""
        _tour()
        _tour(geometry={"type": "MultiLineString", "coordinates": []})
        (first,) = tour_overview()
        assert first["properties"]["summit"] == "signalhorn-gr-3207"
        _tour(target_name="Piz Buin", target_altitude_m=3312)
        assert len(tour_overview()) == 2


class TestTheTourAsARoute:
    """The tour's line as route points and as an unsaved Route."""

    def test_the_parts_are_joined_and_a_repeated_point_dropped(self) -> None:
        """Two parts sharing a point make three points."""
        tour = _tour(
            geometry={
                "type": "MultiLineString",
                "coordinates": [[VALLEY, [10.09, 46.84]], [[10.09, 46.84], TOP]],
            }
        )
        assert tour_points(tour) == [
            [*VALLEY, None],
            [10.09, 46.84, None],
            [*TOP, None],
        ]

    def test_parts_stored_out_of_order_are_chained_from_start_to_finish(
        self,
    ) -> None:
        """The Silvrettahütte way up, as swisstopo stores it (SNOW-1282).

        The way to the summit, a spur off it, then the hut's part last: the
        line runs hut → junction → summit and leaves the spur out.
        """
        hut, junction = [10.0415, 46.8544], [10.0863, 46.8499]
        summit, spur = [10.0969, 46.8488], [10.0905, 46.8510]
        tour = _tour(
            geometry={
                "type": "MultiLineString",
                "coordinates": [[junction, summit], [junction, spur], [hut, junction]],
            }
        )
        assert [p[:2] for p in tour_points(tour)] == [hut, junction, summit]
        assert ends(tour) == (tuple(hut), tuple(summit))

    def test_a_chain_drawn_mostly_backwards_runs_the_way_it_was_drawn(
        self,
    ) -> None:
        """Both parts drawn summit-first: the line starts at the summit."""
        middle = [10.09, 46.84]
        tour = _tour(
            geometry={
                "type": "MultiLineString",
                "coordinates": [[middle, VALLEY], [TOP, middle]],
            }
        )
        assert [p[:2] for p in tour_points(tour)] == [TOP, middle, VALLEY]

    def test_parts_that_do_not_join_keep_their_stored_order(self) -> None:
        """A gap in swisstopo's own data: nothing to chain."""
        tour = _tour(
            geometry={
                "type": "MultiLineString",
                "coordinates": [[[10.09, 46.84], TOP], [VALLEY, [10.085, 46.835]]],
            }
        )
        assert [p[:2] for p in tour_points(tour)] == [
            [10.09, 46.84],
            TOP,
            VALLEY,
            [10.085, 46.835],
        ]

    def test_a_tour_skied_down_its_ascent_stops_at_the_top(self) -> None:
        """SAC's points, and no more: nothing is added for the way down (SNOW-1287)."""
        middle = [10.09, 46.84]
        tour = _tour(ascent_m=900, descent_m=900, geometry=_line(VALLEY, middle, TOP))
        assert [p[:2] for p in tour_points(tour)] == [VALLEY, middle, TOP]

    def test_the_draft_is_titled_by_the_summit_and_marked_a_tour(self) -> None:
        """Title it "Signalhorn (GR) · <name>", source SKI_TOUR, the tour's uuid."""
        tour = _tour(name="Von der Silvrettahütte SAC")
        draft = draft_tour_route(tour)
        assert draft._state.adding
        assert draft.name == "Signalhorn (GR) · Von der Silvrettahütte SAC"
        assert tour_title(_tour(target_name="", name="Alone")) == "Alone"
        assert draft.source == Route.Source.SKI_TOUR
        assert draft.ski_tour_uuid == tour.source_uuid
        assert draft.distance_m > 0

    def test_the_properties_carry_sacs_grade_and_time_not_its_heights(self) -> None:
        """Grade, time and the guide's page; never SAC's height figures (SNOW-1302)."""
        tour = _tour(
            ascent_m=960,
            ascent_time_label="03:30 h",
            urls={"de": "https://www.sac-cas.ch/de/"},
        )
        props = tour_properties(tour)
        assert props["tour_uuid"] == str(tour.source_uuid)
        assert props["sac_time"] == "3 h 30"
        assert props["sac_url"] == "https://www.sac-cas.ch/de/"
        assert props["down"] is False
        assert not {"sac_ascent_m", "sac_descent_m", "via_ascent"} & props.keys()
        assert tour_properties(_tour(ascent_m=None, descent_m=1800))["down"] is True


class TestTheOrder:
    """A way down runs from its summit, a way up to it (SNOW-1306)."""

    def test_a_way_up_drawn_upwards_keeps_sacs_order(self) -> None:
        """Valley first, summit last."""
        tour = _tour()
        assert runs_reversed(tour) is False
        assert tour_points(tour)[0][:2] == VALLEY

    def test_a_way_down_drawn_upwards_runs_from_the_top(self) -> None:
        """The same two points, turned round; none added."""
        tour = _tour(name="Abfahrt nach Guarda", ascent_m=None, descent_m=900)
        assert runs_reversed(tour) is True
        assert tour_points(tour) == [[*TOP, None], [*VALLEY, None]]

    def test_a_way_up_drawn_downwards_ends_at_the_top(self) -> None:
        """Its summit is where its siblings put it, so its own order is no guide."""
        _tour()
        _tour(geometry=_line([10.07, 46.84], TOP))
        backwards = _tour(geometry=_line(TOP, [10.09, 46.82]))
        assert runs_reversed(backwards) is True
        assert tour_points(backwards)[-1][:2] == TOP

    def test_the_terrain_settles_a_lone_way_down_drawn_downwards(self) -> None:
        """Its own summit says nothing; the heights it was walked over do."""
        tour = _tour(
            geometry=_line(TOP, VALLEY),
            ascent_m=None,
            descent_m=900,
            slope_samples={**RECORD, "heights": [3100.0, None, 1700.0]},
        )
        assert runs_reversed(tour) is False
        assert tour_points(tour)[0][:2] == TOP
        assert needs_only_marking(tour) is True

    def test_a_walk_the_wrong_way_is_walked_again_the_other_way(self) -> None:
        """A lone way down drawn downwards: the guess turns it, the heights turn it back."""
        tour = _tour(geometry=_line(TOP, VALLEY), ascent_m=None, descent_m=900)
        assert runs_reversed(tour) is True
        walks = [
            {**RECORD, "heights": [1700.0, 3100.0]},
            {**RECORD, "heights": [3100.0, 1700.0]},
        ]
        with patch(
            "apps.routes.services.ski_tours.build_slope_samples", side_effect=walks
        ) as build:
            record = tour_slope_samples(tour, sample=True)
        assert build.call_count == 2
        assert build.call_args.args[0][0][:2] == TOP
        assert record == {**walks[1], "walked_reversed": False}
        assert runs_reversed(tour) is False

    def test_a_tour_with_no_line_is_not_reversed(self) -> None:
        """Nothing to turn round."""
        assert (
            runs_reversed(
                _tour(geometry={"type": "MultiLineString", "coordinates": []})
            )
            is False
        )


class TestTheTerrain:
    """The terrain is sampled once per tour and kept on it."""

    def test_nothing_is_sampled_unless_asked(self) -> None:
        """A tour never sampled answers None, without walking the grid."""
        tour = _tour()
        with patch("apps.routes.services.ski_tours.build_slope_samples") as build:
            assert tour_slope_samples(tour, sample=False) is None
        build.assert_not_called()

    def test_a_sample_is_kept_on_the_tour_and_read_back(self) -> None:
        """Asked once, held after."""
        tour = _tour()
        with patch(
            "apps.routes.services.ski_tours.build_slope_samples", return_value=RECORD
        ) as build:
            assert tour_slope_samples(tour, sample=True) == KEPT
            tour.refresh_from_db()
            assert tour.slope_samples == KEPT
            assert tour_slope_samples(tour, sample=True) == KEPT
        build.assert_called_once()

    def test_a_record_walked_there_and_back_is_not_held(self) -> None:
        """SNOW-1286's doubled line: walked again, one way (SNOW-1287)."""
        doubled = {**RECORD, "there_and_back": True}
        tour = _tour(ascent_m=900, descent_m=900, slope_samples=doubled)
        assert tour_slope_samples(tour, sample=False) is None
        with patch(
            "apps.routes.services.ski_tours.build_slope_samples",
            return_value=dict(RECORD),
        ) as build:
            assert tour_slope_samples(tour, sample=True) == KEPT
        assert len(build.call_args.args[0]) == 2
        tour.refresh_from_db()
        assert tour.slope_samples == KEPT

    def test_a_one_way_record_is_held_on_a_tour_skied_down_its_ascent(
        self,
    ) -> None:
        """The record matches SAC's line, which is the one drawn."""
        tour = _tour(ascent_m=900, descent_m=900, slope_samples=RECORD)
        assert tour_slope_samples(tour, sample=False) == RECORD

    def test_an_unmarked_record_is_held_and_marked_when_the_order_stands(
        self,
    ) -> None:
        """Walked before SNOW-1306 on a way up drawn upwards: marked, not walked."""
        tour = _tour(slope_samples=RECORD)
        assert needs_only_marking(tour) is True
        assert tour_slope_samples(tour, sample=False) == RECORD
        with patch("apps.routes.services.ski_tours.build_slope_samples") as build:
            assert tour_slope_samples(tour, sample=True) == KEPT
        build.assert_not_called()
        tour.refresh_from_db()
        assert tour.slope_samples == KEPT
        assert needs_only_marking(tour) is False

    def test_an_unmarked_record_is_not_held_when_the_order_flips(self) -> None:
        """A way down drawn upwards now runs from its top: walked again."""
        tour = _tour(ascent_m=None, descent_m=900, slope_samples=RECORD)
        assert needs_only_marking(tour) is False
        assert tour_slope_samples(tour, sample=False) is None
        with patch(
            "apps.routes.services.ski_tours.build_slope_samples",
            return_value=dict(RECORD),
        ) as build:
            assert tour_slope_samples(tour, sample=True) == {
                **RECORD,
                "walked_reversed": True,
            }
        assert build.call_args.args[0][0][:2] == TOP

    def test_a_line_off_the_terrain_model_keeps_nothing(self) -> None:
        """None from the grid stores nothing."""
        tour = _tour()
        with patch(
            "apps.routes.services.ski_tours.build_slope_samples", return_value=None
        ):
            assert tour_slope_samples(tour, sample=True) is None
        tour.refresh_from_db()
        assert tour.slope_samples is None


class TestTheLegs:
    """SNOW-1310: a tour is drawn as its legs, as a route is, cut once."""

    TRACK: list[list[float | None]] = [
        [10.0, 46.0, 1800.0],
        [10.0, 46.01, 2400.0],
        [10.0, 46.02, 2100.0],
    ]
    LEGS: list[dict[str, Any]] = [
        {"i": 1, "climbing": True, "point_from": 0, "point_to": 1},
        {"i": 2, "climbing": False, "point_from": 1, "point_to": 2},
    ]
    CUT: list[dict[str, Any]] = [
        {"climbing": True, "line": [[10.0, 46.0], [10.0, 46.01]]},
        {"climbing": False, "line": [[10.0, 46.01], [10.0, 46.02]]},
    ]

    def _patched(self) -> Any:
        """Patch the terrain track and the legs cut from it."""
        track = patch(
            "apps.routes.services.ski_tours.terrain_points", return_value=self.TRACK
        )
        legs = patch("apps.routes.services.ski_tours.wire_legs", return_value=self.LEGS)
        return track, legs

    def test_the_legs_are_cut_from_the_held_terrain(self) -> None:
        """The legs the panel shows, sliced out of the terrain track."""
        tour = _tour(slope_samples=KEPT)
        track, legs = self._patched()
        with track, legs:
            assert cut_legs(tour) == self.CUT

    def test_a_tour_with_no_terrain_has_no_legs_to_cut(self) -> None:
        """Nothing held, nothing cut."""
        assert cut_legs(_tour()) == []

    def test_keeping_a_record_stores_its_legs(self) -> None:
        """A walk stores the legs with the record, and moves updated_at."""
        tour = _tour()
        before = tour.updated_at
        track, legs = self._patched()
        with (
            track,
            legs,
            patch(
                "apps.routes.services.ski_tours.build_slope_samples",
                return_value=dict(RECORD),
            ),
        ):
            tour_slope_samples(tour, sample=True)
        tour.refresh_from_db()
        assert tour.legs == self.CUT
        assert tour.updated_at > before

    def test_a_held_record_with_no_legs_is_cut_without_a_walk(self) -> None:
        """Walked before legs were stored: cut, and nothing walked again."""
        tour = _tour(slope_samples=KEPT)
        assert SkiTour.objects.filter(pk=tour.pk, legs__isnull=True).exists()
        assert needs_only_marking(tour) is True
        assert tours_to_sample().filter(pk=tour.pk).exists()
        track, legs = self._patched()
        with (
            track,
            legs,
            patch("apps.routes.services.ski_tours.build_slope_samples") as build,
        ):
            tour_slope_samples(tour, sample=True)
        build.assert_not_called()
        tour.refresh_from_db()
        assert tour.legs == self.CUT
        assert not tours_to_sample().filter(pk=tour.pk).exists()
        assert needs_only_marking(tour) is False

    def test_the_layer_draws_the_stored_legs(self) -> None:
        """Read, never cut, per request."""
        tour = _tour(legs=self.CUT)
        assert leg_parts(tour) == [
            (True, [[10.0, 46.0], [10.0, 46.01]]),
            (False, [[10.0, 46.01], [10.0, 46.02]]),
        ]

    def test_a_tour_with_no_legs_is_sacs_line_in_no_direction(self) -> None:
        """No direction is claimed: SAC's own line, ``climbing`` None."""
        tour = _tour(geometry=_line(TOP, VALLEY))
        assert leg_parts(tour) == [(None, [list(TOP[:2]), list(VALLEY[:2])])]
        assert leg_parts(_tour(geometry=_line(VALLEY))) == []


class TestSave:
    """Saving a tour copies it onto the reader's routes."""

    def test_a_sampled_tour_is_copied_with_its_terrain(self) -> None:
        """No second sampling."""
        user = UserFactory.create()
        tour = _tour(slope_samples=RECORD)
        with patch(
            "apps.routes.services.ski_tours.enqueue_route_slope_sampling"
        ) as enqueue:
            route = save_ski_tour(user, tour)
        enqueue.assert_not_called()
        assert route.user == user
        assert route.slope_samples == RECORD
        assert route.source == Route.Source.SKI_TOUR
        assert route.ski_tour_uuid == tour.source_uuid

    def test_a_tour_never_opened_is_sampled_after_the_save(self) -> None:
        """As a drawn route is."""
        user = UserFactory.create()
        with patch(
            "apps.routes.services.ski_tours.enqueue_route_slope_sampling"
        ) as enqueue:
            route = save_ski_tour(user, _tour())
        enqueue.assert_called_once_with(route)

    def test_a_record_walked_there_and_back_is_never_copied(self) -> None:
        """SNOW-1286's doubled record: the copy is sampled afresh (SNOW-1287)."""
        user = UserFactory.create()
        doubled = {**RECORD, "there_and_back": True}
        tour = _tour(ascent_m=900, descent_m=900, slope_samples=doubled)
        with patch(
            "apps.routes.services.ski_tours.enqueue_route_slope_sampling"
        ) as enqueue:
            route = save_ski_tour(user, tour)
        assert route.slope_samples is None
        enqueue.assert_called_once_with(route)

    @override_settings(ROUTES_MAX_PER_USER=1)
    def test_the_cap_holds(self) -> None:
        """As for any copy."""
        user = UserFactory.create()
        RouteFactory.create(user=user)
        with pytest.raises(RouteLimitReached):
            save_ski_tour(user, _tour())
