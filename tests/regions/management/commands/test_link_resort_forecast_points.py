"""
tests/regions/management/commands/test_link_resort_forecast_points.py

Covers ``link_resort_forecast_points`` (SNOW-503):
  - Links a geocoded, unlinked resort to a resolved ForecastCell under
    --commit.
  - Ungeocoded resorts (missing latitude/longitude) are never selected.
  - Already-linked resorts are never selected — a second run is a no-op
    (idempotency).
  - Two nearby geocoded resorts share one ForecastCell (the reuse path);
    elevation-band separation mints a distinct point for each.
  - Dry-run (no --commit) resolves but writes no FK.
  - A per-resort resolve failure increments ``failed``, raises
    CommandError (non-zero exit), and does not abort the rest of the
    batch — the other resort is still linked.

``fetch_elevation`` is mocked at the ``apps.weather.services.forecast_cells``
module seam (the same seam ``tests/weather/services/test_forecast_cells.py``
patches) so no live Open-Meteo call happens anywhere in this suite.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.weather.models import ForecastCell
from tests.factories import ForecastCellFactory, ResortFactory


def _patch_elevation(elevation: float) -> AbstractContextManager[MagicMock]:
    """Patch fetch_elevation (module seam) to return a fixed elevation."""
    return patch(
        "apps.weather.services.forecast_cells.fetch_elevation",
        return_value=elevation,
    )


@pytest.mark.django_db
class TestLinkResortForecastCellsCommit:
    """--commit behaviour: resolves and persists the FK."""

    def test_links_geocoded_unlinked_resort(self) -> None:
        """A geocoded, unlinked resort gets its forecast_point FK set."""
        resort = ResortFactory.create(geocoded=True)

        with _patch_elevation(1500.0):
            call_command(
                "link_resort_forecast_points",
                "--commit",
                "--delay",
                "0",
                stdout=StringIO(),
            )

        resort.refresh_from_db()
        assert resort.forecast_point is not None
        assert ForecastCell.objects.count() == 1

    def test_skips_ungeocoded_resort(self) -> None:
        """A resort with no latitude/longitude is never selected."""
        resort = ResortFactory.create()  # latitude/longitude default to None

        with _patch_elevation(1500.0) as mock_fetch:
            call_command(
                "link_resort_forecast_points",
                "--commit",
                "--delay",
                "0",
                stdout=StringIO(),
            )

        mock_fetch.assert_not_called()
        resort.refresh_from_db()
        assert resort.forecast_point is None

    def test_skips_already_linked_resort(self) -> None:
        """A resort that already has a forecast_point is never re-resolved."""
        existing_point = ForecastCellFactory.create()
        resort = ResortFactory.create(geocoded=True, forecast_point=existing_point)

        with _patch_elevation(1500.0) as mock_fetch:
            call_command(
                "link_resort_forecast_points",
                "--commit",
                "--delay",
                "0",
                stdout=StringIO(),
            )

        mock_fetch.assert_not_called()
        resort.refresh_from_db()
        assert resort.forecast_point_id == existing_point.pk

    def test_idempotent_second_run_selects_nothing(self) -> None:
        """A second run finds no candidates once every resort is linked."""
        ResortFactory.create(geocoded=True)

        with _patch_elevation(1500.0):
            call_command(
                "link_resort_forecast_points",
                "--commit",
                "--delay",
                "0",
                stdout=StringIO(),
            )

        out = StringIO()
        with _patch_elevation(1500.0) as mock_fetch:
            call_command(
                "link_resort_forecast_points",
                "--commit",
                "--delay",
                "0",
                stdout=out,
            )

        mock_fetch.assert_not_called()
        assert "Linking 0 geocoded resort(s)" in out.getvalue()

    def test_two_nearby_resorts_share_one_forecast_point(self) -> None:
        """Two resorts ~200m apart at the same elevation reuse a single point."""
        resort_a = ResortFactory.create(geocoded=True, latitude=46.1, longitude=7.4)
        # ~0.0018 degrees latitude is ~200m — within the 750m reuse threshold.
        resort_b = ResortFactory.create(geocoded=True, latitude=46.1018, longitude=7.4)

        with _patch_elevation(1500.0):
            call_command(
                "link_resort_forecast_points",
                "--commit",
                "--delay",
                "0",
                stdout=StringIO(),
            )

        resort_a.refresh_from_db()
        resort_b.refresh_from_db()
        assert ForecastCell.objects.count() == 1
        assert resort_a.forecast_point_id == resort_b.forecast_point_id

    def test_elevation_band_separation_mints_distinct_points(self) -> None:
        """Two resorts close horizontally but far apart in elevation mint two points."""
        resort_high = ResortFactory.create(geocoded=True, latitude=46.1, longitude=7.4)
        resort_low = ResortFactory.create(
            geocoded=True, latitude=46.1005, longitude=7.4
        )

        def _elevation_side_effect(latitude: float, _longitude: float) -> float:
            return 1500.0 if latitude == 46.1 else 1750.0

        with patch(
            "apps.weather.services.forecast_cells.fetch_elevation",
            side_effect=_elevation_side_effect,
        ):
            call_command(
                "link_resort_forecast_points",
                "--commit",
                "--delay",
                "0",
                stdout=StringIO(),
            )

        resort_high.refresh_from_db()
        resort_low.refresh_from_db()
        assert ForecastCell.objects.count() == 2
        assert resort_high.forecast_point_id != resort_low.forecast_point_id


@pytest.mark.django_db
class TestLinkResortForecastCellsDryRun:
    """Dry-run (no --commit) behaviour."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit, the resort's forecast_point FK is left null."""
        resort = ResortFactory.create(geocoded=True)

        out = StringIO()
        with _patch_elevation(1500.0):
            call_command(
                "link_resort_forecast_points",
                "--delay",
                "0",
                stdout=out,
            )

        resort.refresh_from_db()
        assert resort.forecast_point is None
        assert "No data written" in out.getvalue()
        assert "Pass --commit" in out.getvalue()


@pytest.mark.django_db
class TestLinkResortForecastCellsFailureIsolation:
    """A per-resort failure never aborts the batch."""

    def test_one_bad_resort_fails_others_still_link(self) -> None:
        """The failing resort is counted; the healthy resort still links."""
        resort_ok = ResortFactory.create(geocoded=True, latitude=46.1, longitude=7.4)
        resort_bad = ResortFactory.create(geocoded=True, latitude=50.0, longitude=10.0)

        def _elevation_side_effect(latitude: float, _longitude: float) -> float:
            if latitude == 50.0:
                raise RuntimeError("simulated elevation lookup failure")
            return 1500.0

        with patch(
            "apps.weather.services.forecast_cells.fetch_elevation",
            side_effect=_elevation_side_effect,
        ):
            with pytest.raises(CommandError, match="1 resort failure"):
                call_command(
                    "link_resort_forecast_points",
                    "--commit",
                    "--delay",
                    "0",
                    stdout=StringIO(),
                )

        resort_ok.refresh_from_db()
        resort_bad.refresh_from_db()
        assert resort_ok.forecast_point is not None
        assert resort_bad.forecast_point is None


@pytest.mark.django_db
class TestLinkResortForecastCellsVerbosityAndPacing:
    """--verbosity and --delay behaviour beyond the default test pacing."""

    def test_verbosity_2_logs_each_resolved_resort(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """--verbosity 2 logs a per-resort resolution line."""
        ResortFactory.create(geocoded=True)

        with caplog.at_level("INFO"):
            with _patch_elevation(1500.0):
                call_command(
                    "link_resort_forecast_points",
                    "--commit",
                    "--delay",
                    "0",
                    "--verbosity",
                    "2",
                    stdout=StringIO(),
                )

        assert any("Resolved resort" in message for message in caplog.messages)

    def test_delay_sleeps_between_resorts(self) -> None:
        """A positive --delay sleeps between (but not after) resorts."""
        ResortFactory.create(geocoded=True, latitude=46.1, longitude=7.4)
        ResortFactory.create(geocoded=True, latitude=47.0, longitude=8.0)

        with _patch_elevation(1500.0):
            with patch(
                "apps.regions.management.commands.link_resort_forecast_points.time.sleep"
            ) as mock_sleep:
                call_command(
                    "link_resort_forecast_points",
                    "--commit",
                    "--delay",
                    "0.5",
                    stdout=StringIO(),
                )

        # Two candidates -> one inter-resort sleep, not one after the last.
        mock_sleep.assert_called_once_with(0.5)
