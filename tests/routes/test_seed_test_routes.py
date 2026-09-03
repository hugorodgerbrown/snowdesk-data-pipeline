"""
tests/routes/test_seed_test_routes.py — Tests for the seed_test_routes command.

Dev-convenience seeding: attaches the two bundled .gpx fixtures to the seeded
dev user so the routes panel, the Routes page and the share flow can be looked
at with content in them.

Covers:

  * The command is read-only by default and writes nothing without --commit,
    which is the management-command contract in CLAUDE.md.
  * --commit creates one Route per fixture, owned by the dev user.
  * Re-running is a no-op rather than a duplicate, so it is safe in a
    bootstrap script.
  * A missing dev user is a CommandError, not a traceback.
  * The two fixtures differ in the way the /help/routes/ article claims: one
    carries elevation and one does not, so the second has no ascent figure.
    That pairing is the reason there are two fixtures rather than one, and
    without this test a later "tidy up" could replace the flat track with
    another elevated one and quietly cost the article its worked example.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.routes.management.commands.seed_test_routes import (
    DEV_USER_EMAIL,
    FIXTURE_DIR,
    FIXTURES,
)
from apps.routes.models import Route


@pytest.fixture
def dev_user(db: None) -> User:
    """The seeded dev account the command attaches routes to."""
    return User.objects.create_user(
        username="dev", email=DEV_USER_EMAIL, password="unused-in-test"
    )


@pytest.mark.django_db
class TestSeedTestRoutes:
    """The command's contract."""

    def test_is_read_only_by_default(self, dev_user: User) -> None:
        """No --commit, no rows — the default must never alter data."""
        call_command("seed_test_routes", verbosity=0)

        assert Route.objects.count() == 0

    def test_commit_creates_a_route_per_fixture(self, dev_user: User) -> None:
        """--commit persists one route for each bundled fixture."""
        call_command("seed_test_routes", "--commit", verbosity=0)

        routes = Route.objects.for_user(dev_user)
        assert routes.count() == len(FIXTURES)

    def test_rerunning_is_a_no_op(self, dev_user: User) -> None:
        """A second run adds nothing, so a bootstrap can call it blindly."""
        call_command("seed_test_routes", "--commit", verbosity=0)
        call_command("seed_test_routes", "--commit", verbosity=0)

        assert Route.objects.for_user(dev_user).count() == len(FIXTURES)

    def test_missing_dev_user_is_a_command_error(self, db: None) -> None:
        """Without the seeded user the command fails cleanly."""
        with pytest.raises(CommandError, match=DEV_USER_EMAIL):
            call_command("seed_test_routes", "--commit", verbosity=0)

    def test_one_fixture_has_elevation_and_one_does_not(self, dev_user: User) -> None:
        """The pair exists to demonstrate both cases the help article names.

        /help/routes/ says a track recorded without heights shows no profile
        rather than a flat line. Seeding one of each is what lets that be
        checked in the app, so the asymmetry is load-bearing rather than
        incidental.
        """
        call_command("seed_test_routes", "--commit", verbosity=0)

        ascents = {r.name: r.ascent_m for r in Route.objects.for_user(dev_user)}
        assert any(v for v in ascents.values()), ascents
        assert any(v is None for v in ascents.values()), ascents


@pytest.mark.django_db
class TestFixtures:
    """The bundled .gpx files themselves."""

    @pytest.mark.parametrize("filename", FIXTURES)
    def test_fixture_file_exists(self, filename: str) -> None:
        """Every fixture the command names is actually shipped."""
        assert (FIXTURE_DIR / filename).is_file(), filename

    @pytest.mark.parametrize("filename", FIXTURES)
    def test_fixture_parses_as_gpx(self, filename: str) -> None:
        """Each fixture survives the real parser, not just an XML reader."""
        from apps.routes.services.gpx import parse_gpx

        parsed = parse_gpx((FIXTURE_DIR / filename).read_bytes())

        assert parsed.points, filename
