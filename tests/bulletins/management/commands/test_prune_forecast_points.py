"""
tests/bulletins/management/commands/test_prune_forecast_points.py

Covers ``prune_forecast_points`` (SNOW-633):
  - Deletes an unreferenced point under --commit, cascading its weather and
    history rows.
  - Leaves points held by a favourite or by a resort alone.
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

from apps.bulletins.models import (
    ForecastPoint,
    ForecastPointWeather,
    ForecastPointWeatherHistory,
)
from tests.factories import (
    FavouriteFactory,
    ForecastPointFactory,
    ForecastPointWeatherFactory,
    ForecastPointWeatherHistoryFactory,
    ResortFactory,
)


@pytest.mark.django_db
class TestPruneForecastPointsCommit:
    """--commit behaviour: deletes unreferenced points and their children."""

    def test_deletes_unreferenced_point(self) -> None:
        """A point with no favourite and no resort is deleted."""
        point = ForecastPointFactory.create()

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert not ForecastPoint.objects.filter(pk=point.pk).exists()

    def test_cascades_weather_and_history(self) -> None:
        """Deleting a point takes its weather and history rows with it."""
        point = ForecastPointFactory.create()
        ForecastPointWeatherFactory.create(forecast_point=point)
        ForecastPointWeatherHistoryFactory.create(forecast_point=point)

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastPointWeather.objects.count() == 0
        assert ForecastPointWeatherHistory.objects.count() == 0

    def test_keeps_point_held_by_favourite(self) -> None:
        """A point referenced by a favourite is never deleted."""
        point = ForecastPointFactory.create()
        FavouriteFactory.create(forecast_point=point)

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastPoint.objects.filter(pk=point.pk).exists()

    def test_keeps_point_held_by_resort(self) -> None:
        """A point referenced by a resort is never deleted."""
        point = ForecastPointFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastPoint.objects.filter(pk=point.pk).exists()

    def test_deletes_only_the_unreferenced_one(self) -> None:
        """A mixed table loses the orphan and keeps the held point."""
        held = ForecastPointFactory.create()
        FavouriteFactory.create(forecast_point=held)
        orphan = ForecastPointFactory.create(latitude=47.9, longitude=8.9)

        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert list(ForecastPoint.objects.all()) == [held]
        assert not ForecastPoint.objects.filter(pk=orphan.pk).exists()

    def test_second_run_is_a_no_op(self) -> None:
        """Once pruned, a second run finds no candidates."""
        ForecastPointFactory.create()
        call_command("prune_forecast_points", "--commit", stdout=StringIO())

        out = StringIO()
        call_command("prune_forecast_points", "--commit", stdout=out)

        assert "0 unreferenced ForecastPoint(s)" in out.getvalue()


@pytest.mark.django_db
class TestPruneForecastPointsDryRun:
    """Default behaviour: reports the population, writes nothing."""

    def test_dry_run_deletes_nothing(self) -> None:
        """Without --commit the point and its weather survive."""
        point = ForecastPointFactory.create()
        ForecastPointWeatherFactory.create(forecast_point=point)

        call_command("prune_forecast_points", stdout=StringIO())

        assert ForecastPoint.objects.filter(pk=point.pk).exists()
        assert ForecastPointWeather.objects.count() == 1

    def test_dry_run_reports_what_would_go(self) -> None:
        """The dry-run names the counts it would delete and says so."""
        point = ForecastPointFactory.create()
        ForecastPointWeatherFactory.create(forecast_point=point)

        out = StringIO()
        call_command("prune_forecast_points", stdout=out)
        output = out.getvalue()

        assert "1 unreferenced ForecastPoint(s)" in output
        assert "Would delete 1 point(s), 1 weather row(s)" in output
        assert "Dry-run" in output


@pytest.mark.django_db
class TestPruneForecastPointsFailure:
    """A protected point is counted, reported, and does not abort the batch."""

    def test_protected_point_counts_as_failed(self) -> None:
        """ProtectedError on one point raises CommandError after the batch."""
        ForecastPointFactory.create()

        with (
            patch.object(
                ForecastPoint,
                "delete",
                side_effect=ProtectedError("still referenced", set()),
            ),
            pytest.raises(CommandError, match="1 point"),
        ):
            call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastPoint.objects.count() == 1

    def test_one_failure_does_not_stop_the_others(self) -> None:
        """A failing point is skipped; the remaining points still delete."""
        first = ForecastPointFactory.create()
        second = ForecastPointFactory.create(latitude=47.9, longitude=8.9)
        real_delete = ForecastPoint.delete

        def _fail_for_first(self: ForecastPoint, *args: object) -> object:
            """Raise for the first point only; delete the rest for real."""
            if self.pk == first.pk:
                raise ProtectedError("still referenced", set())
            return real_delete(self)

        with (
            patch.object(ForecastPoint, "delete", _fail_for_first),
            pytest.raises(CommandError),
        ):
            call_command("prune_forecast_points", "--commit", stdout=StringIO())

        assert ForecastPoint.objects.filter(pk=first.pk).exists()
        assert not ForecastPoint.objects.filter(pk=second.pk).exists()
