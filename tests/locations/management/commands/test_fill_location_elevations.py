"""
tests/locations/management/commands/test_fill_location_elevations.py

Covers ``fill_location_elevations`` (SNOW-732) — the out-of-band walk that
resolves ``Location.elevation_m`` from each location's coordinate.

The command exists because ``Location.objects.unresolved()`` was written for
this pass and never had one, so every imported location sat at a null
elevation. These tests are the AGENTS.md command contract — nothing written
without ``--commit``, the walk is idempotent, a partial batch exits non-zero
— plus the ``--report`` comparison, which is the ticket's own check on
whether a curated coordinate is pinned where its note claims. Since
SNOW-1398 each location is one task (run inline here), and a failed lookup
opens the Open-Meteo breaker for the tasks behind it.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command, load_command_class

from apps.core.command_tasks import backing_off
from apps.locations.models import Location
from apps.locations.services.elevation import OPEN_METEO, fill_elevation_task
from tests.factories import LocationFactory

COMMAND = "fill_location_elevations"
ELEVATION_FN = "apps.locations.services.elevation.fetch_elevation"


@pytest.mark.django_db
class TestFillLocationElevations:
    """The walk, its candidate set, and its exit code."""

    def test_writes_nothing_without_commit(self) -> None:
        """A bare run asks nothing and persists nothing."""
        location = LocationFactory.create(elevation_m=None)
        with patch(ELEVATION_FN, return_value=3329.0) as fetch:
            call_command(COMMAND, stdout=StringIO(), verbosity=0)
        fetch.assert_not_called()
        location.refresh_from_db()
        assert location.elevation_m is None

    def test_it_is_offered_to_the_ops_server(self) -> None:
        """It only enqueues, so run_command may start it."""
        assert (
            getattr(load_command_class("apps.locations", COMMAND), "mcp_runnable")
            is True
        )

    def test_commit_persists_the_resolved_elevation(self) -> None:
        """``--commit`` writes the value the service returned."""
        location = LocationFactory.create(elevation_m=None)
        with patch(ELEVATION_FN, return_value=3329.0):
            call_command(COMMAND, "--commit", stdout=StringIO(), verbosity=0)
        location.refresh_from_db()
        assert location.elevation_m == 3329.0

    def test_skips_locations_that_already_have_one(self) -> None:
        """A second pass is a no-op, so an interrupted run is cheap to resume."""
        LocationFactory.create(elevation_m=2000.0)
        with patch(ELEVATION_FN, return_value=3329.0) as fetch:
            call_command(COMMAND, "--commit", stdout=StringIO(), verbosity=0)
        assert fetch.call_count == 0

    def test_force_reresolves_a_location_that_has_one(self) -> None:
        """``--force`` is what a moved pin needs — a stale height is invisible."""
        location = LocationFactory.create(elevation_m=2000.0)
        with patch(ELEVATION_FN, return_value=3329.0):
            call_command(COMMAND, "--commit", "--force", stdout=StringIO(), verbosity=0)
        location.refresh_from_db()
        assert location.elevation_m == 3329.0

    def test_a_failure_backs_off_the_rest(self) -> None:
        """A failed lookup is the upstream, so the tasks behind it wait.

        Their rows stay candidates for the next run rather than each
        failing in turn against a service that is down.
        """
        LocationFactory.create(elevation_m=None, name="first")
        LocationFactory.create(elevation_m=None, name="second")
        with patch(ELEVATION_FN, side_effect=RuntimeError("boom")) as fetch:
            with pytest.raises(SystemExit):
                call_command(
                    COMMAND, "--commit", "--delay", "0", stdout=StringIO(), verbosity=0
                )
        assert fetch.call_count == 1
        assert backing_off(OPEN_METEO)
        assert Location.objects.unresolved().count() == 2

    def test_the_task_waits_for_its_slot(self) -> None:
        """The pace is the gap each lookup waits for on Open-Meteo's slot."""
        location = LocationFactory.create(elevation_m=None)
        with (
            patch(ELEVATION_FN, return_value=2000.0),
            patch("apps.locations.services.elevation.wait_for_slot") as wait,
        ):
            assert fill_elevation_task.call(location.pk, 0.5) == 2000.0
        wait.assert_called_once_with(OPEN_METEO, 0.5)

    def test_partial_batch_exits_non_zero(self) -> None:
        """cron and CI must see a partial run as bad, per the command contract."""
        LocationFactory.create(elevation_m=None)
        with patch(ELEVATION_FN, side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc:
                call_command(COMMAND, "--commit", stdout=StringIO(), verbosity=0)
        assert exc.value.code == 1

    def test_clean_run_does_not_exit_non_zero(self) -> None:
        """The inverse: a run with no failures must not raise."""
        LocationFactory.create(elevation_m=None)
        with patch(ELEVATION_FN, return_value=2000.0):
            call_command(COMMAND, "--commit", stdout=StringIO(), verbosity=0)


@pytest.mark.django_db
class TestReport:
    """``--report`` compares the resolved height against the sheet's claim."""

    def _sheet(self, tmp_path: Path, uuid: str, note: str) -> Path:
        """Write a one-row locations sheet carrying ``note``."""
        path = tmp_path / "locations.tsv"
        path.write_text(
            "uuid\tname\tkind\tlatitude\tlongitude\tnote\n"
            f"{uuid}\tMont Fort\tPEAK\t46.10\t7.29\t{note}\n"
        )
        return path

    def test_flags_a_location_pinned_far_from_its_claim(self, tmp_path: Path) -> None:
        """The mis-pin case this check exists for."""
        location = LocationFactory.create(elevation_m=1200.0)
        sheet = self._sheet(tmp_path, str(location.uuid), "ele=3329 m")
        out = StringIO()
        with patch(
            "apps.locations.management.commands.fill_location_elevations."
            "DEFAULT_SHEET_PATH",
            sheet,
        ):
            call_command(COMMAND, "--report", stdout=out)
        assert "off by 2129m" in out.getvalue()

    def test_accepts_a_location_within_tolerance(self, tmp_path: Path) -> None:
        """A rounded sheet figure is ordinary and must not read as a fault."""
        location = LocationFactory.create(elevation_m=3320.0)
        sheet = self._sheet(tmp_path, str(location.uuid), "ele=3329 m")
        out = StringIO()
        with patch(
            "apps.locations.management.commands.fill_location_elevations."
            "DEFAULT_SHEET_PATH",
            sheet,
        ):
            call_command(COMMAND, "--report", stdout=out)
        assert "0 disagreement(s)" in out.getvalue()

    def test_report_writes_nothing(self, tmp_path: Path) -> None:
        """It is a read — it must never resolve or persist."""
        location = LocationFactory.create(elevation_m=1200.0)
        sheet = self._sheet(tmp_path, str(location.uuid), "ele=3329 m")
        with patch(ELEVATION_FN) as fetch:
            with patch(
                "apps.locations.management.commands.fill_location_elevations."
                "DEFAULT_SHEET_PATH",
                sheet,
            ):
                call_command(COMMAND, "--report", stdout=StringIO())
        assert fetch.call_count == 0
        location.refresh_from_db()
        assert location.elevation_m == 1200.0
