"""
tests/routes/test_canonical.py — the committed corpus of real tracks (SNOW-989).

Two jobs. The first is that the files are there and parse: a corpus nothing
checks is a corpus that quietly rots. The second is the property the corpus
exists FOR — a spread of point spacings wide enough to show that a window
specified in points is not a window specified in metres.
"""

from __future__ import annotations

import math

import pytest

from apps.core.geo import haversine_m
from apps.routes.services.canonical import (
    CANONICAL_DIR,
    canonical_documents,
    canonical_paths,
)
from apps.routes.services.gpx import MAX_POINTS, parse_gpx

# What each file is expected to hold. Spelled out rather than derived so a
# track that is replaced or re-exported fails here loudly, instead of the
# suite silently re-baselining itself onto whatever was committed.
EXPECTED = {
    "chamonix-col-de-balme.gpx": 1134,
    "hidden-valley.gpx": 524,
    "mont-fort-backside.gpx": 694,
    "mont-fort-col-de-la-chaux.gpx": 894,
}


def _mean_spacing(points: list[list[float | None]]) -> float:
    """Return the mean along-track distance between consecutive points.

    ``points`` is typed ``float | None`` because ``ele`` may be absent, so
    the two ordinates this reads are narrowed explicitly. Every canonical
    track is asserted to carry a full elevation series elsewhere in this
    module, but longitude and latitude are never null in any case — a point
    without them is not a point.

    Args:
        points: Parsed ``[lon, lat, ele]`` coordinates.

    Returns:
        Metres per point.

    """
    total = 0.0
    for index in range(1, len(points)):
        previous, current = points[index - 1], points[index]
        lon_1, lat_1 = previous[0], previous[1]
        lon_2, lat_2 = current[0], current[1]
        assert lon_1 is not None and lat_1 is not None
        assert lon_2 is not None and lat_2 is not None
        total += haversine_m(lat_1, lon_1, lat_2, lon_2)
    return total / (len(points) - 1)


class TestTheCorpusIsPresent:
    """The files exist, and are the ones we think they are."""

    def test_the_directory_is_checked_out(self) -> None:
        """A missing fixtures directory is a broken checkout, not zero routes."""
        assert CANONICAL_DIR.is_dir()

    def test_holds_exactly_the_expected_files(self) -> None:
        """Adding or removing a track is a deliberate edit to this test too."""
        assert {path.name for path in canonical_paths()} == set(EXPECTED)

    def test_paths_are_sorted(self) -> None:
        """A stable order, because ``Path.glob`` does not promise one."""
        names = [path.name for path in canonical_paths()]
        assert names == sorted(names)


class TestEveryTrackParses:
    """Each file goes through the project's own parser."""

    @pytest.mark.parametrize("filename", sorted(EXPECTED))
    def test_parses_with_the_expected_point_count(self, filename: str) -> None:
        """The committed geometry is what it was when it was committed."""
        raw = dict(canonical_documents())[filename]

        parsed = parse_gpx(raw)

        assert parsed.point_count == EXPECTED[filename]

    @pytest.mark.parametrize("filename", sorted(EXPECTED))
    def test_carries_elevation_on_every_point(self, filename: str) -> None:
        """No null ``ele`` — every leg figure downstream depends on it."""
        parsed = parse_gpx(dict(canonical_documents())[filename])

        assert all(point[2] is not None for point in parsed.points)

    @pytest.mark.parametrize("filename", sorted(EXPECTED))
    def test_is_untimed(self, filename: str) -> None:
        """Exported from stored points, which never held per-point times.

        The pair is always known or unknown together, so asserting both is
        asserting the contract rather than repeating one fact.
        """
        parsed = parse_gpx(dict(canonical_documents())[filename])

        assert parsed.started_at is None
        assert parsed.finished_at is None

    @pytest.mark.parametrize("filename", sorted(EXPECTED))
    def test_is_stored_whole(self, filename: str) -> None:
        """Under ``MAX_POINTS``, so no track here is a simplified remnant.

        Worth pinning: a corpus whose spacings were partly ours and partly
        the recording devices' could not support the spacing argument below.
        """
        parsed = parse_gpx(dict(canonical_documents())[filename])

        assert parsed.source_point_count <= MAX_POINTS
        assert parsed.point_count == parsed.source_point_count


class TestTheSpacingSpread:
    """The property the corpus exists for."""

    def test_spans_at_least_a_factor_of_three(self) -> None:
        """One recording density proves nothing about a points-based window.

        The detector smooths over ±10 POINTS. At the tight end of this
        corpus that is tens of metres of ground and at the loose end it is
        hundreds, which is the whole argument for respecifying the window
        in metres. If a future edit narrowed the corpus to one device, that
        argument would quietly lose its evidence — so the spread is asserted
        rather than assumed.
        """
        spacings = {
            filename: _mean_spacing(parse_gpx(raw).points)
            for filename, raw in canonical_documents()
        }

        assert min(spacings.values()) < 5.0
        assert max(spacings.values()) > 15.0
        assert max(spacings.values()) / min(spacings.values()) > 3.0

    def test_a_ten_point_window_covers_very_different_ground(self) -> None:
        """Stated in the unit the detector actually uses."""
        spacings = [
            _mean_spacing(parse_gpx(raw).points) for _, raw in canonical_documents()
        ]
        windows = [spacing * 20 for spacing in spacings]  # +/-10 points

        assert math.isclose(min(windows), 76, abs_tol=15)
        assert math.isclose(max(windows), 372, abs_tol=40)
