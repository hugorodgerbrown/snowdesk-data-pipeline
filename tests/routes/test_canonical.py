"""
tests/routes/test_canonical.py — the committed corpus of real tracks (SNOW-989).

Two jobs. The first is that the files are there and parse: a corpus nothing
checks is a corpus that quietly rots. The second is the property the corpus
exists FOR — a spread of point spacings wide enough to show that a window
specified in points is not a window specified in metres.

``TestTheRecordings`` covers the second, smaller corpus under
``fixtures/recordings/``, whose contract is the inverse: timed, over
``MAX_POINTS``, and therefore stored simplified. Its assertions are written
as the mirror of the canonical ones on purpose — the two sets must not
drift into each other, because the spacing argument above depends on the
canonical four being unsimplified.
"""

from __future__ import annotations

import math

import pytest
from defusedxml.ElementTree import fromstring

from apps.core.geo import haversine_m
from apps.routes.services.canonical import (
    CANONICAL_DIR,
    RECORDING_DIR,
    canonical_documents,
    canonical_paths,
    recording_documents,
    recording_paths,
)
from apps.routes.services.gpx import (
    MAX_POINTS,
    _find_all,
    _point_from_element,
    _point_time,
    parse_gpx,
)

# What each file is expected to hold. Spelled out rather than derived so a
# track that is replaced or re-exported fails here loudly, instead of the
# suite silently re-baselining itself onto whatever was committed.
EXPECTED = {
    "chamonix-col-de-balme.gpx": 1134,
    "hidden-valley.gpx": 524,
    "mont-fort-backside.gpx": 694,
    "mont-fort-col-de-la-chaux.gpx": 894,
}

# The raw device exports, as ``{filename: source point count}`` — what the
# watch recorded, NOT what we store. Spelled out for the same reason as
# above, and the count is the source one because that is the figure a
# re-export or a re-redaction would change.
EXPECTED_RECORDINGS = {
    "verbier-lift-served-day.gpx": 7217,
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


class TestTheRecordings:
    """The raw device exports — the opposite contract to the corpus above.

    These exist for what a watch actually emits, which is the one thing a
    reconstitution cannot show: per-point timestamps, and therefore the
    recording gaps only an interval makes visible.
    """

    def test_the_directory_is_checked_out(self) -> None:
        """A missing fixtures directory is a broken checkout, not zero files."""
        assert RECORDING_DIR.is_dir()

    def test_holds_exactly_the_expected_files(self) -> None:
        """Adding a recording is a deliberate edit to this test too."""
        assert {path.name for path in recording_paths()} == set(EXPECTED_RECORDINGS)

    def test_no_recording_leaks_into_the_canonical_corpus(self) -> None:
        """The separation is the point, so it is asserted rather than assumed.

        A recording is timed and simplified. Both are properties
        ``TestEveryTrackParses`` pins the canonical four as NOT having, and
        the spacing-spread argument depends on their absence. If the two
        sets ever met, those assertions would start describing a corpus
        that no longer supports the argument it was committed to make.
        """
        assert not {path.name for path in canonical_paths()} & set(EXPECTED_RECORDINGS)

    @pytest.mark.parametrize("filename", sorted(EXPECTED_RECORDINGS))
    def test_is_timed_on_both_ends(self, filename: str) -> None:
        """The property the canonical corpus cannot supply."""
        parsed = parse_gpx(dict(recording_documents())[filename])

        assert parsed.started_at is not None
        assert parsed.finished_at is not None

    @pytest.mark.parametrize("filename", sorted(EXPECTED_RECORDINGS))
    def test_is_over_max_points_and_is_simplified(self, filename: str) -> None:
        """The deliberate inverse of ``test_is_stored_whole``.

        A recording is committed at the density the device chose, which is
        finer than anything we store, so the stored track IS a simplified
        remnant. That is why it is kept out of the spacing spread.
        """
        parsed = parse_gpx(dict(recording_documents())[filename])

        assert parsed.source_point_count == EXPECTED_RECORDINGS[filename]
        assert parsed.source_point_count > MAX_POINTS
        assert parsed.point_count < parsed.source_point_count

    @pytest.mark.parametrize("filename", sorted(EXPECTED_RECORDINGS))
    def test_carries_no_personal_extensions(self, filename: str) -> None:
        """No heart rate, no author — this repository is public.

        A watch export carries both by default, so a recording added or
        re-committed later without the same removals would publish health
        data and a name. Asserted on the committed bytes rather than
        trusted to whoever adds the next file.
        """
        raw = dict(recording_documents())[filename].decode("utf-8")
        body = raw.split("</metadata>", 1)[1]

        assert "gpxtpx" not in raw
        assert "<extensions>" not in body
        assert "<author>" not in raw

    def test_the_verbier_day_holds_a_recording_gap(self) -> None:
        """The case SNOW-991 turns on, pinned so a re-commit cannot lose it.

        The device stops logging on a lift and resumes at the top, leaving
        two consecutive points far apart in space and time with nothing in
        the file marking it. Measured on the full-resolution series, since
        the gap is between source points rather than stored ones.

        Keyed on the JUMP, not the interval. The longest interval in this
        file is a 16.5-minute stationary pause that moves 3 m — a lunch
        stop, not a gap — so ranking by time finds the wrong leg. It is the
        pairing of a long interval with a long jump that means the recorder
        was off while the skier moved, which is the whole distinction.
        """
        raw = dict(recording_documents())["verbier-lift-served-day.gpx"]
        root = fromstring(raw)
        elements = _find_all(root, "trkpt")
        points = [_point_from_element(element) for element in elements]
        times = [_point_time(element) for element in elements]

        # Built as a loop rather than a comprehension so each ordinate is
        # narrowed explicitly, the same way ``_mean_spacing`` does it above.
        # A watch export carries all three on every point, so a missing one
        # here is a corrupted fixture and should fail loudly.
        legs: list[tuple[float, float, float]] = []
        for index in range(1, len(points)):
            previous, current = points[index - 1], points[index]
            started, ended = times[index - 1], times[index]
            assert started is not None and ended is not None
            assert previous[2] is not None and current[2] is not None
            legs.append(
                (
                    haversine_m(previous[1], previous[0], current[1], current[0]),
                    (ended - started).total_seconds(),
                    current[2] - previous[2],
                )
            )
        jump_m, interval_s, climb_m = max(legs)

        assert jump_m == pytest.approx(2815, abs=5)
        assert interval_s == pytest.approx(403, abs=1)
        assert climb_m == pytest.approx(643.6, abs=0.5)

        # And the point the gate turns on: no other leg comes near it, so a
        # threshold between the two has a wide margin rather than a fine one.
        assert sorted(jump for jump, _, _ in legs)[-2] < 600
