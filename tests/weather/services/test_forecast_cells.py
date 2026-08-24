"""
tests/weather/services/test_forecast_cells.py — Tests for forecast-cell resolution.

Covers:
  - Reuse within the 750m horizontal / 150m elevation thresholds.
  - Distinct rows created when a candidate is outside either threshold
    (too far horizontally, or the altitude-separation case: ~1km apart but
    1,500m vs 3,300m elevation).
  - get_or_create semantics: two calls for the same quantised key converge
    on one row (the guarantee resolve_forecast_cell relies on for race
    safety — Django's own get_or_create catches a concurrent IntegrityError
    internally and re-fetches by the lookup kwargs).
  - fetch_elevation is called exactly once per resolution, and not at all
    when the caller passes an elevation it already holds.
  - find_forecast_cell, the read-only twin (SNOW-719): it must make the
    same decision as resolve_forecast_cell without writing, which means
    matching both of its lookups — the reuse neighbourhood and the exact
    grid key get_or_create would otherwise satisfy with a get.

fetch_elevation is mocked at the apps.weather.services.forecast_cells module
seam so no HTTP mocking leaks into these DB-level resolution tests.
"""

from __future__ import annotations

import math
from contextlib import AbstractContextManager
from unittest.mock import MagicMock, patch

import pytest

from apps.core.coordinates import InvalidCoordinatesError
from apps.weather.models import ForecastCell
from apps.weather.services.forecast_cells import (
    find_forecast_cell,
    resolve_forecast_cell,
)
from tests.factories import ForecastCellFactory


def _patch_elevation(elevation: float) -> AbstractContextManager[MagicMock]:
    """Patch fetch_elevation (module seam) to return a fixed elevation."""
    return patch(
        "apps.weather.services.forecast_cells.fetch_elevation",
        return_value=elevation,
    )


@pytest.mark.django_db
class TestResolveForecastCellReuse:
    """Reuse of an existing nearby ForecastCell."""

    def test_reuses_point_within_thresholds(self) -> None:
        """A pin ~200m from an existing point, at a similar elevation, reuses it."""
        existing = ForecastCellFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )
        # ~0.0018 degrees latitude is ~200m.
        with _patch_elevation(1520.0):
            resolved = resolve_forecast_cell(46.1018, 7.4)

        assert resolved.pk == existing.pk
        assert ForecastCell.objects.count() == 1

    def test_second_call_at_same_location_reuses_the_first(self) -> None:
        """Two resolutions of ~200m-apart pins collapse to one row."""
        with _patch_elevation(1500.0):
            first = resolve_forecast_cell(46.1, 7.4)
        with _patch_elevation(1500.0):
            second = resolve_forecast_cell(46.1018, 7.4)

        assert first.pk == second.pk
        assert ForecastCell.objects.count() == 1

    def test_elevation_fetched_exactly_once_per_resolution(self) -> None:
        """fetch_elevation is called exactly once per resolve_forecast_cell call."""
        with _patch_elevation(1500.0) as mock_fetch:
            resolve_forecast_cell(46.1, 7.4)
        mock_fetch.assert_called_once_with(46.1, 7.4)


@pytest.mark.django_db
class TestResolveForecastCellDistinctPoints:
    """Distinct ForecastCell rows created outside the reuse thresholds."""

    def test_creates_new_point_beyond_horizontal_threshold(self) -> None:
        """A pin ~2km away creates a new point rather than reusing the old one."""
        existing = ForecastCellFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )
        with _patch_elevation(1500.0):
            resolved = resolve_forecast_cell(46.118, 7.4)  # ~2km north

        assert resolved.pk != existing.pk
        assert ForecastCell.objects.count() == 2

    def test_creates_new_point_beyond_elevation_threshold(self) -> None:
        """A pin close horizontally but far apart in elevation creates a new point."""
        existing = ForecastCellFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )
        with _patch_elevation(1750.0):  # 250m above the existing point's elevation
            resolved = resolve_forecast_cell(46.1005, 7.4)

        assert resolved.pk != existing.pk
        assert ForecastCell.objects.count() == 2

    def test_altitude_separation_creates_distinct_points(self) -> None:
        """Pins ~1km apart but 1,500m vs 3,300m elevation resolve to distinct points."""
        with _patch_elevation(1500.0):
            first = resolve_forecast_cell(46.1, 7.4)
        with _patch_elevation(3300.0):
            second = resolve_forecast_cell(46.109, 7.4)  # ~1km away

        assert first.pk != second.pk
        assert ForecastCell.objects.count() == 2


@pytest.mark.django_db
class TestForecastCellGetOrCreateConvergence:
    """
    get_or_create semantics that resolve_forecast_cell's race safety relies on.

    resolve_forecast_cell() has no bespoke IntegrityError handling around
    its get_or_create call — Django's own get_or_create already wraps the
    create in a savepoint, catches a concurrent IntegrityError, and
    re-fetches by the lookup kwargs (here, exactly the unique key). These
    tests prove that underlying guarantee directly, without patching the
    queryset class.
    """

    def test_second_get_or_create_for_same_key_reuses_the_first_row(self) -> None:
        """Two get_or_create calls for the same quantised key return one row."""
        first, created_first = ForecastCell.objects.get_or_create(
            lat_cell=100,
            lon_cell=200,
            elevation_band=5,
            defaults={"latitude": 46.1, "longitude": 7.4, "elevation": 1500.0},
        )
        second, created_second = ForecastCell.objects.get_or_create(
            lat_cell=100,
            lon_cell=200,
            elevation_band=5,
            defaults={"latitude": 46.2, "longitude": 7.5, "elevation": 1600.0},
        )

        assert created_first is True
        assert created_second is False
        assert first.pk == second.pk
        assert ForecastCell.objects.count() == 1


class TestResolveForecastCellInvalidCoordinates:
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
        """resolve_forecast_cell rejects bad coords without calling fetch_elevation."""
        with _patch_elevation(1500.0) as mock_fetch:
            with pytest.raises(InvalidCoordinatesError):
                resolve_forecast_cell(latitude, longitude)
        mock_fetch.assert_not_called()


@pytest.mark.django_db
class TestFindForecastCell:
    """find_forecast_cell answers resolve_forecast_cell's question, read-only.

    It exists because ``--commit``-less backfill runs were calling
    ``resolve_forecast_cell`` purely to report which cell they *would* use,
    and creating it in the process (SNOW-719).
    """

    def test_creates_nothing(self) -> None:
        """The whole point: no row, whatever the answer."""
        assert find_forecast_cell(46.1, 7.4, 1500.0) is None
        assert ForecastCell.objects.count() == 0

    def test_finds_a_cell_within_the_reuse_thresholds(self) -> None:
        """~200m away and 20m up is the reuse case resolve would take."""
        existing = ForecastCellFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )

        found = find_forecast_cell(46.1018, 7.4, 1520.0)

        assert found is not None
        assert found.pk == existing.pk

    def test_finds_a_cell_on_the_exact_grid_key_it_cannot_reuse(self) -> None:
        """The get_or_create branch, which the reuse check alone misses.

        A row in the same quantised cell but beyond the 750m horizontal
        threshold fails the reuse check, and ``get_or_create`` still returns
        it rather than creating a second row on a duplicate key. A preview
        that reported "would create" here would overstate the cost.
        """
        # The 0.015-degree longitude cell is ~1,160m wide at this latitude,
        # so two rows can share a key and still be beyond the 750m reuse
        # threshold. These sit near its west and east edges, ~1,080m apart.
        existing = ForecastCellFactory.create(
            latitude=46.1000, longitude=7.3955, elevation=1500.0
        )
        latitude, longitude, elevation = 46.1000, 7.4095, 1500.0
        assert find_forecast_cell(latitude, longitude, elevation) is not None

        with _patch_elevation(elevation):
            resolved = resolve_forecast_cell(latitude, longitude)

        assert resolved.pk == existing.pk
        assert ForecastCell.objects.count() == 1

    def test_agrees_with_resolve_when_a_new_cell_is_needed(self) -> None:
        """None here must mean "resolve would create" there."""
        ForecastCellFactory.create(latitude=46.1, longitude=7.4, elevation=1500.0)

        # Same place, 1,800m higher — outside the elevation threshold and
        # in a different band, so neither lookup matches.
        assert find_forecast_cell(46.1, 7.4, 3300.0) is None

        with _patch_elevation(3300.0):
            resolved = resolve_forecast_cell(46.1, 7.4)

        assert ForecastCell.objects.count() == 2
        assert resolved.elevation == 3300.0

    def test_rejects_invalid_coordinates(self) -> None:
        """A preview must fail on bad input, not report "would create"."""
        with pytest.raises(InvalidCoordinatesError):
            find_forecast_cell(math.nan, 7.4, 1500.0)


@pytest.mark.django_db
class TestResolveForecastCellSuppliedElevation:
    """A caller holding the elevation should not pay for it twice."""

    def test_supplied_elevation_skips_the_lookup(self) -> None:
        """SNOW-719: halves the Open-Meteo bill of the backfill commands."""
        with _patch_elevation(1500.0) as lookup:
            resolved = resolve_forecast_cell(46.1, 7.4, elevation=3300.0)

        lookup.assert_not_called()
        assert resolved.elevation == 3300.0
