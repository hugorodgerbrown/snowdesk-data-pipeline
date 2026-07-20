"""
tests/bulletins/services/test_forecast_points.py — Tests for resolve_forecast_point.

Covers:
  - Reuse within the 750m horizontal / 150m elevation thresholds.
  - Distinct rows created when a candidate is outside either threshold
    (too far horizontally, or the altitude-separation case: ~1km apart but
    1,500m vs 3,300m elevation).
  - get_or_create semantics: two calls for the same quantised key converge
    on one row (the guarantee resolve_forecast_point relies on for race
    safety — Django's own get_or_create catches a concurrent IntegrityError
    internally and re-fetches by the lookup kwargs).
  - fetch_elevation is called exactly once per resolution.

fetch_elevation is mocked at the bulletins.services.forecast_points module
seam so no HTTP mocking leaks into these DB-level resolution tests.
"""

from __future__ import annotations

import math
from contextlib import AbstractContextManager
from unittest.mock import MagicMock, patch

import pytest

from bulletins.models import ForecastPoint
from bulletins.services.forecast_points import resolve_forecast_point
from core.coordinates import InvalidCoordinatesError
from tests.factories import ForecastPointFactory


def _patch_elevation(elevation: float) -> AbstractContextManager[MagicMock]:
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
class TestForecastPointGetOrCreateConvergence:
    """
    get_or_create semantics that resolve_forecast_point's race safety relies on.

    resolve_forecast_point() has no bespoke IntegrityError handling around
    its get_or_create call — Django's own get_or_create already wraps the
    create in a savepoint, catches a concurrent IntegrityError, and
    re-fetches by the lookup kwargs (here, exactly the unique key). These
    tests prove that underlying guarantee directly, without patching the
    queryset class.
    """

    def test_second_get_or_create_for_same_key_reuses_the_first_row(self) -> None:
        """Two get_or_create calls for the same quantised key return one row."""
        first, created_first = ForecastPoint.objects.get_or_create(
            lat_cell=100,
            lon_cell=200,
            elevation_band=5,
            defaults={"latitude": 46.1, "longitude": 7.4, "elevation": 1500.0},
        )
        second, created_second = ForecastPoint.objects.get_or_create(
            lat_cell=100,
            lon_cell=200,
            elevation_band=5,
            defaults={"latitude": 46.2, "longitude": 7.5, "elevation": 1600.0},
        )

        assert created_first is True
        assert created_second is False
        assert first.pk == second.pk
        assert ForecastPoint.objects.count() == 1


class TestResolveForecastPointInvalidCoordinates:
    """SNOW-464: invalid coordinates raise before the Open-Meteo elevation call."""

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [
            (math.nan, 7.4),
            (46.1, math.inf),
            (95.0, 7.4),  # latitude > 90
            (46.1, 200.0),  # longitude > 180
        ],
    )
    def test_invalid_coords_raise_before_elevation_call(
        self, latitude: float, longitude: float
    ) -> None:
        """resolve_forecast_point rejects bad coords without calling fetch_elevation."""
        with _patch_elevation(1500.0) as mock_fetch:
            with pytest.raises(InvalidCoordinatesError):
                resolve_forecast_point(latitude, longitude)
        mock_fetch.assert_not_called()
