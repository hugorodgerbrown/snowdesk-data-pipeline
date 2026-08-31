"""
tests/public/test_weather_detail.py — the weather card and the forecast page.

Two surfaces, one estate rule (SNOW-761):

  * ``/api/weather/<location_id>/detail/`` — the card the map opens when a
    weather symbol is tapped. Today's conditions and a link out.
  * ``/weather/<location_id>/`` — the full forecast that link opens, with
    the outlook chart, the day strip and the hourly detail.

The load-bearing assertions are the **privacy** ones. Both filter on
``Location.objects.public()``, and a location reachable only from a
``Favourite`` must 404 on both — otherwise a guessed id hands back the name,
coordinates and elevation of a stranger's private pin. That is a test rather
than a comment in the view for the same reason the feed's version is.

The second theme is the **split of detail between the two**: the card was
carrying the chart, the day strip and 72 hourly rows, which was 79% of its
payload on a surface reached by a tap. Those assertions are what stop that
detail drifting back onto it.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.locations.models import Location
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherFactory,
)

TODAY = datetime.date(2026, 1, 12)


def _hourly(day: datetime.date) -> list[dict[str, Any]]:
    """Build a full 24-hour series for one day.

    Args:
        day: The day the rows belong to.

    Returns:
        24 rows, one per hour, in Open-Meteo's own local-time string format.

    """
    return [
        {
            "time": f"{day.isoformat()}T{hour:02d}:00",
            "temperature_2m": -3.0 + hour * 0.4,
            "snowfall": 0.2,
            "precipitation": 0.4,
            "wind_speed_10m": 12.0,
            "wind_gusts_10m": 28.0,
            "freezing_level_height": 1100.0,
        }
        for hour in range(24)
    ]


def _forecast(anchor: datetime.date, days: int = 4) -> list[dict[str, Any]]:
    """Build forward days, the first two carrying an hourly series.

    Mirrors the real shape: ``hourly`` is present on only the first few
    entries, so a fixture giving every day one would misrepresent it.

    Args:
        anchor: The parent row's own day; forward days follow it.
        days: How many forward days to build.

    Returns:
        A list of ``ForecastDay``-shaped dicts.

    """
    entries: list[dict[str, Any]] = []
    for index in range(1, days + 1):
        day = anchor + datetime.timedelta(days=index)
        entry: dict[str, Any] = {
            "date": day.isoformat(),
            "weather_code": 3,
            "sunrise": f"{day.isoformat()}T07:30:00+01:00",
            "sunset": f"{day.isoformat()}T17:00:00+01:00",
            "temperature_2m_max": 2.0 + index,
            "temperature_2m_min": -6.0 + index,
            "snowfall_sum": 1.0,
            "freezing_level_height": 1500.0 + index * 100,
        }
        if index <= 2:
            entry["hourly"] = _hourly(day)
        entries.append(entry)
    return entries


@pytest.fixture
def resort_location() -> Location:
    """A public location linked to a resort, with a week of weather.

    Anonymous, which is the ordinary case: ``link_resort_locations`` mints
    the location at the resort's own pin without naming it, so the surfaces
    have to name it from the resort.
    """
    location = LocationFactory.create(anonymous=True, elevation_m=1450.0)
    ResortLocationFactory.create(
        resort=ResortFactory.create(name="Nendaz"), location=location
    )
    WeatherFactory.create(
        location=location,
        observed_on=TODAY,
        temperature_2m_max=1.5,
        temperature_2m_min=-5.5,
        freezing_level_height=1800.0,
        snowfall_sum=3.0,
        wind_speed_10m_max=22.0,
        precipitation_probability_max=60,
        hourly=_hourly(TODAY),
        forecast=_forecast(TODAY),
    )
    return location


@pytest.fixture
def favourite_only_location() -> Location:
    """A location reachable only from a Favourite — billable, never public."""
    location = LocationFactory.create(anonymous=True, elevation_m=2000.0)
    FavouriteFactory.create(location=location)
    WeatherFactory.create(location=location, observed_on=TODAY)
    return location


def _card_url(location: Location) -> str:
    """Build the card endpoint's URL for a location."""
    return reverse("api:weather_detail", kwargs={"location_id": location.pk})


def _page_url(location: Location) -> str:
    """Build the forecast page's URL for a location."""
    return reverse("public:location_weather", kwargs={"location_id": location.pk})


@pytest.mark.django_db
class TestPrivacy:
    """Neither surface may answer for a location outside the public estate."""

    def test_favourite_only_location_is_in_active_but_not_public(
        self, favourite_only_location: Location
    ) -> None:
        """The predicates themselves, before any view is involved.

        ``active()`` is the billable set and ``public()`` the visible one.
        A favourite's location belongs in the first and not the second, and
        every assertion below rests on that distinction holding.
        """
        assert Location.objects.active().filter(pk=favourite_only_location.pk).exists()
        assert (
            not Location.objects.public().filter(pk=favourite_only_location.pk).exists()
        )

    def test_card_404s_for_a_favourite_only_location(
        self, client: Client, favourite_only_location: Location
    ) -> None:
        """A guessed id must not return a private pin's name or height."""
        assert client.get(_card_url(favourite_only_location)).status_code == 404

    def test_page_404s_for_a_favourite_only_location(
        self, client: Client, favourite_only_location: Location
    ) -> None:
        """The page has the same estate rule as the card."""
        assert client.get(_page_url(favourite_only_location)).status_code == 404

    def test_both_404_for_an_unknown_id(self, client: Client) -> None:
        """An id nothing owns is a 404, not a 500."""
        assert client.get("/api/weather/999999/detail/").status_code == 404
        assert client.get("/weather/999999/").status_code == 404


@pytest.mark.django_db
class TestCard:
    """The tap surface: today, and a way out."""

    def test_renders_todays_conditions(
        self, client: Client, resort_location: Location
    ) -> None:
        """The figures that change a plan are all on the card."""
        response = client.get(_card_url(resort_location), {"date": TODAY.isoformat()})

        assert response.status_code == 200
        html = response.json()["html"]
        assert 'data-testid="weather-detail"' in html
        assert "1450" in html  # elevation
        assert "1800" in html  # freezing level
        assert "22" in html  # wind
        assert "60%" in html  # precipitation probability

    def test_names_an_anonymous_location_by_its_resort(
        self, client: Client, resort_location: Location
    ) -> None:
        """A location with no name must never be headed by its coordinates.

        ``Location.to_string()`` renders the lat/lon pair, which is what a
        naive heading would have printed for the majority of the estate.
        """
        html = client.get(_card_url(resort_location)).json()["html"]

        assert "Nendaz" in html
        assert str(resort_location.latitude) not in html

    def test_names_a_centroid_by_its_region(self, client: Client) -> None:
        """A region centroid is headed by the region it stands for."""
        location = LocationFactory.create(anonymous=True, elevation_m=2400.0)
        MicroRegionFactory.create(name="Chablais", centroid_location=location)
        WeatherFactory.create(location=location, observed_on=TODAY)

        html = client.get(_card_url(location)).json()["html"]

        assert "Chablais" in html

    def test_carries_a_link_to_the_full_forecast_on_the_same_day(
        self, client: Client, resort_location: Location
    ) -> None:
        """The card hands the detail off rather than holding it.

        The date rides along so the page opens on the day the map was
        showing rather than snapping to today.
        """
        html = client.get(
            _card_url(resort_location), {"date": TODAY.isoformat()}
        ).json()["html"]

        assert f"{_page_url(resort_location)}?date={TODAY.isoformat()}" in html

    def test_does_not_carry_the_chart_the_strip_or_the_hourly_tables(
        self, client: Client, resort_location: Location
    ) -> None:
        """THE POINT OF THE CARD. All three belong to the page.

        They were on it once, and the hourly rows alone were 79% of the
        response — 72.7 kB for a surface opened by tapping a symbol. This
        is what stops them drifting back.
        """
        html = client.get(_card_url(resort_location)).json()["html"]

        assert "forecast-chart" not in html
        assert "-forecast-day" not in html
        assert "-hourly-list" not in html

    def test_says_so_when_there_is_no_reading_for_the_day(
        self, client: Client, resort_location: Location
    ) -> None:
        """A past date before the backfill is normal, not an error."""
        response = client.get(_card_url(resort_location), {"date": "2020-01-01"})

        assert response.status_code == 200
        assert 'data-testid="weather-detail-empty"' in response.json()["html"]

    def test_falls_back_to_today_on_an_unparseable_date(
        self, client: Client, resort_location: Location
    ) -> None:
        """The scrubber is a slider; a bad value costs the wrong day, not a 400."""
        response = client.get(_card_url(resort_location), {"date": "not-a-date"})

        assert response.status_code == 200


@pytest.mark.django_db
class TestForecastPage:
    """The destination: everything the card left out."""

    def test_renders_the_outlook_the_strip_and_the_hourly_chart(
        self, client: Client, resort_location: Location
    ) -> None:
        """All three live here, which is what makes the card's link worth following."""
        response = client.get(_page_url(resort_location), {"date": TODAY.isoformat()})

        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-testid="forecast-chart"' in html
        assert "location-weather-forecast-day" in html
        assert 'data-testid="location-weather-hourly' in html

    def test_the_hourly_table_is_gone(
        self, client: Client, resort_location: Location
    ) -> None:
        """SNOW-786: the chart replaced the 24-row table, it did not join it.

        Both answered "what happens through this day"; the table existed
        only because there was no chart. Rendering both would be the
        duplication this ticket removed, at 24 rows a day.
        """
        html = client.get(
            _page_url(resort_location), {"date": TODAY.isoformat()}
        ).content.decode()

        assert "-hourly-list" not in html
        assert '<span class="font-mono text-text-3 w-12 shrink-0">' not in html

    def test_sets_its_own_page_metadata(
        self, client: Client, resort_location: Location
    ) -> None:
        """A public page states its sharing metadata rather than omitting it."""
        html = client.get(_page_url(resort_location)).content.decode()

        assert 'property="og:title"' in html
        assert 'property="og:description"' in html

    def test_links_on_to_the_resort_and_the_region(self, client: Client) -> None:
        """A location reaching both offers both onward links."""
        location = LocationFactory.create(anonymous=True, elevation_m=1450.0)
        resort = ResortFactory.create(name="Nendaz")
        ResortLocationFactory.create(resort=resort, location=location)
        MicroRegionFactory.create(name="Chablais", centroid_location=location)
        WeatherFactory.create(location=location, observed_on=TODAY)

        html = client.get(_page_url(location)).content.decode()

        assert resort.get_absolute_url() in html
        assert "View bulletin" in html

    def test_says_so_when_there_is_no_reading_for_the_day(
        self, client: Client, resort_location: Location
    ) -> None:
        """An empty page must read as absent data, not as a failure."""
        response = client.get(_page_url(resort_location), {"date": "2020-01-01"})

        assert response.status_code == 200
        assert 'data-testid="location-weather-empty"' in response.content.decode()
