"""
tests/routes/management/commands/test_seed_canonical_routes.py

Covers ``seed_canonical_routes`` (SNOW-1023):
  - The bare invocation writes nothing and asks the terrain origin nothing.
  - ``--commit`` creates the four canonical routes for the dev user.
  - A second ``--commit`` prints exactly ``UP_TO_DATE`` and creates nothing
    — the sentence ``bin/init-worktree`` greps for on every session.
  - A canonical route the user already holds is not duplicated.
  - A held canonical route with null ``slope_samples`` is sampled.
  - With no dev user the command exits 0 and writes nothing.
  - A sampling error makes the command exit non-zero.

``build_slope_samples`` is patched in both the command's module and the
upload worker's, so no test depends on what the (unreachable) terrain
origin answers.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.bulletins.management.commands.seed_test_data import NORMAL_USER_EMAIL
from apps.routes.management.commands.seed_canonical_routes import UP_TO_DATE
from apps.routes.models import Route
from apps.routes.services.canonical import canonical_documents, canonical_paths
from apps.routes.services.routes import create_route
from tests.factories import RouteFactory, UserFactory

COMMAND = "seed_canonical_routes"
_COMMAND_BUILDER = (
    "apps.routes.management.commands.seed_canonical_routes.build_slope_samples"
)
_WORKER_BUILDER = "apps.routes.services.slope_segments.build_slope_samples"

RECORD: dict[str, Any] = {
    "window_m": 10.0,
    "stride_m": 25.0,
    "grid": "snowdesk-terrain-5m-3035",
    "points": [[7.4, 46.1], [7.41, 46.11]],
    "segments": [{"angle_deg": 34.2, "aspect_deg": 105.3}],
    "cruxes": [],
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
    """Patch the sampler on both paths to return ``RECORD``.

    Yields:
        The command module's patched builder, for call assertions.

    """
    with (
        patch(_WORKER_BUILDER, return_value=RECORD),
        patch(_COMMAND_BUILDER, return_value=RECORD) as command_builder,
    ):
        yield command_builder


@pytest.fixture
def dev_user() -> User:
    """Create the seeded normal dev user."""
    return UserFactory.create(email=NORMAL_USER_EMAIL)


def _canonical_count(user: User) -> int:
    """Return how many canonical routes ``user`` holds."""
    names = [path.name for path in canonical_paths()]
    return Route.objects.filter(user=user, source_filename__in=names).count()


@pytest.mark.django_db
@pytest.mark.usefixtures("builder")
class TestReadOnlyByDefault:
    """The bare invocation reports and does nothing else."""

    def test_it_creates_nothing(self, dev_user: User) -> None:
        """No route is written without --commit."""
        output = _run()

        assert Route.objects.count() == 0
        assert "[READ-ONLY]" in output

    def test_it_samples_nothing(self, dev_user: User, builder: MagicMock) -> None:
        """An unsampled canonical route stays null and the origin is not asked."""
        filename, raw = canonical_documents()[0]
        with patch(_WORKER_BUILDER, return_value=None):
            route = create_route(dev_user, raw, source_filename=filename)

        _run()

        route.refresh_from_db()
        assert route.slope_samples is None
        builder.assert_not_called()


@pytest.mark.django_db
@pytest.mark.usefixtures("builder")
class TestCommit:
    """--commit brings the dev user's canonical set up to date."""

    def test_it_creates_the_canonical_routes(self, dev_user: User) -> None:
        """One route per committed canonical file, owned by the dev user."""
        _run("--commit")

        assert _canonical_count(dev_user) == len(canonical_paths()) == 4

    def test_a_second_run_prints_only_the_no_op_sentence(self, dev_user: User) -> None:
        """The exact string init-worktree greps for, and nothing else."""
        _run("--commit")

        output = _run("--commit")

        assert output == f"{UP_TO_DATE}\n"
        assert _canonical_count(dev_user) == 4

    def test_a_held_route_is_not_duplicated(self, dev_user: User) -> None:
        """Matched on source_filename, so the held one stays the only one."""
        filename, raw = canonical_documents()[0]
        create_route(dev_user, raw, source_filename=filename)

        _run("--commit")

        assert (
            Route.objects.filter(user=dev_user, source_filename=filename).count() == 1
        )
        assert _canonical_count(dev_user) == 4

    def test_an_unsampled_route_is_sampled(self, dev_user: User) -> None:
        """A null left by a failed sampling at seed time is filled in."""
        filename, raw = canonical_documents()[0]
        with patch(_WORKER_BUILDER, return_value=None):
            route = create_route(dev_user, raw, source_filename=filename)
        assert route.slope_samples is None

        _run("--commit")

        route.refresh_from_db()
        assert route.slope_samples == RECORD

    def test_an_unanswered_route_stays_null_and_is_not_a_failure(
        self, dev_user: User
    ) -> None:
        """Null stays honest, as in the backfill: a later run retries it."""
        filename, raw = canonical_documents()[0]
        with patch(_WORKER_BUILDER, return_value=None):
            route = create_route(dev_user, raw, source_filename=filename)

        with patch(_COMMAND_BUILDER, return_value=None):
            output = _run("--commit")

        route.refresh_from_db()
        assert route.slope_samples is None
        assert f"Left {filename} unsampled" in output

    def test_other_routes_are_left_alone(self, dev_user: User) -> None:
        """An unsampled non-canonical route is not this command's to sample."""
        other = RouteFactory.create(user=dev_user, source_filename="mine.gpx")

        _run("--commit")

        other.refresh_from_db()
        assert other.slope_samples is None

    def test_a_sampling_error_exits_non_zero(self, dev_user: User) -> None:
        """A raised error is a failure the caller must be able to see."""
        filename, raw = canonical_documents()[0]
        with patch(_WORKER_BUILDER, return_value=None):
            create_route(dev_user, raw, source_filename=filename)

        with (
            patch(_COMMAND_BUILDER, side_effect=RuntimeError("boom")),
            pytest.raises(CommandError),
        ):
            _run("--commit")


@pytest.mark.django_db
@pytest.mark.usefixtures("builder")
class TestNoDevUser:
    """Nobody to seed for is reported, not failed."""

    def test_it_exits_cleanly_and_writes_nothing(self) -> None:
        """Returns normally, names the missing user, creates no route."""
        output = _run("--commit")

        assert NORMAL_USER_EMAIL in output
        assert Route.objects.count() == 0
