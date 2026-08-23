"""
tests/locations/management/commands/test_link_location_forecast_cells.py

Covers ``link_location_forecast_cells`` (SNOW-701):
  - Resolves elevation and cell under --commit; writes nothing without it.
  - The elevation stored is the location's OWN, not the shared cell's.
  - Idempotent — a second run selects nothing.
  - Two locations inside the reuse thresholds share one cell.
  - A per-location failure is counted, does not abort the batch, and makes
    the command exit non-zero.
  - The resolved cell puts the location into ForecastCell.active(), so
    prune_forecast_points leaves it alone.
  - Anonymous locations are never candidates — the filter that keeps this
    command's cost bounded once SNOW-704 and SNOW-709 start minting rows.

Every Open-Meteo call is patched out — the command's own tests must not
depend on a live external API.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.locations.models import Location
from apps.weather.models import ForecastCell
from tests.factories import ForecastCellFactory, LocationFactory

COMMAND = "link_location_forecast_cells"
_ELEVATION = (
    "apps.locations.management.commands.link_location_forecast_cells.fetch_elevation"
)
_RESOLVE = "apps.locations.management.commands.link_location_forecast_cells.resolve_forecast_cell"


@pytest.mark.django_db
class TestLinkLocationForecastCellsCommit:
    """--commit persists the resolved elevation and cell."""

    def test_resolves_an_unresolved_location(self) -> None:
        """Both fields are written under --commit."""
        location = LocationFactory.create()
        cell = ForecastCellFactory.create()

        with patch(_ELEVATION, return_value=3328.0), patch(_RESOLVE, return_value=cell):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        location.refresh_from_db()
        assert location.elevation_m == 3328.0
        assert location.forecast_cell == cell

    def test_stores_the_locations_own_elevation_not_the_cells(self) -> None:
        """elevation_m is the location's height, not the shared cell's.

        The cell's elevation is representative of whichever pin minted it
        and is shared by everything inside it. Mont Fort must carry 3328 m,
        not the cell's 1500 m average — which is the whole reason this
        command pays for a second Open-Meteo call.
        """
        location = LocationFactory.create(name="Mont Fort")
        cell = ForecastCellFactory.create(elevation=1500.0)

        with patch(_ELEVATION, return_value=3328.0), patch(_RESOLVE, return_value=cell):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        location.refresh_from_db()
        assert location.elevation_m == 3328.0
        assert location.forecast_cell is not None
        assert location.forecast_cell.elevation == 1500.0

    def test_second_run_selects_nothing(self) -> None:
        """Idempotent — a resolved location is out of the candidate set."""
        LocationFactory.create()
        cell = ForecastCellFactory.create()

        with patch(_ELEVATION, return_value=3328.0), patch(_RESOLVE, return_value=cell):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

            with patch(_ELEVATION) as second_lookup:
                call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        second_lookup.assert_not_called()

    def test_two_nearby_locations_share_one_cell(self) -> None:
        """Locations inside the reuse thresholds resolve to the same cell.

        Many locations to one cell is the cell's entire purpose — this is
        the Mont-Fort-shared-by-four case proven at the fetch layer.
        """
        for _ in range(4):
            LocationFactory.create(latitude=46.1036, longitude=7.2989)
        cell = ForecastCellFactory.create()

        with patch(_ELEVATION, return_value=3328.0), patch(_RESOLVE, return_value=cell):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        assert Location.objects.filter(forecast_cell=cell).count() == 4
        assert ForecastCell.objects.count() == 1

    def test_resolved_location_survives_the_prune_pass(self) -> None:
        """The cell a location holds is active(), so prune leaves it alone.

        The consequence that matters: a cell that fell out of active()
        would stop being fetched and then be deleted with its stored
        weather.
        """
        LocationFactory.create()
        cell = ForecastCellFactory.create()

        with patch(_ELEVATION, return_value=3328.0), patch(_RESOLVE, return_value=cell):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastCell.objects.filter(pk=cell.pk).exists()


@pytest.mark.django_db
class TestLinkLocationForecastCellsDryRun:
    """Without --commit the command resolves but writes nothing."""

    def test_dry_run_writes_nothing(self) -> None:
        """The default invocation is read-only."""
        location = LocationFactory.create()
        cell = ForecastCellFactory.create()

        with patch(_ELEVATION, return_value=3328.0), patch(_RESOLVE, return_value=cell):
            out = StringIO()
            call_command(COMMAND, "--delay", "0", stdout=out)

        location.refresh_from_db()
        assert location.elevation_m is None
        assert location.forecast_cell is None
        assert "Read-only run complete" in out.getvalue()

    def test_dry_run_still_makes_the_external_calls(self) -> None:
        """Resolution happens even in a dry run, so the report is real.

        A dry run that skipped the lookup could only report "1 candidate",
        never "1 would resolve, 0 failed" — and the failures are the half
        an operator needs before committing.
        """
        LocationFactory.create()
        cell = ForecastCellFactory.create()

        with (
            patch(_ELEVATION, return_value=3328.0) as lookup,
            patch(_RESOLVE, return_value=cell),
        ):
            call_command(COMMAND, "--delay", "0", stdout=StringIO())

        lookup.assert_called_once()


@pytest.mark.django_db
class TestLinkLocationForecastCellsFailures:
    """A per-location failure is counted, not fatal to the batch."""

    def test_one_failure_does_not_stop_the_others(self) -> None:
        """A failing lookup is counted; the rest of the batch still runs."""
        LocationFactory.create(latitude=46.1, longitude=7.4)
        LocationFactory.create(latitude=47.9, longitude=8.9)
        cell = ForecastCellFactory.create()

        with (
            patch(_ELEVATION, side_effect=[RuntimeError("boom"), 3328.0]),
            patch(_RESOLVE, return_value=cell),
            pytest.raises(CommandError, match="1 location failure"),
        ):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        assert Location.objects.filter(forecast_cell=cell).count() == 1

    def test_exits_non_zero_when_any_location_fails(self) -> None:
        """A partial run raises CommandError so cron/CI notices."""
        LocationFactory.create()

        with (
            patch(_ELEVATION, side_effect=RuntimeError("boom")),
            pytest.raises(CommandError, match="1 location failure"),
        ):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

    def test_negative_delay_is_rejected(self) -> None:
        """--delay validates as non-negative."""
        with pytest.raises(CommandError):
            call_command(COMMAND, "--delay", "-1", stdout=StringIO())


@pytest.mark.django_db
class TestLinkLocationForecastCellsScope:
    """Only the curated estate is a candidate.

    ``named()`` is what keeps this command's cost bounded. A favourite's
    location already carries its cell from creation, so it is never
    unresolved; an observation's deliberately has none, because an
    observation shows no forecast panel. Without the filter, every
    historical report would bill an Open-Meteo elevation lookup for weather
    nothing renders.
    """

    def test_anonymous_locations_are_not_candidates(self) -> None:
        """A location minted from user data is never resolved here."""
        minted = LocationFactory.create(anonymous=True)
        cell = ForecastCellFactory.create()

        with patch(_ELEVATION, return_value=3328.0), patch(_RESOLVE, return_value=cell):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        minted.refresh_from_db()
        assert minted.elevation_m is None
        assert minted.forecast_cell is None

    def test_no_external_call_is_made_for_an_anonymous_location(self) -> None:
        """The cost, not just the write, is avoided.

        A candidate queryset that included these would pay for the lookup
        even in a dry run — this command resolves before it decides.
        """
        LocationFactory.create(anonymous=True)

        with patch(_ELEVATION) as lookup, patch(_RESOLVE):
            call_command(COMMAND, "--delay", "0", stdout=StringIO())

        lookup.assert_not_called()

    def test_a_curated_location_alongside_them_is_still_resolved(self) -> None:
        """The filter narrows the set; it does not disable the command."""
        curated = LocationFactory.create(name="Mont Fort")
        LocationFactory.create(anonymous=True)
        cell = ForecastCellFactory.create()

        with patch(_ELEVATION, return_value=3328.0), patch(_RESOLVE, return_value=cell):
            call_command(COMMAND, "--commit", "--delay", "0", stdout=StringIO())

        curated.refresh_from_db()
        assert curated.elevation_m == 3328.0
