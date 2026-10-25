"""
tests/routes/management/commands/test_set_example_route.py

Covers ``set_example_route`` (SNOW-1230):
  - The bare invocation reports and writes nothing.
  - ``--commit`` imports the canonical track for the example owner and
    marks a share of it on the given day.
  - A second ``--commit`` changes nothing and says so.
  - An unknown track exits non-zero.
  - The run lands in the CommandRun register with its tallies.

``build_slope_samples`` is patched on the upload worker's path, so no test
depends on what the (unreachable) terrain origin answers.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import CommandRun
from apps.routes.models import Route, RouteShare
from apps.routes.services.example import (
    DEFAULT_EXAMPLE_DATE,
    DEFAULT_EXAMPLE_FILENAME,
    EXAMPLE_OWNER_EMAIL,
    example_share,
)

COMMAND = "set_example_route"
_WORKER_BUILDER = "apps.routes.services.slope_segments.build_slope_samples"

RECORD: dict[str, Any] = {
    "window_m": 10.0,
    "stride_m": 25.0,
    "grid": "snowdesk-terrain-5m-3035",
    "points": [[7.4, 46.1], [7.41, 46.11]],
    "segments": [{"angle_deg": 34.2, "aspect_deg": 105.3}],
}


def _run(*args: str) -> str:
    """Run the command and return its stdout.

    Args:
        args: Extra command-line arguments.

    Returns:
        Everything the command wrote to stdout.

    """
    out = StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def builder() -> Iterator[MagicMock]:
    """Patch the terrain sampler to return ``RECORD``.

    Yields:
        The patched builder.

    """
    with patch(_WORKER_BUILDER, return_value=RECORD) as patched:
        yield patched


@pytest.mark.django_db
class TestSetExampleRoute:
    """The only writer of the example route."""

    def test_the_bare_run_writes_nothing(self, builder: MagicMock) -> None:
        """Read-only by default."""
        out = _run()

        assert "No example route is set." in out
        assert "READ-ONLY" in out
        assert not RouteShare.objects.exists()
        assert not Route.objects.exists()

    def test_commit_sets_the_default_example(self, builder: MagicMock) -> None:
        """The canonical track, owned by the example account, on its day."""
        out = _run("--commit")

        share = example_share()
        assert share is not None
        assert share.route is not None
        assert share.route.source_filename == DEFAULT_EXAMPLE_FILENAME
        assert share.route.user.email == EXAMPLE_OWNER_EMAIL
        assert share.example_date == DEFAULT_EXAMPLE_DATE
        assert "Example set" in out

    def test_a_second_commit_changes_nothing(self, builder: MagicMock) -> None:
        """Idempotent: one route, one share."""
        _run("--commit")

        out = _run("--commit")

        assert "Example route up to date." in out
        assert Route.objects.count() == 1
        assert RouteShare.objects.count() == 1

    def test_a_new_date_reuses_the_route(self, builder: MagicMock) -> None:
        """The track is imported once; the day moves."""
        _run("--commit")

        _run("--date", "2026-03-02", "--commit")

        share = example_share()
        assert share is not None
        assert share.example_date == date(2026, 3, 2)
        assert Route.objects.count() == 1

    def test_an_unknown_track_exits_non_zero(self, builder: MagicMock) -> None:
        """A typo is a failure, not a no-op."""
        with pytest.raises(CommandError, match="not a canonical track"):
            _run("--track", "nowhere.gpx")

    def test_the_run_is_registered_with_its_tallies(self, builder: MagicMock) -> None:
        """Every run lands in the CommandRun register."""
        _run("--commit")

        run = CommandRun.objects.filter(command=COMMAND).latest("started_at")
        assert run.counts["set"] == 1
        assert run.counts["routes_created"] == 1
