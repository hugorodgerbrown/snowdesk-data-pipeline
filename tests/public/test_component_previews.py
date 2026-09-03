"""
tests/public/test_component_previews.py — Tests for the help illustrations' synthetic contexts.

Covers the one piece of ``apps.public.component_previews`` with arithmetic
in it: ``profile_paths``, the port of ``buildPaths`` from
static/js/elevation_profile_core.js that shapes the route popup's chart on
the Routes article. The assertions are the same properties
tests/js/test_elevation_profile_core.js pins on the original — full width,
inverted y, true along-track position, nothing outside the box — so the two
implementations are held to one contract from both sides.

No database: the module builds everything in memory, which is its whole
point.
"""

from __future__ import annotations

import re

from apps.public.component_previews import (
    PROFILE_VIEWBOX,
    help_illustrations,
    profile_paths,
)

WIDTH, HEIGHT = PROFILE_VIEWBOX


def vertices(d: str) -> list[tuple[float, float]]:
    """Pull every ``[ML]x y`` vertex out of a path ``d`` string, in order."""
    return [(float(x), float(y)) for x, y in re.findall(r"[ML]([\d.]+) ([\d.]+)", d)]


class TestProfilePaths:
    """The projection matches the chart the map draws."""

    def test_spans_the_full_width_and_inverts_the_y_axis(self) -> None:
        """The first point sits at x=0, the last at the right edge, higher = smaller y."""
        (paths,) = profile_paths([(0, 1000), (2000, 2000)])
        points = vertices(paths["line"])

        assert points[0][0] == 0
        assert points[1][0] == WIDTH
        # SVG y grows downward, so the higher point must sit at the smaller y.
        assert points[0][1] > points[1][1]

    def test_places_a_mid_track_point_at_its_true_distance(self) -> None:
        """A point halfway along the track lands halfway across the box."""
        (paths,) = profile_paths([(0, 1000), (1500, 1500), (3000, 2000)])
        points = vertices(paths["line"])

        assert points[1][0] == WIDTH / 2

    def test_keeps_every_vertex_inside_the_box(self) -> None:
        """Nothing is drawn above the top edge or below the floor."""
        (paths,) = profile_paths([(0, 1200), (1000, 900), (2000, 2400), (3000, 1800)])

        for _, y in vertices(paths["line"]):
            assert 0 <= y <= HEIGHT

    def test_a_flat_track_is_a_level_line_through_the_middle(self) -> None:
        """No vertical range is centred rather than divided by zero."""
        (paths,) = profile_paths([(0, 1500), (1000, 1500)])

        assert {y for _, y in vertices(paths["line"])} == {HEIGHT / 2}

    def test_the_area_closes_along_the_floor(self) -> None:
        """The fill runs down to the baseline, back along it, and closes."""
        (paths,) = profile_paths([(0, 1000), (1000, 2000)])

        assert paths["area"].startswith(paths["line"])
        assert paths["area"].endswith("Z")

    def test_fewer_than_two_points_draws_nothing(self) -> None:
        """A lone vertex has no line, matching the JavaScript's dropped run."""
        assert profile_paths([(0, 1000)]) == []
        assert profile_paths([]) == []


class TestRoutePopupContext:
    """The article's popup context is complete and needs no database."""

    def test_route_popup_is_in_the_help_context(self) -> None:
        """One route seen twice: the popup names the panel's first row."""
        context = help_illustrations()
        popup = context["route_popup"]

        assert popup["name"] == context["panels"]["routes"]["rows"][0]["label"]
        assert popup["meta"] == context["panels"]["routes"]["rows"][0]["meta"]
        assert popup["caption"]
        assert len(popup["paths"]) == 1
