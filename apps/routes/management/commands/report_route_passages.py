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

**THE FOURTH TABLE IS THE ONE THAT MEASURES THE TOLERANCE.** The other
three count passages, which is the right unit for the gate, the floor
and the minimum — but ``FALL_LINE_TOLERANCE_DEG`` acts on a SEGMENT, and
the coverage vote sits between the two. A passage-level count therefore
gives ``crossing`` two routes to victory (the tolerance's residue and the
tie-break) and cannot say which produced any figure, which is exactly
what the first staging run showed: 0/0/10 at every one of 20, 30 and 40
degrees. So the fall-line sweep histograms the per-segment angles
themselves, and the alignment table says how many of its crossings a tie
chose (SNOW-971).

**Pure SELECT** — no ``--commit`` flag at all, because there is nothing
to commit. Nothing here writes, and nothing here derives anything that is
stored: ``route_passages`` takes its four thresholds as keyword arguments,
so the sweeps below re-run the derivation at every candidate over a record
already in memory, with no further database access.

**Trips are excluded on purpose.** A ``Trip`` carries a verbatim snapshot
of a route's record (SNOW-962), so counting both would weight one route by
how many people are going on it.

Usage::

    # The four tables.
    uv run python manage.py report_route_passages

    # Plus a per-route block naming each passage at the shipped defaults.
    uv run python manage.py report_route_passages -v 2

    # A first batch, on a large table.
    uv run python manage.py report_route_passages --limit 50
"""

from __future__ import annotations

import logging
import math
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
    fall_line_alignment,
    passage_alignment_detail,
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

# The width of one fall-line bucket, in degrees, and how many there are.
#
# Ten degrees, giving 18 buckets over the 0-180 the angle can take. The
# width is chosen from the tolerances the table above sweeps rather than
# for its own sake: 20, 30 and 40 — and their mirrors at 140, 150 and 160
# — all land on a bucket EDGE, so a reader can add up the rows either
# side of a candidate and see exactly what moving the tolerance to it
# would reclassify. A 15 degree bucket would straddle every one of them
# and answer nothing.
_BUCKET_WIDTH_DEG = 10.0
_BUCKET_COUNT = 18

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
    """How one tolerance splits the passages it is given.

    ``tied`` is a SUBSET of ``crossing`` and never a fifth category: a
    tie resolves to ``crossing``, so it is already counted there. It is
    reported because the two kinds of crossing say different things — one
    is the tolerance's residue and is evidence about the tolerance, the
    other is a passage genuinely split in half and is not.
    """

    descending: int = 0
    climbing: int = 0
    crossing: int = 0
    unclassified: int = 0
    tied: int = 0


@dataclass
class _SweepStats:
    """The per-segment fall-line angles inside the shipped passages.

    The unit is a SEGMENT, which is the unit the tolerance acts on — the
    whole reason this table exists beside the passage-level one above.
    """

    buckets: list[int] = field(default_factory=lambda: [0] * _BUCKET_COUNT)
    unmeasured: int = 0

    @property
    def total(self) -> int:
        """Return how many segments were seen, measurable or not.

        Returns:
            The denominator of the table's percentage column.

        """
        return sum(self.buckets) + self.unmeasured


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
        """Walk the sampled routes and print the four sweep tables.

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
        sweep = _SweepStats()

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
            _accumulate_sweep(record, sweep)
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
        self._print_sweep_table(sweep)
        self.stdout.write("")
        self.stdout.write("* the shipped setting.")

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
        it wins two thirds of a uniform distribution by chance. And read
        with the fall-line sweep below it, which is where the tolerance's
        own unit is counted: the ``of which tied`` column names the
        crossings this table cannot attribute to the tolerance at all.

        Args:
            tolerances: The accumulated per-tolerance figures.

        """
        self.stdout.write("")
        self.stdout.write(
            "Alignment — the shipped gate and floor, labelled at each tolerance:"
        )
        header = (
            f"{'tol':>5}  {'descending':>11}  {'climbing':>9}  "
            f"{'crossing':>9}  {'unclassified':>13}  {'of which tied':>14}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for tolerance in _TOLERANCE_CANDIDATES:
            stats = tolerances[tolerance]
            marker = " *" if tolerance == FALL_LINE_TOLERANCE_DEG else ""
            self.stdout.write(
                f"{tolerance:>4.0f}°  {stats.descending:>11}  {stats.climbing:>9}  "
                f"{stats.crossing:>9}  {stats.unclassified:>13}  "
                f"{stats.tied:>14}{marker}"
            )
        self.stdout.write(
            "  'of which tied' is a SUBSET of 'crossing', not a fifth column:"
        )
        self.stdout.write(
            "  a tie resolves to crossing, so those passages are in both."
        )

    def _print_sweep_table(self, sweep: _SweepStats) -> None:
        """Print the distribution of the per-segment fall-line angles.

        **THE ONLY TABLE COUNTED IN THE TOLERANCE'S OWN UNIT.** Every
        other one counts passages, and the coverage vote between a
        segment and a passage is what made the alignment table unable to
        say anything about ``FALL_LINE_TOLERANCE_DEG``. Here each segment
        inside a shipped-threshold passage contributes its own angle, so
        a candidate tolerance can be read straight off the rows: at the
        shipped 30 the first three rows are descents and the last three
        climbs, and moving to 20 or 40 moves one row across each end.

        Segments nothing could measure get their own line rather than
        being dropped, because a histogram that silently omits them
        overstates how much of the terrain it describes.

        Args:
            sweep: The accumulated per-segment figures.

        """
        self.stdout.write("")
        self.stdout.write(
            "Fall-line sweep — per-segment angle between track bearing and aspect,"
        )
        self.stdout.write(
            f"inside every passage at the shipped gate and floor "
            f"(min_m={_SWEEP_MIN_M:.0f}):"
        )
        label_column = f"at {FALL_LINE_TOLERANCE_DEG:.0f}°"
        header = f"{'bucket':>11}  {'count':>6} {'(%)':>5}  {label_column}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        total = sweep.total
        for index, count in enumerate(sweep.buckets):
            self.stdout.write(
                f"{_bucket_label(index):>11}  {count:>6} "
                f"{_pct(count, total):>4}%  {_bucket_verdict(index)}"
            )
        self.stdout.write(
            f"{'unmeasured':>11}  {sweep.unmeasured:>6} "
            f"{_pct(sweep.unmeasured, total):>4}%  (no vote)"
        )


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

    The tie count is read back from ``passage_alignment_detail`` because
    the label alone cannot carry it: a tie always resolves to
    ``crossing``, so the two kinds are indistinguishable on the wire — and
    that is deliberate, since a client has no use for the distinction.

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
            detail = passage_alignment_detail(
                record,
                passage["from"],
                passage["to"],
                tolerance_deg=tolerance,
            )
            if detail is not None and detail.resolved_by_tie:
                stats.tied += 1


def _accumulate_sweep(record: dict[str, Any] | None, sweep: _SweepStats) -> None:
    """Add one route's per-segment fall-line angles to the distribution.

    The angles come from ``passage_alignment_detail`` rather than from a
    second walk of the geometry, so the histogram is of the numbers the
    label was actually voted from and the two cannot drift.

    Args:
        record: The row's ``slope_samples``.
        sweep: The accumulator, mutated.

    """
    for passage in route_passages(record) or []:
        detail = passage_alignment_detail(record, passage["from"], passage["to"])
        if detail is None:
            continue
        for segment in detail.segments:
            if segment.delta_deg is None:
                sweep.unmeasured += 1
                continue
            sweep.buckets[_bucket_index(segment.delta_deg)] += 1


def _bucket_index(delta_deg: float) -> int:
    """Return which fall-line bucket one per-segment angle falls in.

    Args:
        delta_deg: The angle between track bearing and aspect, in
            ``[0, 180]``.

    Returns:
        An index into ``_SweepStats.buckets``. Exactly 180 degrees — a
        track straight up the fall line — is clamped into the last
        bucket rather than falling off the end of the table.

    """
    return min(int(delta_deg // _BUCKET_WIDTH_DEG), _BUCKET_COUNT - 1)


def _bucket_label(index: int) -> str:
    """Return one bucket's printed range.

    Args:
        index: The bucket's index.

    Returns:
        ``"20–30°"`` and so on, the half-open range the bucket holds.

    """
    low = index * _BUCKET_WIDTH_DEG
    return f"{low:.0f}–{low + _BUCKET_WIDTH_DEG:.0f}°"


def _bucket_verdict(index: int) -> str:
    """Return the label a bucket's segments carry at the shipped tolerance.

    Asked of ``fall_line_alignment`` itself rather than restated here, so
    the column cannot drift from the rule it is describing. The bucket's
    MIDPOINT is the angle asked about: the shipped tolerance is inclusive,
    so the single value 30.0 is a descent while the rest of the 30–40
    bucket is a crossing, and the midpoint is what the row is true of.

    Args:
        index: The bucket's index.

    Returns:
        One of the three labels.

    """
    midpoint = index * _BUCKET_WIDTH_DEG + _BUCKET_WIDTH_DEG / 2.0
    # A due-north bearing against an aspect that far round from it, which
    # makes the separation the midpoint itself.
    return fall_line_alignment(0.0, midpoint) or CROSSING


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

    ``ceil`` and not ``round(x + 0.5)``, which is the nearest-rank
    formula's usual disguise and is not the same function. Python rounds
    halves to even, so at any count where ``0.9 * n`` is an odd integer —
    10, 30, 50 — ``round(n' + 0.5)`` goes UP a rank and the column
    reports the longest passage as its 90th percentile. A tuning table
    that overstates its own tail is the one kind of error this command
    must not make, because the numbers it prints are chosen from.

    Args:
        values: The lengths, in any order.

    Returns:
        The 90th-percentile length, or 0.0 for an empty list.

    """
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.ceil(0.9 * len(ordered)) - 1]


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
