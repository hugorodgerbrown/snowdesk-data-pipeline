"""
tests/public/test_weather_surfaces.py — Tests for the server-rendered weather.

Three surfaces read a ``Weather`` row and render it (SNOW-761):

* the bulletin masthead, from the region's ``centroid_location``;
* the resort page, one block per curated ``Location`` linked to the resort;
* the favourite detail card, from the pin's own ``Location``.

The assertions below are about what reaches the page, and about the one
behaviour every surface shares: **no row means no panel, never an error**.
Historical bulletin dates have no ``Weather`` row at all — the SNOW-731
backfill is deferred — so that path is the common one, not the edge.

Every test that names an icon file runs under ``freeze_time`` at midday. The
day/night suffix projects the current wall-clock time onto the page date, so
an unfrozen assertion passes locally and fails in CI after sunset.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation
from freezegun import freeze_time

from apps.locations.models import Location, ResortLocation
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortFactory,
    ResortLocationFactory,
    UserFactory,
    WeatherFactory,
)

MIDDAY = "2026-08-30T12:00:00+00:00"
PAGE_DATE = datetime.date(2026, 8, 30)

# A sunrise/sunset pair bracketing MIDDAY, so the day icon is selected.
SUNRISE = datetime.datetime(2026, 8, 30, 6, 30, tzinfo=datetime.UTC)
SUNSET = datetime.datetime(2026, 8, 30, 20, 15, tzinfo=datetime.UTC)


def _forecast_day(date: str, weather_code: int = 71) -> dict[str, object]:
    """Build one ``forecast[]`` entry with no nested hourly series.

    Args:
        date: The forward day's ISO date.
        weather_code: The day's WMO code.

    Returns:
        The entry dict.

    """
    return {
        "date": date,
        "weather_code": weather_code,
        "sunrise": f"{date}T06:30:00+00:00",
        "sunset": f"{date}T20:15:00+00:00",
        "temperature_2m_max": 2.0,
        "temperature_2m_min": -5.0,
        "snowfall_sum": 8.0,
        "freezing_level_height": 2000.0,
    }


@pytest.mark.django_db
class TestBulletinMasthead:
    """The masthead's weather row, read off the region's centroid."""

    @freeze_time(MIDDAY)
    def test_centroid_weather_reaches_the_masthead(self) -> None:
        """A centroid row for the page date renders inside the masthead."""
        centroid = LocationFactory.create(name="CH-1000 centroid")
        region = MicroRegionFactory.create(centroid_location=centroid)
        WeatherFactory.create(
            location=centroid,
            observed_on=PAGE_DATE,
            weather_code=71,
            sunrise=SUNRISE,
            sunset=SUNSET,
        )

        response = Client().get(region.get_absolute_url(PAGE_DATE))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="bulletin-weather-panel"' in content
        assert "light_snow-day.svg" in content
        assert "Light snow" in content

    def test_a_region_with_no_centroid_renders_no_panel(self) -> None:
        """No centroid link means nothing to read, and no panel."""
        region = MicroRegionFactory.create(centroid_location=None)

        response = Client().get(region.get_absolute_url(PAGE_DATE))

        assert response.status_code == 200
        assert 'data-testid="bulletin-weather-panel"' not in response.content.decode()

    def test_a_date_with_no_row_renders_no_panel(self) -> None:
        """A historical date predates the estate's first fetch.

        This is the SNOW-731 case: no backfill, so a bulletin for last
        February has no row. It must degrade to no panel, not to an error.
        """
        centroid = LocationFactory.create()
        region = MicroRegionFactory.create(centroid_location=centroid)
        WeatherFactory.create(location=centroid, observed_on=PAGE_DATE)

        response = Client().get(region.get_absolute_url(datetime.date(2026, 2, 14)))

        assert response.status_code == 200
        assert 'data-testid="bulletin-weather-panel"' not in response.content.decode()


@pytest.mark.django_db
class TestResortPage:
    """One weather block per curated location linked to the resort."""

    @freeze_time(MIDDAY)
    def test_one_block_per_linked_location_labelled_with_its_elevation(self) -> None:
        """Each block names its location and the height it was read at."""
        today = datetime.date(2026, 8, 30)
        resort = ResortFactory.create(name="Verbier")
        village = LocationFactory.create(
            name="Verbier", kind=Location.KIND.VILLAGE, elevation_m=1500.0
        )
        peak = LocationFactory.create(
            name="Mont Fort", kind=Location.KIND.PEAK, elevation_m=3328.0
        )
        ResortLocationFactory.create(
            resort=resort,
            location=village,
            role=ResortLocation.ROLE.BASE,
            is_primary=True,
        )
        ResortLocationFactory.create(
            resort=resort, location=peak, role=ResortLocation.ROLE.TOP
        )
        for location in (village, peak):
            WeatherFactory.create(
                location=location,
                observed_on=today,
                sunrise=SUNRISE,
                sunset=SUNSET,
            )

        response = Client().get(resort.get_absolute_url())

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="resort-weather"' in content
        assert "Verbier · 1500 m" in content
        assert "Mont Fort · 3328 m" in content
        # The primary (base) link leads, whatever order the links were made.
        assert content.index("Verbier · 1500 m") < content.index("Mont Fort · 3328 m")

    @freeze_time(MIDDAY)
    def test_the_outlook_comes_from_the_same_rows_forecast_column(self) -> None:
        """The multi-day strip is one row's ``forecast[]``, not N rows."""
        today = datetime.date(2026, 8, 30)
        resort = ResortFactory.create()
        location = LocationFactory.create(name="Attelas", elevation_m=2200.0)
        ResortLocationFactory.create(resort=resort, location=location)
        WeatherFactory.create(
            location=location,
            observed_on=today,
            sunrise=SUNRISE,
            sunset=SUNSET,
            forecast=[_forecast_day("2026-08-31"), _forecast_day("2026-09-01")],
        )

        response = Client().get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-date="2026-08-30"' in content
        assert 'data-date="2026-08-31"' in content
        assert 'data-date="2026-09-01"' in content

    def test_a_location_without_a_row_is_dropped_not_rendered_empty(self) -> None:
        """A resort whose peak has a row and village none shows one block."""
        today = datetime.date(2026, 8, 30)
        resort = ResortFactory.create()
        with_row = LocationFactory.create(name="Attelas")
        without_row = LocationFactory.create(name="Ruinettes")
        ResortLocationFactory.create(resort=resort, location=with_row)
        ResortLocationFactory.create(resort=resort, location=without_row)
        WeatherFactory.create(location=with_row, observed_on=today)

        with freeze_time(MIDDAY):
            response = Client().get(resort.get_absolute_url())

        content = response.content.decode()
        assert "Attelas" in content
        assert 'data-testid="resort-weather-0"' in content
        assert 'data-testid="resort-weather-1"' not in content

    def test_a_resort_with_no_linked_locations_omits_the_section(self) -> None:
        """No links means no section heading, not an empty one."""
        resort = ResortFactory.create()

        response = Client().get(resort.get_absolute_url())

        assert response.status_code == 200
        assert 'data-testid="resort-weather"' not in response.content.decode()


@pytest.mark.django_db
class TestFavouriteCard:
    """The favourite detail card's weather section."""

    @freeze_time(MIDDAY)
    def test_the_pins_own_location_reaches_the_card(self) -> None:
        """The card reads the pin's Location, not its region's centroid."""
        user = UserFactory.create()
        favourite = FavouriteFactory.create(user=user, name="The col")
        WeatherFactory.create(
            location=favourite.location,
            observed_on=datetime.date(2026, 8, 30),
            weather_code=0,
            sunrise=SUNRISE,
            sunset=SUNSET,
        )

        client = Client()
        client.force_login(user)
        response = client.get(
            reverse("favourites:detail", kwargs={"uuid": favourite.uuid})
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="favourite-card-weather"' in content
        assert "clear-day.svg" in content

    @freeze_time(MIDDAY)
    def test_the_outlook_panel_reaches_the_card_with_its_chart(self) -> None:
        """The card is the forecast panel's second consumer.

        It includes the same partial the resort page does, with only its
        ``testid_prefix`` differing — so a change to the panel's contract
        that this misses is a change that ships broken on one of them.
        """
        user = UserFactory.create()
        favourite = FavouriteFactory.create(user=user)
        WeatherFactory.create(
            location=favourite.location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
            hourly=[
                {
                    "time": "2026-08-30T09:00",
                    "temperature_2m": -2.0,
                    "wind_speed_10m": 14.0,
                    "wind_gusts_10m": 30.0,
                    "precipitation": 0.2,
                    "snowfall": 0.1,
                    "freezing_level_height": 1900.0,
                },
                {
                    "time": "2026-08-30T10:00",
                    "temperature_2m": -1.0,
                    "wind_speed_10m": 16.0,
                    "wind_gusts_10m": 33.0,
                    "precipitation": 0.4,
                    "snowfall": 0.2,
                    "freezing_level_height": 1950.0,
                },
            ],
        )

        client = Client()
        client.force_login(user)
        response = client.get(
            reverse("favourites:detail", kwargs={"uuid": favourite.uuid})
        )

        content = response.content.decode()
        assert 'data-testid="favourite-forecast-panel"' in content
        assert 'data-testid="favourite-forecast-chart-svg"' in content
        assert 'name="favourite-forecast-days"' in content

    def test_a_pin_with_no_row_renders_no_weather_section(self) -> None:
        """No row for today means the section is absent, not empty."""
        user = UserFactory.create()
        favourite = FavouriteFactory.create(user=user)

        client = Client()
        client.force_login(user)
        response = client.get(
            reverse("favourites:detail", kwargs={"uuid": favourite.uuid})
        )

        assert response.status_code == 200
        assert 'data-testid="favourite-card-weather"' not in response.content.decode()


def _hourly(date: str) -> list[dict[str, object]]:
    """Build a short hourly series for one day.

    Args:
        date: The day's ISO date, used to build each row's ``time``.

    Returns:
        The rows.

    """
    return [
        {
            "time": f"{date}T{hour:02d}:00",
            "temperature_2m": -4.0 + hour,
            "snowfall": 0.4,
            "precipitation": 0.6,
            "wind_speed_10m": 18.0,
            "wind_gusts_10m": 34.0,
            "freezing_level_height": 1800.0,
        }
        for hour in (6, 7, 8, 9)
    ]


@pytest.mark.django_db
class TestForecastPanelDaySelector:
    """The day strip is the control, and it is CSS-only (SNOW-776).

    Rendered through the resort page rather than the partial in isolation,
    because the trap this guards against — two panels sharing one radio
    group — only exists on a page that renders more than one.
    """

    def _resort_with_panels(self, count: int) -> str:
        """Build a resort with ``count`` weather-bearing locations.

        Args:
            count: How many curated locations to link and give a row.

        Returns:
            The resort's URL.

        """
        resort = ResortFactory.create()
        for index in range(count):
            location = LocationFactory.create(name=f"Point {index}")
            ResortLocationFactory.create(resort=resort, location=location)
            WeatherFactory.create(
                location=location,
                observed_on=PAGE_DATE,
                sunrise=SUNRISE,
                sunset=SUNSET,
                hourly=_hourly("2026-08-30"),
                forecast=[
                    {**_forecast_day("2026-08-31"), "hourly": _hourly("2026-08-31")},
                    _forecast_day("2026-09-01"),
                ],
            )
        return resort.get_absolute_url()

    @freeze_time(MIDDAY)
    def test_the_first_live_day_ships_checked(self) -> None:
        """The panel opens on a day, so it is never a strip over nothing."""
        response = Client().get(self._resort_with_panels(1))

        content = response.content.decode()
        assert content.count('data-testid="resort-weather-0-day-input"') == 2
        checked = re.findall(r'<input[^>]*?value="([^"]+)"[^>]*?checked', content, re.S)
        assert checked == ["2026-08-30"]

    @freeze_time(MIDDAY)
    def test_exactly_one_chart_is_marked_as_the_panel_default(self) -> None:
        """The default is marked, not merely checked.

        Two ``checked`` radios sharing a group name leave only the last one
        checked, which is what the component library produces when it
        renders one variant twice on the same page. The mark is what the
        CSS falls back to, so a panel is never a strip over nothing.
        """
        response = Client().get(self._resort_with_panels(2))

        content = response.content.decode()
        assert content.count("data-forecast-chart-focus") == 2

    @freeze_time(MIDDAY)
    def test_a_day_past_the_horizon_gets_no_input_and_no_label(self) -> None:
        """An inert day is not a control that ignores presses."""
        response = Client().get(self._resort_with_panels(1))

        content = response.content.decode()
        assert 'data-date="2026-09-01"' in content
        assert content.count('aria-disabled="true"') == 1
        assert content.count("<label") == 2

    @freeze_time(MIDDAY)
    def test_one_chart_per_day_that_carries_a_series(self) -> None:
        """The chart count follows ``hourly``, not the length of the strip."""
        response = Client().get(self._resort_with_panels(1))

        content = response.content.decode()
        assert content.count('data-testid="resort-weather-0-day-chart"') == 2
        assert content.count('data-testid="resort-weather-0-chart-svg"') == 2

    @freeze_time(MIDDAY)
    def test_two_panels_on_one_page_get_distinct_radio_group_names(self) -> None:
        """A shared name would fuse every panel into one group.

        The resort page renders one panel per curated location, so choosing
        a day in one would clear the choice in all the others.
        """
        response = Client().get(self._resort_with_panels(2))

        content = response.content.decode()
        assert 'name="resort-weather-0-days"' in content
        assert 'name="resort-weather-1-days"' in content

    @freeze_time(MIDDAY)
    def test_chart_coordinates_survive_a_comma_decimal_locale(self) -> None:
        """``localize off`` is load-bearing, not decoration.

        Coordinates reach the template as floats. An active locale that
        formats decimals with a comma would render ``x="12,5"`` and take
        every band out.
        """
        with translation.override("de"):
            response = Client().get(self._resort_with_panels(1))

        content = response.content.decode()
        # 06:00 is the first hour in the series, so its bar is the first
        # rect on the axis: 6 * 10 + 5 - 6 / 2 user units.
        assert 'x="62.0"' in content
        assert 'x="62,0"' not in content
