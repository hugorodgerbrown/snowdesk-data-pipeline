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
  - The three tables appear, and the sweeps genuinely vary — the gate
    sweep reaches ground the shipped gate does not, which is the only
    evidence that the thresholds are arguments rather than constants
    baked into the walk.
  - Trips are not counted, since a trip is a verbatim snapshot of a
    route's record and would weight one route by its party size.

The grouping itself is ``tests/routes/test_passages.py``'s subject; what
is asserted here is the command around it.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command

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
        """The bare invocation prints all three tables."""
        RouteFactory.create(slope_samples=RECORD)
        output = _run()
        assert "Gate sweep" in output
        assert "Passage sweep" in output
        assert "Alignment" in output

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


def _alignment_rows(output: str) -> dict[str, tuple[int, int, int, int]]:
    """Return the alignment table's four counts, keyed by tolerance.

    Args:
        output: The command's stdout.

    Returns:
        ``{"30": (descending, climbing, crossing, unclassified), …}``.

    """
    table = output.split("Alignment")[1]
    rows: dict[str, tuple[int, int, int, int]] = {}
    for line in table.splitlines():
        parts = line.replace("°", "").replace("*", "").split()
        if len(parts) == 5 and parts[0].isdigit():
            counts = [int(value) for value in parts[1:5]]
            rows[parts[0]] = (counts[0], counts[1], counts[2], counts[3])
    return rows
