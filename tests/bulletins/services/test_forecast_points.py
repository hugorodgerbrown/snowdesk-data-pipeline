"""
tests/bulletins/services/test_forecast_points.py — Tests for resolve_forecast_point.

Covers:
  - Reuse within the 750m horizontal / 150m elevation thresholds.
  - Distinct rows created when a candidate is outside either threshold
    (too far horizontally, or the altitude-separation case: ~1km apart but
    1,500m vs 3,300m elevation).
  - The IntegrityError race path: get_or_create raises, the winner's row
    is re-fetched and returned.
  - fetch_elevation is called exactly once per resolution.

fetch_elevation is mocked at the bulletins.services.forecast_points module
seam so no HTTP mocking leaks into these DB-level resolution tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import _patch, patch

import pytest
from django.db import IntegrityError

from bulletins.models import ForecastPoint
from bulletins.services.forecast_points import resolve_forecast_point
from tests.factories import ForecastPointFactory


def _patch_elevation(elevation: float) -> _patch[Any]:
    """Patch fetch_elevation (module seam) to return a fixed elevation."""
    return patch(
        "bulletins.services.forecast_points.fetch_elevation",
        return_value=elevation,
    )


@pytest.mark.django_db
class TestResolveForecastPointReuse:
    """Reuse of an existing nearby ForecastPoint."""

    def test_reuses_point_within_thresholds(self) -> None:
        """A pin ~200m from an existing point, at a similar elevation, reuses it."""
        existing = ForecastPointFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )
        # ~0.0018 degrees latitude is ~200m.
        with _patch_elevation(1520.0):
            resolved = resolve_forecast_point(46.1018, 7.4)

        assert resolved.pk == existing.pk
        assert ForecastPoint.objects.count() == 1

    def test_second_call_at_same_location_reuses_the_first(self) -> None:
        """Two resolutions of ~200m-apart pins collapse to one row."""
        with _patch_elevation(1500.0):
            first = resolve_forecast_point(46.1, 7.4)
        with _patch_elevation(1500.0):
            second = resolve_forecast_point(46.1018, 7.4)

        assert first.pk == second.pk
        assert ForecastPoint.objects.count() == 1

    def test_elevation_fetched_exactly_once_per_resolution(self) -> None:
        """fetch_elevation is called exactly once per resolve_forecast_point call."""
        with _patch_elevation(1500.0) as mock_fetch:
            resolve_forecast_point(46.1, 7.4)
        mock_fetch.assert_called_once_with(46.1, 7.4)


@pytest.mark.django_db
class TestResolveForecastPointDistinctPoints:
    """Distinct ForecastPoint rows created outside the reuse thresholds."""

    def test_creates_new_point_beyond_horizontal_threshold(self) -> None:
        """A pin ~2km away creates a new point rather than reusing the old one."""
        existing = ForecastPointFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )
        with _patch_elevation(1500.0):
            resolved = resolve_forecast_point(46.118, 7.4)  # ~2km north

        assert resolved.pk != existing.pk
        assert ForecastPoint.objects.count() == 2

    def test_creates_new_point_beyond_elevation_threshold(self) -> None:
        """A pin close horizontally but far apart in elevation creates a new point."""
        existing = ForecastPointFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )
        with _patch_elevation(1750.0):  # 250m above the existing point's elevation
            resolved = resolve_forecast_point(46.1005, 7.4)

        assert resolved.pk != existing.pk
        assert ForecastPoint.objects.count() == 2

    def test_altitude_separation_creates_distinct_points(self) -> None:
        """Pins ~1km apart but 1,500m vs 3,300m elevation resolve to distinct points."""
        with _patch_elevation(1500.0):
            first = resolve_forecast_point(46.1, 7.4)
        with _patch_elevation(3300.0):
            second = resolve_forecast_point(46.109, 7.4)  # ~1km away

        assert first.pk != second.pk
        assert ForecastPoint.objects.count() == 2


@pytest.mark.django_db
class TestResolveForecastPointRace:
    """The IntegrityError race-safety path in get_or_create."""

    def test_integrity_error_returns_winners_row(self) -> None:
        """When get_or_create raises IntegrityError, the winner's row is returned."""
        winner_holder: dict[str, ForecastPoint] = {}

        def _simulate_concurrent_insert(**kwargs: Any) -> tuple[ForecastPoint, bool]:
            """Simulate another request winning the race to create the cell."""
            defaults: dict[str, Any] = kwargs.pop("defaults", {})
            winner_holder["winner"] = ForecastPoint.objects.create(
                lat_cell=kwargs["lat_cell"],
                lon_cell=kwargs["lon_cell"],
                elevation_band=kwargs["elevation_band"],
                **defaults,
            )
            raise IntegrityError("duplicate key")

        with (
            _patch_elevation(1500.0),
            patch(
                "bulletins.models.ForecastPointQuerySet.get_or_create",
                side_effect=_simulate_concurrent_insert,
            ),
        ):
            resolved = resolve_forecast_point(46.1, 7.4)

        assert resolved.pk == winner_holder["winner"].pk
        assert ForecastPoint.objects.count() == 1
