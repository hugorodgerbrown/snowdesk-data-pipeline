"""
tests/routes/management/commands/test_backfill_route_slope_samples.py

Covers ``backfill_route_slope_samples`` (SNOW-910):
  - Dry-run makes NO REQUEST and writes nothing — the read-only default
    this command's rules require, and stricter than "no write", because the
    work here is outbound traffic rather than a UPDATE.
  - ``--commit`` samples and stores, on the one column.
  - A sampled route is not a candidate, so a second run is a no-op.
  - A route the sampler could not answer for is left NULL rather than
    written as a record of nothing, so a later run retries it — and that
    is not counted as a failure.
  - One failing route does not abort the batch; the command exits non-zero.
  - ``--limit`` stops where it says it will.

``build_slope_samples`` is patched throughout: the walk and the record are
``tests/routes/test_slope_segments.py``'s subject, and what this file
asserts is the command around it.
"""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import RouteFactory

COMMAND = "backfill_route_slope_samples"
_BUILDER = (
    "apps.routes.management.commands.backfill_route_slope_samples.build_slope_samples"
)

# A record of the shape build_slope_samples returns: one segment, bounded
# by the two coordinates the module's N + 1 rule requires.
RECORD: dict[str, Any] = {
    "window_m": 10.0,
    "stride_m": 25.0,
    "grid": "snowdesk-terrain-5m-3035",
    "points": [[7.4, 46.1], [7.41, 46.11]],
    "segments": [{"angle_deg": 34.2, "aspect_deg": 105.3}],
}


def _run(*args: str) -> str:
    """Run the command with no delay and return its stdout.

    ``--delay 0`` on every call: the default paces itself a second per
    route against the tile origin, and a test that waited for that would
    be measuring ``time.sleep``.

    Args:
        args: Extra command-line arguments.

    Returns:
        Everything the command wrote to stdout.

    """
    out = StringIO()
    call_command(COMMAND, "--delay", "0", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestReadOnlyByDefault:
    """The bare invocation reports and does nothing else."""

    def test_it_makes_no_request_at_all(self) -> None:
        """Not merely no write: a preview must not spend the origin's bandwidth."""
        RouteFactory.create()

        with patch(_BUILDER) as builder:
            output = _run()

        builder.assert_not_called()
        assert "[READ-ONLY]" in output

    def test_it_writes_nothing(self) -> None:
        """The candidate is still a candidate afterwards."""
        route = RouteFactory.create()

        with patch(_BUILDER, return_value=RECORD):
            _run()

        route.refresh_from_db()
        assert route.slope_samples is None

    def test_it_counts_the_candidates(self) -> None:
        """Two unsampled routes are two the operator would be committing to."""
        RouteFactory.create()
        RouteFactory.create()

        with patch(_BUILDER):
            output = _run()

        assert "2 route(s)" in output


@pytest.mark.django_db
class TestCommit:
    """--commit samples the terrain and stores the answer."""

    def test_it_stores_the_record(self) -> None:
        """The row ends up carrying exactly what the sampler returned."""
        route = RouteFactory.create()

        with patch(_BUILDER, return_value=RECORD):
            _run("--commit")

        route.refresh_from_db()
        assert route.slope_samples == RECORD

    def test_an_already_sampled_route_is_not_a_candidate(self) -> None:
        """Idempotent: a second run has nothing to do."""
        RouteFactory.create(slope_samples=RECORD)

        with patch(_BUILDER) as builder:
            output = _run("--commit")

        builder.assert_not_called()
        assert "0 route(s)" in output

    def test_a_route_the_sampler_could_not_answer_for_stays_null(self) -> None:
        """Null keeps meaning NEVER SAMPLED, so a later run picks it up again."""
        route = RouteFactory.create()

        with patch(_BUILDER, return_value=None):
            output = _run("--commit")

        route.refresh_from_db()
        assert route.slope_samples is None
        assert "1 left null" in output

    def test_an_unanswered_route_is_not_a_failure(self) -> None:
        """An unreachable origin is retryable, not a partial run to alert on."""
        RouteFactory.create()

        with patch(_BUILDER, return_value=None):
            # No CommandError: the run completed, it just learned nothing.
            _run("--commit")

    def test_limit_stops_where_it_says(self) -> None:
        """The first small batch an operator watches before the whole table."""
        for _ in range(3):
            RouteFactory.create()

        with patch(_BUILDER, return_value=RECORD) as builder:
            _run("--commit", "--limit", "2")

        assert builder.call_count == 2


@pytest.mark.django_db
class TestFailures:
    """One bad route must not take the batch with it."""

    def test_one_failure_does_not_abort_the_others(self) -> None:
        """The surviving routes are sampled; the command still exits non-zero."""
        first = RouteFactory.create()
        second = RouteFactory.create()

        def _explode_once(points: Any, label: str) -> dict[str, Any]:
            """Fail for the first route processed, succeed for the rest.

            Keyed on the LABEL, which carries the pk — ``points`` alone
            cannot tell two routes apart, and the label is what the walk
            is given to name a track in its own log lines.
            """
            if label == f"route pk={second.pk}":
                raise RuntimeError("tile origin misbehaved")
            return RECORD

        with patch(_BUILDER, side_effect=_explode_once):
            with pytest.raises(CommandError, match="1 failure"):
                _run("--commit")

        first.refresh_from_db()
        second.refresh_from_db()
        # ``-id`` ordering means the second row is processed first, so the
        # one that survived is the one created earlier.
        assert first.slope_samples == RECORD
        assert second.slope_samples is None
