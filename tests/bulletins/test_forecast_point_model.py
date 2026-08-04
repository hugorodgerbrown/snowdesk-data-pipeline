"""
tests/bulletins/test_forecast_point_model.py — Tests for the ForecastPoint model.

Covers:
  - Factory produces a valid instance via .create(), with cells consistent
    with the representative coordinates.
  - to_string() / __str__() format.
  - unique_together constraint on (lat_cell, lon_cell, elevation_band).
  - Quantisation edge cases: cell boundaries and negative-coordinate floors,
    exercised via apps.bulletins.services.forecast_points.quantise_*.
  - ForecastPointQuerySet.active() — points with at least one favourite or
    resort (SNOW-503).
  - ForecastPointQuerySet.inactive() — the exact complement of active()
    (SNOW-633).
"""

import pytest
from django.db import IntegrityError

from apps.bulletins.models import ForecastPoint
from apps.bulletins.services.forecast_points import (
    ELEVATION_BAND_SIZE,
    LAT_CELL_SIZE,
    LON_CELL_SIZE,
    quantise_elevation,
    quantise_lat,
    quantise_lon,
)
from tests.factories import FavouriteFactory, ForecastPointFactory, ResortFactory


@pytest.mark.django_db
class TestForecastPointFactory:
    """The factory produces valid, well-formed ForecastPoint instances."""

    def test_create_returns_forecast_point(self) -> None:
        """ForecastPointFactory.create() returns a persisted ForecastPoint."""
        point = ForecastPointFactory.create()
        assert isinstance(point, ForecastPoint)
        assert point.pk is not None

    def test_cells_consistent_with_coordinates(self) -> None:
        """Factory-derived cells match the quantisation of the coordinates."""
        point = ForecastPointFactory.create(
            latitude=46.123, longitude=7.456, elevation=1834.0
        )
        assert point.lat_cell == quantise_lat(46.123)
        assert point.lon_cell == quantise_lon(7.456)
        assert point.elevation_band == quantise_elevation(1834.0)


@pytest.mark.django_db
class TestForecastPointStr:
    """__str__ / to_string() format."""

    def test_to_string_format(self) -> None:
        """to_string() returns '<lat>,<lon> @<elevation>m'."""
        point = ForecastPointFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )
        assert point.to_string() == "46.10000,7.40000 @1500m"

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string()."""
        point = ForecastPointFactory.create()
        assert str(point) == point.to_string()


@pytest.mark.django_db
class TestForecastPointConstraints:
    """Model-level integrity constraints."""

    def test_unique_together_cell(self) -> None:
        """Inserting two points for the same cell raises IntegrityError."""
        ForecastPointFactory.create(lat_cell=100, lon_cell=200, elevation_band=5)
        with pytest.raises(IntegrityError):
            ForecastPointFactory.create(lat_cell=100, lon_cell=200, elevation_band=5)

    def test_different_elevation_band_same_lat_lon_cell_allowed(self) -> None:
        """Two points can share (lat_cell, lon_cell) with different bands."""
        ForecastPointFactory.create(lat_cell=100, lon_cell=200, elevation_band=5)
        other = ForecastPointFactory.create(
            lat_cell=100, lon_cell=200, elevation_band=6
        )
        assert other.pk is not None


class TestQuantisation:
    """Quantisation edge cases: cell boundaries and negative-coordinate floors."""

    def test_quantise_lat_at_exact_boundary(self) -> None:
        """A latitude exactly on a cell boundary floors into the higher cell."""
        assert quantise_lat(0.02) == 2
        assert quantise_lat(0.01) == 1

    def test_quantise_lat_just_below_boundary(self) -> None:
        """A latitude just below a cell boundary floors into the lower cell."""
        assert quantise_lat(0.0099999) == 0

    def test_quantise_lon_at_exact_boundary(self) -> None:
        """A longitude exactly on a cell boundary floors into the higher cell."""
        assert quantise_lon(0.03) == 2
        assert quantise_lon(0.015) == 1

    def test_quantise_elevation_at_exact_boundary(self) -> None:
        """An elevation exactly on a band boundary floors into the higher band."""
        assert quantise_elevation(400) == 2
        assert quantise_elevation(200) == 1

    def test_quantise_lat_negative_coordinate_floors_correctly(self) -> None:
        """Negative latitudes floor towards negative infinity, not truncate."""
        # -0.005 / 0.01 == -0.5; floor(-0.5) == -1, whereas int(-0.5) == 0.
        assert quantise_lat(-0.005) == -1
        assert quantise_lat(-0.01) == -1
        assert quantise_lat(-0.011) == -2

    def test_quantise_lon_negative_coordinate_floors_correctly(self) -> None:
        """Negative longitudes floor towards negative infinity, not truncate."""
        assert quantise_lon(-0.0075) == -1
        assert quantise_lon(-0.015) == -1
        assert quantise_lon(-0.0151) == -2

    def test_quantise_elevation_negative_floors_correctly(self) -> None:
        """Below-sea-level elevations floor towards negative infinity."""
        assert quantise_elevation(-50) == -1
        assert quantise_elevation(-200) == -1
        assert quantise_elevation(-201) == -2

    def test_cell_sizes_match_module_constants(self) -> None:
        """Sanity-check the module constants used by the quantisation helpers."""
        assert LAT_CELL_SIZE == 0.01
        assert LON_CELL_SIZE == 0.015
        assert ELEVATION_BAND_SIZE == 200


@pytest.mark.django_db
class TestForecastPointActiveQuerySet:
    """ForecastPointQuerySet.active() — points referenced by a favourite or resort."""

    def test_point_with_favourite_is_active(self) -> None:
        """A point with one favourite is included in active()."""
        point = ForecastPointFactory.create()
        FavouriteFactory.create(forecast_point=point)
        assert point in ForecastPoint.objects.active()

    def test_point_without_favourite_is_not_active(self) -> None:
        """A point with no favourites is excluded from active()."""
        point = ForecastPointFactory.create()
        assert point not in ForecastPoint.objects.active()

    def test_point_with_resort_is_active(self) -> None:
        """A point referenced only by a Resort is included in active()."""
        point = ForecastPointFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)
        assert point in ForecastPoint.objects.active()

    def test_point_with_favourite_and_resort_appears_once(self) -> None:
        """A point shared by a favourite and a resort appears exactly once."""
        point = ForecastPointFactory.create()
        FavouriteFactory.create(forecast_point=point)
        ResortFactory.create(geocoded=True, forecast_point=point)
        assert ForecastPoint.objects.active().filter(pk=point.pk).count() == 1

    def test_orphan_point_excluded(self) -> None:
        """A point referenced by neither a favourite nor a resort is excluded."""
        point = ForecastPointFactory.create()
        assert point not in ForecastPoint.objects.active()


@pytest.mark.django_db
class TestForecastPointInactiveQuerySet:
    """ForecastPointQuerySet.inactive() — the complement of active() (SNOW-633)."""

    def test_orphan_point_is_inactive(self) -> None:
        """A point with no favourite and no resort is included in inactive()."""
        point = ForecastPointFactory.create()
        assert point in ForecastPoint.objects.inactive()

    def test_point_with_favourite_is_not_inactive(self) -> None:
        """A point held by a favourite is excluded from inactive()."""
        point = ForecastPointFactory.create()
        FavouriteFactory.create(forecast_point=point)
        assert point not in ForecastPoint.objects.inactive()

    def test_point_with_resort_is_not_inactive(self) -> None:
        """A point held by a resort is excluded from inactive()."""
        point = ForecastPointFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)
        assert point not in ForecastPoint.objects.inactive()

    def test_partitions_the_table(self) -> None:
        """active() and inactive() together cover every row, without overlap."""
        held = ForecastPointFactory.create()
        FavouriteFactory.create(forecast_point=held)
        orphan = ForecastPointFactory.create(latitude=47.9, longitude=8.9)

        active = set(ForecastPoint.objects.active())
        inactive = set(ForecastPoint.objects.inactive())

        assert active == {held}
        assert inactive == {orphan}
        assert active | inactive == set(ForecastPoint.objects.all())
        assert not active & inactive
