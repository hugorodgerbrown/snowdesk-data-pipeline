"""report_route_passages — what the no-fall gates would mark, at each setting.

SNOW-964. ``apps.routes.services.passages`` decides four numbers — the
gate a passage seeds at, the floor it grows through, the shortest one
reported, and how far off the fall line still counts as with it — and
those numbers were chosen from reasoning about the terrain rather than
from the routes this database actually holds. This command is the
instrument that checks the reasoning against the data, and it is what
should be re-run before any of the four is moved again.

**A TUNING INSTRUMENT, NOT A CHECK.** It reports a distribution; there is
no such thing as a wrong one, so it never exits non-zero. Compare that
with ``diagnose_region_coverage``, which finds a problem and says so.

**Pure SELECT** — no ``--commit`` flag at all, because there is nothing
to commit. Nothing here writes, and nothing here derives anything that is
stored: ``route_passages`` takes its four thresholds as keyword arguments,
so the sweeps below re-run the derivation at every candidate over a record
already in memory, with no further database access.

**Trips are excluded on purpose.** A ``Trip`` carries a verbatim snapshot
of a route's record (SNOW-962), so counting both would weight one route by
how many people are going on it.

Usage::

    # The three tables.
    uv run python manage.py report_route_passages

    # Plus a per-route block naming each passage at the shipped defaults.
    uv run python manage.py report_route_passages -v 2

    # A first batch, on a large table.
    uv run python manage.py report_route_passages --limit 50
"""

from __future__ import annotations

import logging
import statistics
from argparse import ArgumentParser
from dataclasses import dataclass, field
from typing import Any

from django.core.management.base import BaseCommand

from apps.core.command_iteration import iterate_rows
from apps.routes.models import Route
from apps.routes.services.passages import (
    CLIMBING,
    CROSSING,
    DESCENDING,
    FALL_LINE_TOLERANCE_DEG,
    NO_FALL_GATE_DEG,
    PASSAGE_GROW_FLOOR_DEG,
    PASSAGE_MIN_M,
    route_passages,
)

logger = logging.getLogger(__name__)

# The gates the first table sweeps, in degrees.
#
# Two bands either side of the shipped 50. The question it answers is the
# one that has to be settled before any of the rest matters: how much
# ground is even in reach of a mark, and does moving the gate one band
# change that by a little or by an order of magnitude.
_GATE_CANDIDATES = (40.0, 45.0, 50.0, 55.0)

# The grow floors the second table sweeps, in degrees.
#
# From "no growing at all" (the floor at the gate) down two bands. A
# floor's whole job is to stop one continuous face being reported as
# several passages with gaps, so what the table is really showing is how
# many passages collapse into one as it drops.
_FLOOR_CANDIDATES = (55.0, 50.0, 45.0, 40.0)

# The alignment tolerances the third table sweeps, in degrees.
#
# The table that most earns its place. At the shipped 30, *crossing*
# covers 120 degrees of the 180 available and would win two thirds of a
# uniform distribution by chance alone — so a crossing-heavy result is
# only evidence of anything when read against 20 and 40.
_TOLERANCE_CANDIDATES = (20.0, 30.0, 40.0)

# The minimum the second and third tables hold fixed, in metres.
#
# The shipped one. The minimum is the one of the four thresholds with no
# room to move: it is a single stride, which is the shortest passage the
# record can express at all.
_SWEEP_MIN_M = PASSAGE_MIN_M


@dataclass
class _GateStats:
    """What one candidate gate reaches, before growing or any minimum."""

    routes_touched: int = 0
    runs: int = 0
    metres: float = 0.0


@dataclass
class _PassageStats:
    """What one (gate, floor) pairing produces at the shipped minimum."""

    routes_marked: int = 0
    single_segment: int = 0
    lengths_m: list[float] = field(default_factory=list)


@dataclass
class _AlignmentStats:
    """How one tolerance splits the passages it is given."""

    descending: int = 0
    climbing: int = 0
    crossing: int = 0
    unclassified: int = 0


class Command(BaseCommand):
    """Report what the no-fall passage gates would mark, at each setting.

    Read-only by construction — no ``--commit``, no writes, and no
    non-zero exit: a distribution cannot be wrong.
    """

    help = (
        "Sweep the no-fall passage thresholds over every sampled Route and "
        "report what each setting would mark. Pure SELECT — writes nothing."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to register on.

        """
        # No --commit: the command is read-only by construction, not by
        # default. --verbosity is inherited from BaseCommand.
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help=(
                "Stop after this many routes. Defaults to 0, meaning every "
                "sampled route."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Walk the sampled routes and print the three sweep tables.

        Args:
            *args: Unused positional arguments.
            **options: Django's parsed options.

        """
        verbosity: int = options["verbosity"]
        limit: int = options["limit"]

        gates: dict[float, _GateStats] = {
            gate: _GateStats() for gate in _GATE_CANDIDATES
        }
        pairs: dict[tuple[float, float], _PassageStats] = {
            (gate, floor): _PassageStats()
            for gate in _GATE_CANDIDATES
            for floor in _FLOOR_CANDIDATES
            if floor <= gate
        }
        tolerances: dict[float, _AlignmentStats] = {
            tolerance: _AlignmentStats() for tolerance in _TOLERANCE_CANDIDATES
        }

        routes = 0
        # Only the two columns the sweeps read. A record is the large part
        # of the row and every route has to be deserialised anyway, but
        # the name and the full track are not wanted and would double what
        # crosses the wire on a long tour.
        queryset = Route.objects.exclude(slope_samples__isnull=True).values_list(
            "id", "uuid", "slope_samples"
        )
        for _pk, _uuid, record in iterate_rows(
            self,
            queryset,
            verbosity=verbosity,
            describe=lambda row: row[1],
        ):
            routes += 1
            _accumulate_gates(record, gates)
            _accumulate_pairs(record, pairs)
            _accumulate_tolerances(record, tolerances)
            if verbosity >= 2:
                self._print_route_detail(record)
            if limit and routes >= limit:
                break

        self.stdout.write("")
        if not routes:
            self.stdout.write(
                self.style.WARNING(
                    "No sampled routes in this database — nothing to tune "
                    "against. The shipped thresholds remain reasoned guesses."
                )
            )
            return

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"No-fall passages over {routes} sampled route(s)"
            )
        )
        self._print_gate_table(gates, routes)
        self._print_pair_table(pairs, routes)
        self._print_alignment_table(tolerances)

        logger.info("report_route_passages finished: routes=%d", routes)

    def _print_route_detail(self, record: dict[str, Any] | None) -> None:
        """Print one route's passages at the shipped defaults.

        Printed under the countdown line ``iterate_rows`` has already
        written, so the uuid above it is the route this belongs to.

        Args:
            record: The row's ``slope_samples``.

        """
        passages = route_passages(record)
        if not passages:
            self.stdout.write("    (no passages at the shipped thresholds)")
            return
        for passage in passages:
            self.stdout.write(
                f"    segments {passage['from']}–{passage['to']}  "
                f"{passage['m']:>7.1f} m  "
                f"{passage.get('fall_line', '(unclassified)')}"
            )

    def _print_gate_table(self, gates: dict[float, _GateStats], routes: int) -> None:
        """Print the gate sweep — how much ground each candidate reaches.

        No growing and no minimum, so this is the raw distribution of
        steep ground rather than of passages.

        Args:
            gates: The accumulated per-gate figures.
            routes: How many routes were walked.

        """
        self.stdout.write("")
        self.stdout.write("Gate sweep — segments at or above the gate, ungrown:")
        header = f"{'gate':>6}  {'routes':>7} {'(%)':>5}  {'runs':>6}  {'metres':>10}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for gate in _GATE_CANDIDATES:
            stats = gates[gate]
            marker = " *" if gate == NO_FALL_GATE_DEG else ""
            self.stdout.write(
                f"{gate:>5.0f}°  {stats.routes_touched:>7} "
                f"{_pct(stats.routes_touched, routes):>4}%  "
                f"{stats.runs:>6}  {stats.metres:>10.0f}{marker}"
            )

    def _print_pair_table(
        self, pairs: dict[tuple[float, float], _PassageStats], routes: int
    ) -> None:
        """Print the (gate, floor) sweep at the shipped minimum.

        The table the floor is chosen from: as it drops, passages merge,
        so the count falls while the median length rises.

        Args:
            pairs: The accumulated per-pairing figures.
            routes: How many routes were walked.

        """
        self.stdout.write("")
        self.stdout.write(f"Passage sweep — gate x floor at min_m={_SWEEP_MIN_M:.0f}:")
        header = (
            f"{'gate':>6} {'floor':>6}  {'count':>6}  {'routes':>7} {'(%)':>5}  "
            f"{'median m':>9}  {'p90 m':>8}  {'1-seg':>6}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for (gate, floor), stats in pairs.items():
            count = len(stats.lengths_m)
            marker = (
                " *"
                if gate == NO_FALL_GATE_DEG and floor == PASSAGE_GROW_FLOOR_DEG
                else ""
            )
            self.stdout.write(
                f"{gate:>5.0f}° {floor:>5.0f}°  {count:>6}  "
                f"{stats.routes_marked:>7} {_pct(stats.routes_marked, routes):>4}%  "
                f"{_median(stats.lengths_m):>9.0f}  {_p90(stats.lengths_m):>8.0f}  "
                f"{_pct(stats.single_segment, count):>5}%{marker}"
            )

    def _print_alignment_table(self, tolerances: dict[float, _AlignmentStats]) -> None:
        """Print how each candidate tolerance labels the same passages.

        Read against the geometry, not on its own: at a 30 degree
        tolerance ``crossing`` covers 120 of the 180 degrees available, so
        it wins two thirds of a uniform distribution by chance.

        Args:
            tolerances: The accumulated per-tolerance figures.

        """
        self.stdout.write("")
        self.stdout.write(
            "Alignment — the shipped gate and floor, labelled at each tolerance:"
        )
        header = (
            f"{'tol':>5}  {'descending':>11}  {'climbing':>9}  "
            f"{'crossing':>9}  {'unclassified':>13}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for tolerance in _TOLERANCE_CANDIDATES:
            stats = tolerances[tolerance]
            marker = " *" if tolerance == FALL_LINE_TOLERANCE_DEG else ""
            self.stdout.write(
                f"{tolerance:>4.0f}°  {stats.descending:>11}  {stats.climbing:>9}  "
                f"{stats.crossing:>9}  {stats.unclassified:>13}{marker}"
            )
        self.stdout.write("")
        self.stdout.write("* the shipped setting.")


def _accumulate_gates(
    record: dict[str, Any] | None, gates: dict[float, _GateStats]
) -> None:
    """Add one route's figures to the gate sweep.

    Each candidate is run with the floor AT the gate and no minimum, so
    what is counted is the ground itself rather than the passages a later
    pairing would make of it.

    Args:
        record: The row's ``slope_samples``.
        gates: The accumulator, mutated.

    """
    for gate, stats in gates.items():
        passages = route_passages(record, gate_deg=gate, floor_deg=gate, min_m=0.0)
        if not passages:
            continue
        stats.routes_touched += 1
        stats.runs += len(passages)
        stats.metres += sum(passage["m"] for passage in passages)


def _accumulate_pairs(
    record: dict[str, Any] | None,
    pairs: dict[tuple[float, float], _PassageStats],
) -> None:
    """Add one route's figures to the (gate, floor) sweep.

    Args:
        record: The row's ``slope_samples``.
        pairs: The accumulator, mutated.

    """
    for (gate, floor), stats in pairs.items():
        passages = route_passages(
            record, gate_deg=gate, floor_deg=floor, min_m=_SWEEP_MIN_M
        )
        if not passages:
            continue
        stats.routes_marked += 1
        for passage in passages:
            stats.lengths_m.append(float(passage["m"]))
            if passage["from"] == passage["to"]:
                stats.single_segment += 1


def _accumulate_tolerances(
    record: dict[str, Any] | None, tolerances: dict[float, _AlignmentStats]
) -> None:
    """Add one route's passages to the alignment distribution.

    The gate, floor and minimum are held at the shipped values throughout:
    the question is how the SAME passages are labelled, not how many there
    are.

    Args:
        record: The row's ``slope_samples``.
        tolerances: The accumulator, mutated.

    """
    for tolerance, stats in tolerances.items():
        passages = route_passages(record, tolerance_deg=tolerance) or []
        for passage in passages:
            label = passage.get("fall_line")
            if label == DESCENDING:
                stats.descending += 1
            elif label == CLIMBING:
                stats.climbing += 1
            elif label == CROSSING:
                stats.crossing += 1
            else:
                stats.unclassified += 1


def _median(values: list[float]) -> float:
    """Return the median of a list, or 0.0 when it is empty.

    Args:
        values: The lengths, in any order.

    Returns:
        The median, or 0.0 — a zero in an empty row of a distribution
        table is a formatting choice, not a claim about ground.

    """
    return statistics.median(values) if values else 0.0


def _p90(values: list[float]) -> float:
    """Return the 90th-percentile value, or 0.0 when the list is empty.

    Nearest-rank rather than an interpolated percentile: the point of the
    column is "how long do the long ones get", and a rank names a passage
    that actually exists.

    Args:
        values: The lengths, in any order.

    Returns:
        The 90th-percentile length, or 0.0 for an empty list.

    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.9 * len(ordered) + 0.5)) - 1)
    return ordered[index]


def _pct(numerator: int, denominator: int) -> int:
    """Return an integer percentage, or 0 when the denominator is 0.

    Args:
        numerator: The count to express as a percentage.
        denominator: The total.

    Returns:
        An integer percentage in 0–100.

    """
    if denominator == 0:
        return 0
    return round(100 * numerator / denominator)
