"""
tests/weather/test_forecast_cell_model.py — Tests for the ForecastCell model.

Covers:
  - Factory produces a valid instance via .create(), with cells consistent
    with the representative coordinates.
  - to_string() / __str__() format.
  - unique_together constraint on (lat_cell, lon_cell, elevation_band).
  - Quantisation edge cases: cell boundaries and negative-coordinate floors,
    exercised via apps.weather.services.forecast_cells.quantise_*.
  - ForecastCellQuerySet.active() — points with at least one favourite,
    resort (SNOW-503) or location (SNOW-700).
  - ForecastCellQuerySet.inactive() — the exact complement of active()
    (SNOW-633).
"""

import pytest
from django.db import IntegrityError

from apps.weather.models import ForecastCell
from apps.weather.services.forecast_cells import (
    ELEVATION_BAND_SIZE,
    LAT_CELL_SIZE,
    LON_CELL_SIZE,
    quantise_elevation,
    quantise_lat,
    quantise_lon,
)
from tests.factories import (
    FavouriteFactory,
    ForecastCellFactory,
    LocationFactory,
    ResortFactory,
)


@pytest.mark.django_db
class TestForecastCellFactory:
    """The factory produces valid, well-formed ForecastCell instances."""

    def test_create_returns_forecast_cell(self) -> None:
        """ForecastCellFactory.create() returns a persisted ForecastCell."""
        point = ForecastCellFactory.create()
        assert isinstance(point, ForecastCell)
        assert point.pk is not None

    def test_cells_consistent_with_coordinates(self) -> None:
        """Factory-derived cells match the quantisation of the coordinates."""
        point = ForecastCellFactory.create(
            latitude=46.123, longitude=7.456, elevation=1834.0
        )
        assert point.lat_cell == quantise_lat(46.123)
        assert point.lon_cell == quantise_lon(7.456)
        assert point.elevation_band == quantise_elevation(1834.0)


@pytest.mark.django_db
class TestForecastCellStr:
    """__str__ / to_string() format."""

    def test_to_string_format(self) -> None:
        """to_string() returns '<lat>,<lon> @<elevation>m'."""
        point = ForecastCellFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )
        assert point.to_string() == "46.10000,7.40000 @1500m"

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string()."""
        point = ForecastCellFactory.create()
        assert str(point) == point.to_string()


@pytest.mark.django_db
class TestForecastCellConstraints:
    """Model-level integrity constraints."""

    def test_unique_together_cell(self) -> None:
        """Inserting two points for the same cell raises IntegrityError."""
        ForecastCellFactory.create(lat_cell=100, lon_cell=200, elevation_band=5)
        with pytest.raises(IntegrityError):
            ForecastCellFactory.create(lat_cell=100, lon_cell=200, elevation_band=5)

    def test_different_elevation_band_same_lat_lon_cell_allowed(self) -> None:
        """Two points can share (lat_cell, lon_cell) with different bands."""
        ForecastCellFactory.create(lat_cell=100, lon_cell=200, elevation_band=5)
        other = ForecastCellFactory.create(lat_cell=100, lon_cell=200, elevation_band=6)
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
class TestForecastCellActiveQuerySet:
    """active() — points referenced by a favourite, resort or location."""

    def test_point_with_favourite_is_active(self) -> None:
        """A point with one favourite is included in active()."""
        point = ForecastCellFactory.create()
        FavouriteFactory.create(forecast_point=point)
        assert point in ForecastCell.objects.active()

    def test_point_without_favourite_is_not_active(self) -> None:
        """A point with no favourites is excluded from active()."""
        point = ForecastCellFactory.create()
        assert point not in ForecastCell.objects.active()

    def test_point_with_resort_is_active(self) -> None:
        """A point referenced only by a Resort is included in active()."""
        point = ForecastCellFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)
        assert point in ForecastCell.objects.active()

    def test_point_with_favourite_and_resort_appears_once(self) -> None:
        """A point shared by a favourite and a resort appears exactly once."""
        point = ForecastCellFactory.create()
        FavouriteFactory.create(forecast_point=point)
        ResortFactory.create(geocoded=True, forecast_point=point)
        assert ForecastCell.objects.active().filter(pk=point.pk).count() == 1

    def test_point_with_location_is_active(self) -> None:
        """A cell referenced only by a Location is included in active().

        The SNOW-700 referent. Without it a curated location's cell would
        never be fetched, and prune_forecast_points would delete it.
        """
        point = ForecastCellFactory.create()
        LocationFactory.create(forecast_cell=point)
        assert point in ForecastCell.objects.active()

    def test_point_held_by_all_three_referents_appears_once(self) -> None:
        """A cell held by a favourite, a resort and a location appears once.

        The three-way join is what ``distinct=True`` exists to stop
        inflating — with two referents a duplicate would still be caught by
        the pair test above, but the counts only actually multiply once
        three reverse relations are joined at the same time.
        """
        point = ForecastCellFactory.create()
        FavouriteFactory.create(forecast_point=point)
        ResortFactory.create(geocoded=True, forecast_point=point)
        LocationFactory.create(forecast_cell=point)
        assert ForecastCell.objects.active().filter(pk=point.pk).count() == 1

    def test_orphan_point_excluded(self) -> None:
        """A point with no favourite, resort or location is excluded."""
        point = ForecastCellFactory.create()
        assert point not in ForecastCell.objects.active()


@pytest.mark.django_db
class TestForecastCellInactiveQuerySet:
    """ForecastCellQuerySet.inactive() — the complement of active() (SNOW-633)."""

    def test_orphan_point_is_inactive(self) -> None:
        """A point with no favourite and no resort is included in inactive()."""
        point = ForecastCellFactory.create()
        assert point in ForecastCell.objects.inactive()

    def test_point_with_favourite_is_not_inactive(self) -> None:
        """A point held by a favourite is excluded from inactive()."""
        point = ForecastCellFactory.create()
        FavouriteFactory.create(forecast_point=point)
        assert point not in ForecastCell.objects.inactive()

    def test_point_with_resort_is_not_inactive(self) -> None:
        """A point held by a resort is excluded from inactive()."""
        point = ForecastCellFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)
        assert point not in ForecastCell.objects.inactive()

    def test_point_with_location_is_not_inactive(self) -> None:
        """A cell held by a location is excluded from inactive().

        The other half of the SNOW-700 referent: if active() gained it and
        inactive() did not, the same cell would sit in both, and
        prune_forecast_points would delete a cell still being fetched for.
        """
        point = ForecastCellFactory.create()
        LocationFactory.create(forecast_cell=point)
        assert point not in ForecastCell.objects.inactive()

    def test_partitions_the_table(self) -> None:
        """active() and inactive() together cover every row, without overlap."""
        held = ForecastCellFactory.create()
        FavouriteFactory.create(forecast_point=held)
        orphan = ForecastCellFactory.create(latitude=47.9, longitude=8.9)

        active = set(ForecastCell.objects.active())
        inactive = set(ForecastCell.objects.inactive())

        assert active == {held}
        assert inactive == {orphan}
        assert active | inactive == set(ForecastCell.objects.all())
        assert not active & inactive
