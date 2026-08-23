"""
tests/public/test_resort_location_forecasts.py — SNOW-702 resort page weather.

The resort page showed one unlabelled forecast, taken from the resort's
stored coordinate — which is the geocoder's hit for its *name*, so in
practice the village: Verbier reads 1436 m against terrain to 3330 m. And
it stacked region-centroid numbers directly above point numbers for the
same day, which the accepted weather-sourcing rule forbids.

Covers:
  - One labelled panel per linked Location, primary first then up by
    elevation.
  - The hero band takes its numbers from the primary location's day-0 row.
  - It falls back to the region WeatherSnapshot for an uncurated resort, so
    incremental curation costs nobody their weather.
  - A location with no cell, or a cell with no rows, is omitted.
  - The legacy single panel and the per-location sections are mutually
    exclusive.
  - Four resorts sharing Mont Fort all render it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.locations.models import ResortLocation
from apps.regions.models import Resort
from tests.factories import (
    ForecastPointFactory,
    ForecastPointWeatherFactory,
    LocationFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherSnapshotFactory,
)

PER_LOCATION = 'data-testid="resort-location-forecast-section"'
LEGACY = 'data-testid="resort-forecast-section"'


def _linked(
    resort: Resort,
    name: str,
    elevation: float,
    role: str,
    *,
    is_primary: bool = False,
    with_rows: bool = True,
    temp_max: float = 5.0,
    lat: float = 46.1,
    lon: float = 7.4,
) -> ResortLocation:
    """Link a resort to a named location with a fetched forecast cell.

    Args:
        resort: The resort to link.
        name: The location's curated name.
        elevation: Its resolved elevation in metres.
        role: The ResortLocation.ROLE value.
        is_primary: Whether the resort leads with it.
        with_rows: Whether to write a forward window for its cell.
        temp_max: Day-0 maximum temperature, so a test can tell two
            locations' rows apart.
        lat: The cell's latitude — vary it to avoid the cell's
            unique_together on the quantised triple.
        lon: The cell's longitude.

    Returns:
        The created link.

    """
    cell = ForecastPointFactory.create(latitude=lat, longitude=lon)
    if with_rows:
        today = timezone.localdate()
        for offset in range(3):
            ForecastPointWeatherFactory.create(
                forecast_point=cell,
                valid_for_date=today + timedelta(days=offset),
                temperature_2m_max=temp_max if offset == 0 else temp_max - 1,
            )
    return ResortLocationFactory.create(
        resort=resort,
        location=LocationFactory.create(
            name=name, elevation_m=elevation, forecast_cell=cell
        ),
        role=role,
        is_primary=is_primary,
    )


@pytest.mark.django_db
class TestResortLocationForecasts:
    """One labelled forecast per linked location."""

    def test_renders_one_section_per_linked_location(self, client: Client) -> None:
        """Three locations, three panels."""
        resort = ResortFactory.create(geocoded=True)
        _linked(
            resort,
            "Verbier village",
            1436,
            ResortLocation.ROLE.BASE,
            is_primary=True,
            lat=46.1,
            lon=7.4,
        )
        _linked(resort, "Les Attelas", 2730, ResortLocation.ROLE.MID, lat=46.2, lon=7.5)
        _linked(resort, "Mont Fort", 3328, ResortLocation.ROLE.TOP, lat=46.3, lon=7.6)

        body = client.get(resort.get_absolute_url()).content.decode()

        assert body.count(PER_LOCATION) == 3

    def test_each_panel_is_labelled_with_name_and_elevation(
        self, client: Client
    ) -> None:
        """The label is the thing the single unlabelled panel never said."""
        resort = ResortFactory.create(geocoded=True)
        _linked(resort, "Mont Fort", 3328, ResortLocation.ROLE.TOP, is_primary=True)

        body = client.get(resort.get_absolute_url()).content.decode()

        assert "Mont Fort" in body
        assert "3328" in body

    def test_primary_leads_then_ascending_elevation(self, client: Client) -> None:
        """Ordering reads as the way up the mountain."""
        resort = ResortFactory.create(geocoded=True)
        _linked(resort, "Mont Fort", 3328, ResortLocation.ROLE.TOP, lat=46.3, lon=7.6)
        _linked(
            resort,
            "Verbier village",
            1436,
            ResortLocation.ROLE.BASE,
            is_primary=True,
            lat=46.1,
            lon=7.4,
        )
        _linked(resort, "Les Attelas", 2730, ResortLocation.ROLE.MID, lat=46.2, lon=7.5)

        body = client.get(resort.get_absolute_url()).content.decode()

        assert (
            body.index("Verbier village")
            < body.index("Les Attelas")
            < body.index("Mont Fort")
        )

    def test_a_location_with_no_rows_is_omitted(self, client: Client) -> None:
        """No empty state — a section promising weather it lacks is worse."""
        resort = ResortFactory.create(geocoded=True)
        _linked(
            resort,
            "Verbier village",
            1436,
            ResortLocation.ROLE.BASE,
            is_primary=True,
            lat=46.1,
            lon=7.4,
        )
        _linked(
            resort,
            "Mont Fort",
            3328,
            ResortLocation.ROLE.TOP,
            with_rows=False,
            lat=46.3,
            lon=7.6,
        )

        body = client.get(resort.get_absolute_url()).content.decode()

        assert body.count(PER_LOCATION) == 1
        assert "Verbier village" in body

    def test_a_location_with_no_cell_is_omitted(self, client: Client) -> None:
        """An uncurated-cell location is filtered out in SQL, not rendered."""
        resort = ResortFactory.create(geocoded=True)
        ResortLocationFactory.create(
            resort=resort,
            location=LocationFactory.create(name="Unresolved", forecast_cell=None),
            role=ResortLocation.ROLE.TOP,
        )

        body = client.get(resort.get_absolute_url()).content.decode()

        assert PER_LOCATION not in body

    def test_one_location_renders_what_it_always_did_plus_a_label(
        self, client: Client
    ) -> None:
        """The single-location case degrades cleanly.

        The whole point of shipping this before the estate is curated.
        """
        resort = ResortFactory.create(geocoded=True)
        _linked(
            resort,
            "Verbier village",
            1436,
            ResortLocation.ROLE.BASE,
            is_primary=True,
        )

        body = client.get(resort.get_absolute_url()).content.decode()

        assert body.count(PER_LOCATION) == 1
        assert "Verbier village" in body
        assert "1436" in body

    def test_four_resorts_share_one_mont_fort(self, client: Client) -> None:
        """The sharing the model exists for, visible on the page.

        One Location row, four resort pages, each rendering it as their
        top — where the resort sheet holds four copies of the scalar 3330.
        """
        cell = ForecastPointFactory.create()
        today = timezone.localdate()
        ForecastPointWeatherFactory.create(forecast_point=cell, valid_for_date=today)
        mont_fort = LocationFactory.create(
            name="Mont Fort", elevation_m=3328, forecast_cell=cell
        )
        resorts = [
            ResortFactory.create(geocoded=True, name=name)
            for name in ("Verbier", "Nendaz", "Veysonnaz", "Thyon")
        ]
        for resort in resorts:
            ResortLocationFactory.create(
                resort=resort, location=mont_fort, role=ResortLocation.ROLE.TOP
            )

        for resort in resorts:
            body = client.get(resort.get_absolute_url()).content.decode()
            assert "Mont Fort" in body


@pytest.mark.django_db
class TestResortHeroBand:
    """The hero takes its numbers from a point, not a region centroid."""

    def test_hero_uses_the_primary_locations_day_zero_row(self, client: Client) -> None:
        """Numbers from points, decoration from snapshots.

        The page previously stacked region-centroid figures directly above
        point figures for the same day, which the accepted rule forbids.
        The snapshot here carries a temperature nothing should show.
        """
        resort = ResortFactory.create(geocoded=True)
        WeatherSnapshotFactory.create(
            region=resort.region,
            valid_for_date=timezone.localdate(),
            temperature_2m_max=-99.0,
        )
        _linked(
            resort,
            "Verbier village",
            1436,
            ResortLocation.ROLE.BASE,
            is_primary=True,
            temp_max=7.0,
        )

        response = client.get(resort.get_absolute_url())

        assert response.context["weather_display"]["temp_max"] == 7.0

    def test_hero_falls_back_to_the_region_snapshot_when_uncurated(
        self, client: Client
    ) -> None:
        """An uncurated resort loses nothing.

        What makes SNOW-701's incremental curation safe to land a few
        resorts at a time.
        """
        resort = ResortFactory.create(geocoded=True)
        WeatherSnapshotFactory.create(
            region=resort.region,
            valid_for_date=timezone.localdate(),
            temperature_2m_max=-3.0,
        )

        response = client.get(resort.get_absolute_url())

        assert response.context["weather_display"]["temp_max"] == -3.0

    def test_hero_ignores_a_window_that_starts_tomorrow(self, client: Client) -> None:
        """A day-0 row must actually be today.

        Otherwise a cell whose stored window starts tomorrow would caption
        tomorrow's figure as today's.
        """
        resort = ResortFactory.create(geocoded=True)
        WeatherSnapshotFactory.create(
            region=resort.region,
            valid_for_date=timezone.localdate(),
            temperature_2m_max=-3.0,
        )
        cell = ForecastPointFactory.create()
        ForecastPointWeatherFactory.create(
            forecast_point=cell,
            valid_for_date=timezone.localdate() + timedelta(days=1),
            temperature_2m_max=12.0,
        )
        ResortLocationFactory.create(
            resort=resort,
            location=LocationFactory.create(
                name="Verbier village", elevation_m=1436, forecast_cell=cell
            ),
            role=ResortLocation.ROLE.BASE,
            is_primary=True,
        )

        response = client.get(resort.get_absolute_url())

        assert response.context["weather_display"]["temp_max"] == -3.0


@pytest.mark.django_db
class TestLegacyPanelIsMutuallyExclusive:
    """The SNOW-572 single panel survives only for uncurated resorts."""

    def test_legacy_panel_shows_for_an_uncurated_linked_resort(
        self, client: Client
    ) -> None:
        """A resort SNOW-701 has not reached keeps what SNOW-572 gave it."""
        cell = ForecastPointFactory.create()
        ForecastPointWeatherFactory.create(
            forecast_point=cell, valid_for_date=timezone.localdate()
        )
        resort = ResortFactory.create(geocoded=True, forecast_point=cell)

        body = client.get(resort.get_absolute_url()).content.decode()

        assert LEGACY in body
        assert PER_LOCATION not in body

    def test_legacy_panel_is_dropped_once_a_location_is_linked(
        self, client: Client
    ) -> None:
        """The two sections never render together."""
        cell = ForecastPointFactory.create(latitude=47.9, longitude=8.9)
        ForecastPointWeatherFactory.create(
            forecast_point=cell, valid_for_date=timezone.localdate()
        )
        resort = ResortFactory.create(geocoded=True, forecast_point=cell)
        _linked(
            resort,
            "Verbier village",
            1436,
            ResortLocation.ROLE.BASE,
            is_primary=True,
        )

        body = client.get(resort.get_absolute_url()).content.decode()

        assert PER_LOCATION in body
        assert LEGACY not in body
