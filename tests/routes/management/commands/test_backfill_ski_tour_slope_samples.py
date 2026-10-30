"""
tests/routes/management/commands/test_backfill_ski_tour_slope_samples.py

Covers ``backfill_ski_tour_slope_samples`` (SNOW-1288):
  - Read-only by default: no request, no write.
  - ``--commit`` walks and keeps a tour's record.
  - The candidates are a tour never walked and one walked over SNOW-1286's
    doubled line; a tour with a current record is left alone.
  - A tour the terrain model cannot answer for stays null, not a failure.
  - One failing tour does not abort the batch; the command exits non-zero.
  - ``--limit`` stops where it says.
  - A record walked before SNOW-1306 on a tour whose order stands is only
    marked: no request, no pause.

``build_slope_samples`` is patched throughout: the walk is
``tests/routes/test_slope_segments.py``'s subject.
"""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.routes.models import SkiTour
from tests.factories import SkiTourFactory

pytestmark = pytest.mark.django_db

COMMAND = "backfill_ski_tour_slope_samples"
_BUILDER = "apps.routes.services.ski_tours.build_slope_samples"
RECORD: dict[str, Any] = {"segments": [], "heights": [2000.0]}
# What a walk keeps since SNOW-1306.
KEPT: dict[str, Any] = {**RECORD, "top_ordered": True}


def _run(*args: str) -> str:
    """Run the command with no pause and return its stdout.

    Args:
        args: Extra command-line arguments.

    Returns:
        What the command wrote to stdout.

    """
    out = StringIO()
    call_command(COMMAND, "--delay", "0", *args, stdout=out)
    return out.getvalue()


def test_a_read_only_run_asks_nothing_and_writes_nothing() -> None:
    """The default reports the count."""
    tour = SkiTourFactory.create()
    with patch(_BUILDER) as build:
        out = _run()
    build.assert_not_called()
    assert "1 SAC tour(s) [READ-ONLY]" in out
    tour.refresh_from_db()
    assert tour.slope_samples is None


def test_commit_walks_and_keeps_the_record() -> None:
    """Each line names the tour by its SAC id, summit and name."""
    tour = SkiTourFactory.create(name="Von der Hütte")
    with patch(_BUILDER, return_value=dict(RECORD)):
        out = _run("--commit")
    assert f"SAC {tour.sac_id} Piz Buin · Von der Hütte" in out
    tour.refresh_from_db()
    assert tour.slope_samples == KEPT


def test_only_tours_without_a_current_record_are_walked() -> None:
    """A doubled record is walked again; a current one is not."""
    SkiTourFactory.create(slope_samples=KEPT)
    doubled = SkiTourFactory.create(slope_samples={**RECORD, "there_and_back": True})
    with patch(_BUILDER, return_value=dict(RECORD)) as build:
        _run("--commit")
    build.assert_called_once()
    doubled.refresh_from_db()
    assert doubled.slope_samples == KEPT


def test_a_record_walked_before_the_order_rule_is_marked_not_walked() -> None:
    """SNOW-1306: no request and no pause for a tour whose order stands."""
    tour = SkiTourFactory.create(slope_samples=RECORD)
    with patch(_BUILDER) as build, patch("time.sleep") as sleep:
        out = call_command_out = StringIO()
        call_command(COMMAND, "--commit", stdout=call_command_out)
    build.assert_not_called()
    sleep.assert_not_called()
    assert "1 marked" in out.getvalue()
    tour.refresh_from_db()
    assert tour.slope_samples == KEPT


def test_a_tour_with_no_answer_stays_null_and_is_no_failure() -> None:
    """A later run tries it again."""
    tour = SkiTourFactory.create()
    with patch(_BUILDER, return_value=None):
        out = _run("--commit")
    assert "1 left null" in out
    tour.refresh_from_db()
    assert tour.slope_samples is None


def test_one_failure_does_not_stop_the_batch_and_exits_non_zero() -> None:
    """The other tour is still walked."""
    SkiTourFactory.create_batch(2)
    with (
        patch(_BUILDER, side_effect=[RuntimeError("boom"), dict(RECORD)]),
        pytest.raises(CommandError, match="1 failure"),
    ):
        _run("--commit")
    assert SkiTour.objects.filter(slope_samples__isnull=False).count() == 1


def test_limit_stops_where_it_says() -> None:
    """Two of three."""
    SkiTourFactory.create_batch(3)
    with patch(_BUILDER, return_value=dict(RECORD)) as build:
        _run("--commit", "--limit", "2")
    assert build.call_count == 2
