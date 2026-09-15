"""
tests/routes/management/commands/test_report_route_passages.py

Covers ``report_route_passages`` (SNOW-964):
  - It WRITES NOTHING. The record is compared byte for byte afterwards,
    because the whole argument for deriving passages at read time is that
    no threshold ever reaches the stored row — a command that quietly
    wrote one back would make every future re-tune a backfill again.
  - It runs with no arguments and exits zero on every distribution,
    including an empty database. This is a tuning instrument, not a
    check: there is no wrong answer for it to fail on.
  - The four tables appear, and the sweeps genuinely vary — the gate
    sweep reaches ground the shipped gate does not, which is the only
    evidence that the thresholds are arguments rather than constants
    baked into the walk.
  - Trips are not counted, since a trip is a verbatim snapshot of a
    route's record and would weight one route by its party size.
  - The fall-line sweep counts SEGMENTS, which is the unit the tolerance
    acts on — the alignment table counts passages, with the coverage vote
    in between, and so cannot say whether a ``crossing`` came from the
    tolerance or from the tie-break (SNOW-971). ``_alignment_rows`` below
    parses the tie column: a parser left at the old column count would
    match no rows at all and pass every assertion vacuously, which is how
    a table can break in silence.

The grouping itself is ``tests/routes/test_passages.py``'s subject; what
is asserted here is the command around it.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command

from apps.routes.management.commands.report_route_passages import _p90
from apps.routes.models import Route
from tests.factories import RouteFactory, TripFactory

COMMAND = "report_route_passages"

# A record with one two-segment stretch of 50+ ground in the middle of an
# otherwise moderate track, and a 40-degree stretch the shipped gate does
# not reach. The second is what makes the gate sweep's rows differ.
RECORD: dict[str, Any] = {
    "window_m": 10.0,
    "stride_m": 25.0,
    "grid": "snowdesk-terrain-5m-3035",
    "points": [
        [7.40, 46.10],
        [7.40, 46.10025],
        [7.40, 46.10050],
        [7.40, 46.10075],
        [7.40, 46.10100],
        [7.40, 46.10125],
    ],
    "segments": [
        {"angle_deg": 22.0, "aspect_deg": 0.0},
        {"angle_deg": 52.0, "aspect_deg": 0.0},
        {"angle_deg": 51.0, "aspect_deg": 10.0},
        {"angle_deg": 20.0, "aspect_deg": 0.0},
        {"angle_deg": 41.0, "aspect_deg": 180.0},
    ],
}


# A single five-segment passage whose aspects put one segment in each of
# four widely separated buckets, plus one segment with no aspect at all.
# The track runs due north, so every bearing is 0 and the aspect IS the
# per-segment angle from the fall line.
BUCKET_RECORD: dict[str, Any] = {
    "stride_m": 25.0,
    "points": [
        [7.40, 46.10],
        [7.40, 46.10025],
        [7.40, 46.10050],
        [7.40, 46.10075],
        [7.40, 46.10100],
        [7.40, 46.10125],
    ],
    "segments": [
        {"angle_deg": 52.0, "aspect_deg": 5.0},
        {"angle_deg": 52.0, "aspect_deg": 35.0},
        {"angle_deg": 52.0, "aspect_deg": 95.0},
        {"angle_deg": 52.0, "aspect_deg": 175.0},
        {"angle_deg": 52.0, "aspect_deg": None},
    ],
}

# A passage of exactly two full-stride segments, one descending and one
# climbing: equal coverage each way, so its label is the tie-break's and
# not the tolerance's. The two tying segments are deliberately NOT the
# track's last one, which takes its chord and would break the tie.
TIE_RECORD: dict[str, Any] = {
    "stride_m": 25.0,
    "points": [
        [7.40, 46.10],
        [7.40, 46.10025],
        [7.40, 46.10050],
        [7.40, 46.10075],
        [7.40, 46.10100],
    ],
    "segments": [
        {"angle_deg": 10.0, "aspect_deg": None},
        {"angle_deg": 52.0, "aspect_deg": 0.0},
        {"angle_deg": 52.0, "aspect_deg": 180.0},
        {"angle_deg": 10.0, "aspect_deg": None},
    ],
}


def _run(*args: str) -> str:
    """Run the command and return its stdout.

    Args:
        *args: Extra command-line arguments.

    Returns:
        Everything the command wrote to stdout.

    """
    out = StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestReportRoutePassages:
    """The command's behaviour over a small database."""

    def test_runs_with_no_arguments(self) -> None:
        """The bare invocation prints all four tables."""
        RouteFactory.create(slope_samples=RECORD)
        output = _run()
        assert "Gate sweep" in output
        assert "Passage sweep" in output
        assert "Alignment" in output
        assert "Fall-line sweep" in output

    def test_writes_nothing(self) -> None:
        """The stored record is byte-identical afterwards.

        The load-bearing assertion of the whole ticket: passages are
        derived on every read precisely so that no threshold is ever
        frozen into a row.
        """
        route = RouteFactory.create(slope_samples=RECORD)
        before = json.dumps(
            Route.objects.get(pk=route.pk).slope_samples, sort_keys=True
        )
        _run()
        after = json.dumps(Route.objects.get(pk=route.pk).slope_samples, sort_keys=True)
        assert after == before

    def test_an_empty_database_says_so_and_exits_zero(self) -> None:
        """Nothing to tune against is a result, not a failure."""
        output = _run()
        assert "No sampled routes" in output

    def test_an_unsampled_route_is_not_a_candidate(self) -> None:
        """A route nothing has walked has no record to sweep."""
        RouteFactory.create()
        assert "No sampled routes" in _run()

    def test_a_trip_is_not_counted(self) -> None:
        """A trip's snapshot is the same record and would double-count it."""
        RouteFactory.create(slope_samples=RECORD)
        TripFactory.create(slope_samples=RECORD)
        assert "over 1 sampled route(s)" in _run()

    def test_the_gate_sweep_reaches_ground_the_shipped_gate_does_not(self) -> None:
        """The 40-degree row counts a stretch the 50-degree row cannot.

        The evidence that the sweep really re-runs the derivation at each
        candidate, rather than reporting one answer four times.
        """
        RouteFactory.create(slope_samples=RECORD)
        rows = _gate_rows(_run())
        assert rows["40"] > rows["50"] > 0

    def test_the_alignment_table_labels_the_shipped_passages(self) -> None:
        """A descent of the fall line is counted as one."""
        RouteFactory.create(slope_samples=RECORD)
        rows = _alignment_rows(_run())
        # The record's passage runs due north over north-facing ground,
        # which is straight down the fall line — so it is a descent at
        # every one of the three tolerances.
        assert sorted(rows) == ["20", "30", "40"]
        assert all(counts[0] == 1 for counts in rows.values())
        # A clear winner, so no tie anywhere: the column is a subset of
        # crossing and crossing is empty here.
        assert all(counts[4] == 0 for counts in rows.values())

    def test_the_fall_line_sweep_buckets_each_segment_by_its_own_angle(
        self,
    ) -> None:
        """Four known aspects land in four separate 10 degree buckets.

        The table the tolerance is actually tuned from: the alignment
        table above would report this whole record as one ``crossing``,
        which says nothing about where the segments sit relative to a
        candidate tolerance.
        """
        RouteFactory.create(slope_samples=BUCKET_RECORD)
        rows = _sweep_rows(_run())
        assert rows["0-10"] == 1
        assert rows["30-40"] == 1
        assert rows["90-100"] == 1
        assert rows["170-180"] == 1
        assert sum(rows.values()) == 5

    def test_an_unmeasurable_segment_is_counted_rather_than_dropped(
        self,
    ) -> None:
        """Level ground faces nowhere, and the table says how much of it there was.

        A histogram that silently omitted them would overstate how much of
        the terrain it describes.
        """
        RouteFactory.create(slope_samples=BUCKET_RECORD)
        assert _sweep_rows(_run())["unmeasured"] == 1

    def test_the_tie_column_counts_a_passage_the_tie_break_decided(self) -> None:
        """One segment each way is a ``crossing`` the tolerance did not produce.

        Without this column the row is indistinguishable from a passage
        that genuinely runs across the fall line, and the tolerance sweep
        is uninterpretable — which is what the first staging run showed.
        """
        RouteFactory.create(slope_samples=TIE_RECORD)
        rows = _alignment_rows(_run())
        assert all(counts[2] == 1 for counts in rows.values())
        assert all(counts[4] == 1 for counts in rows.values())

    def test_limit_stops_where_it_says(self) -> None:
        """``--limit 1`` walks one route out of two."""
        RouteFactory.create(slope_samples=RECORD)
        RouteFactory.create(slope_samples=RECORD)
        assert "over 1 sampled route(s)" in _run("--limit", "1")
        assert "over 2 sampled route(s)" in _run()

    def test_verbose_names_each_passage(self) -> None:
        """At ``-v 2`` each route's passages are listed under its uuid."""
        RouteFactory.create(slope_samples=RECORD)
        output = _run("--verbosity", "2")
        assert "segments 1–2" in output
        assert "descending" in output

    def test_verbose_says_so_when_a_route_has_none(self) -> None:
        """A route with no passages still prints a line, not a silent gap."""
        RouteFactory.create(
            slope_samples={
                "stride_m": 25.0,
                "points": [[7.4, 46.1], [7.4, 46.10025]],
                "segments": [{"angle_deg": 12.0, "aspect_deg": 0.0}],
            }
        )
        assert "(no passages at the shipped thresholds)" in _run("--verbosity", "2")


def _gate_rows(output: str) -> dict[str, int]:
    """Return the gate sweep's metre figure, keyed by gate.

    Args:
        output: The command's stdout.

    Returns:
        ``{"40": 125, "45": 50, …}`` — the last column of each gate row.

    """
    table = output.split("Gate sweep")[1].split("Passage sweep")[0]
    rows: dict[str, int] = {}
    for line in table.splitlines():
        parts = line.replace("°", "").replace("%", "").replace("*", "").split()
        if len(parts) == 5 and parts[0].isdigit():
            rows[parts[0]] = int(parts[4])
    return rows


def _alignment_rows(output: str) -> dict[str, tuple[int, int, int, int, int]]:
    """Return the alignment table's five counts, keyed by tolerance.

    **THE COLUMN COUNT IS LOAD-BEARING.** This parser matches a row by its
    width, so leaving it at the four-column shape when SNOW-971 added the
    tie column would have matched nothing and passed every assertion
    above on an empty dict — a table that broke in silence.

    Args:
        output: The command's stdout.

    Returns:
        ``{"30": (descending, climbing, crossing, unclassified, tied), …}``.

    """
    table = output.split("Alignment")[1]
    rows: dict[str, tuple[int, int, int, int, int]] = {}
    for line in table.splitlines():
        parts = line.replace("°", "").replace("*", "").split()
        if len(parts) == 6 and parts[0].isdigit():
            counts = [int(value) for value in parts[1:6]]
            rows[parts[0]] = (
                counts[0],
                counts[1],
                counts[2],
                counts[3],
                counts[4],
            )
    return rows


def _sweep_rows(output: str) -> dict[str, int]:
    """Return the fall-line sweep's count column, keyed by bucket.

    Args:
        output: The command's stdout.

    Returns:
        ``{"0-10": 1, …, "unmeasured": 1}`` — the en dash of the printed
        range is normalised to a hyphen so a test can write the key.

    """
    table = output.split("Fall-line sweep")[1]
    rows: dict[str, int] = {}
    for line in table.splitlines():
        parts = line.replace("°", "").replace("%", "").replace("–", "-").split()
        if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
            rows[parts[0]] = int(parts[1])
    return rows


class TestPercentile:
    """The tuning table's own arithmetic.

    Unit-level rather than through ``call_command``, because a percentile
    is wrong only at particular counts and building a database of ten
    passages to reach one of them would test the fixture, not the
    formula.
    """

    def test_the_p90_is_nearest_rank_and_does_not_round_up_a_place(self) -> None:
        """Ten lengths report the ninth, not the tenth.

        ``round(0.9 * n + 0.5)`` is the nearest-rank formula's usual
        disguise and is a different function: Python rounds halves to
        even, so wherever ``0.9 * n`` is an odd integer — 10, 30, 50 —
        it goes up a rank and the column reports the longest passage as
        its 90th percentile. A tuning table that overstates its own tail
        is read by someone choosing a threshold from it.
        """
        assert _p90([float(n) for n in range(1, 11)]) == 9.0
        assert _p90([float(n) for n in range(1, 31)]) == 27.0

    def test_the_p90_names_a_length_that_exists(self) -> None:
        """Nearest-rank, so never an interpolated value between two."""
        lengths = [25.0, 50.0, 400.0]
        assert _p90(lengths) in lengths

    def test_the_p90_of_nothing_is_zero(self) -> None:
        """An empty sweep cell reports 0.0 rather than raising."""
        assert _p90([]) == 0.0
