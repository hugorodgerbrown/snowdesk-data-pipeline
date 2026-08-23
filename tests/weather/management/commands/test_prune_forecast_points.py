"""
tests/weather/management/commands/test_prune_forecast_points.py

Covers ``prune_forecast_points`` (SNOW-633):
  - Deletes an unreferenced point under --commit, cascading its weather and
    history rows.
  - Leaves points held by a favourite, a resort or a location alone.
  - Dry-run (no --commit) reports the same population but deletes nothing.
  - Idempotent — a second --commit run selects nothing.
  - A point that becomes referenced between the walk and the delete is
    counted as failed, raises CommandError (non-zero exit), and does not
    stop the rest of the batch.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import ProtectedError

from apps.weather.models import (
    ForecastCell,
    ForecastCellWeather,
    ForecastCellWeatherHistory,
)
from tests.factories import (
    FavouriteFactory,
    ForecastCellFactory,
    ForecastCellWeatherFactory,
    ForecastCellWeatherHistoryFactory,
    LocationFactory,
    ResortFactory,
)


@pytest.mark.django_db
class TestPruneForecastCellsCommit:
    """--commit behaviour: deletes unreferenced points and their children."""

    def test_deletes_unreferenced_point(self) -> None:
        """A point with no favourite and no resort is deleted."""
        point = ForecastCellFactory.create()

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert not ForecastCell.objects.filter(pk=point.pk).exists()

    def test_cascades_weather_and_history(self) -> None:
        """Deleting a point takes its weather and history rows with it."""
        point = ForecastCellFactory.create()
        ForecastCellWeatherFactory.create(forecast_cell=point)
        ForecastCellWeatherHistoryFactory.create(forecast_cell=point)

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastCellWeather.objects.count() == 0
        assert ForecastCellWeatherHistory.objects.count() == 0

    def test_keeps_point_held_by_favourite(self) -> None:
        """A point referenced by a favourite is never deleted."""
        point = ForecastCellFactory.create()
        FavouriteFactory.create(forecast_point=point)

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastCell.objects.filter(pk=point.pk).exists()

    def test_keeps_point_held_by_resort(self) -> None:
        """A point referenced by a resort is never deleted."""
        point = ForecastCellFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastCell.objects.filter(pk=point.pk).exists()

    def test_keeps_point_held_by_location(self) -> None:
        """A cell referenced by a location is never deleted (SNOW-700).

        The consequence of the active()/inactive() widening, checked at the
        command rather than at the queryset: a curated location's weather
        must survive the prune pass, and would not have before SNOW-700.
        """
        point = ForecastCellFactory.create()
        LocationFactory.create(forecast_cell=point)

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastCell.objects.filter(pk=point.pk).exists()

    def test_deletes_only_the_unreferenced_one(self) -> None:
        """A mixed table loses the orphan and keeps the held point."""
        held = ForecastCellFactory.create()
        FavouriteFactory.create(forecast_point=held)
        orphan = ForecastCellFactory.create(latitude=47.9, longitude=8.9)

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert list(ForecastCell.objects.all()) == [held]
        assert not ForecastCell.objects.filter(pk=orphan.pk).exists()

    def test_second_run_is_a_no_op(self) -> None:
        """Once pruned, a second run finds no candidates."""
        ForecastCellFactory.create()
        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        out = StringIO()
        call_command("prune_forecast_points", "--commit", stdout=out)

        assert "0 unreferenced ForecastCell(s)" in out.getvalue()


@pytest.mark.django_db
class TestPruneForecastCellsDryRun:
    """Default behaviour: reports the population, writes nothing."""

    def test_dry_run_deletes_nothing(self) -> None:
        """Without --commit the point and its weather survive."""
        point = ForecastCellFactory.create()
        ForecastCellWeatherFactory.create(forecast_cell=point)

        call_command("prune_forecast_points", stdout=StringIO())

        assert ForecastCell.objects.filter(pk=point.pk).exists()
        assert ForecastCellWeather.objects.count() == 1

    def test_dry_run_reports_what_would_go(self) -> None:
        """The dry-run names the counts it would delete and says so."""
        point = ForecastCellFactory.create()
        ForecastCellWeatherFactory.create(forecast_cell=point)

        out = StringIO()
        call_command("prune_forecast_points", stdout=out)
        output = out.getvalue()

        assert "1 unreferenced ForecastCell(s)" in output
        assert "Would delete 1 point(s), 1 weather row(s)" in output
        assert "Dry-run" in output


@pytest.mark.django_db
class TestPruneForecastCellsFailure:
    """A protected point is counted, reported, and does not abort the batch."""

    def test_protected_point_counts_as_failed(self) -> None:
        """ProtectedError on one point raises CommandError after the batch."""
        ForecastCellFactory.create()

        with (
            patch.object(
                ForecastCell,
                "delete",
                side_effect=ProtectedError("still referenced", set()),
            ),
            pytest.raises(CommandError, match="1 point"),
        ):
            call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastCell.objects.count() == 1

    def test_one_failure_does_not_stop_the_others(self) -> None:
        """A failing point is skipped; the remaining points still delete."""
        first = ForecastCellFactory.create()
        second = ForecastCellFactory.create(latitude=47.9, longitude=8.9)
        real_delete = ForecastCell.delete

        def _fail_for_first(self: ForecastCell, *args: object) -> object:
            """Raise for the first point only; delete the rest for real."""
            if self.pk == first.pk:
                raise ProtectedError("still referenced", set())
            return real_delete(self)

        with (
            patch.object(ForecastCell, "delete", _fail_for_first),
            pytest.raises(CommandError),
        ):
            call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastCell.objects.filter(pk=first.pk).exists()
        assert not ForecastCell.objects.filter(pk=second.pk).exists()
