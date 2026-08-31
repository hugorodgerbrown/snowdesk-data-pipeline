"""
tests/public/test_weather_surfaces.py — Tests for the server-rendered weather.

Four surfaces read a ``Weather`` row and render it (SNOW-761):

* the bulletin masthead, from the region's ``centroid_location``;
* the resort page, one block per curated ``Location`` linked to the resort;
* the favourite detail card, from the pin's own ``Location``;
* the location forecast page, from one ``Location``.

**Only the last of those draws the week** (SNOW-783). The resort page and
the favourite card show the day and link out; asserting a day-strip on
either is asserting the defect this ticket removed.

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

import pytest
from django.test import Client
from django.urls import reverse
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
    """The bulletin masthead carries NO Open-Meteo weather (SNOW-784).

    It used to, read off the region's ``centroid_location``. A
    micro-region spans thousands of metres of vertical, so one centroid
    point under a regional heading claims more than it knows. The
    forecaster's own pan-regional prose is the bulletin's weather and
    stays where it is. That prose section is covered by
    ``tests/public/test_bulletin_page.py`` (``snowpack-weather-section``)
    — it is the thing this ticket must NOT remove.
    """

    @freeze_time(MIDDAY)
    def test_a_centroid_row_does_not_reach_the_masthead(self) -> None:
        """Even with a row for the exact page date, no panel renders."""
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
        assert 'data-testid="bulletin-weather-panel"' not in content
        assert "light_snow-day.svg" not in content


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
    def test_the_week_is_not_drawn_here_only_linked(self) -> None:
        """SNOW-783: the day per altitude, and a link out for the week.

        The strip used to be rendered once per curated location, so a
        resort with a village, a mid-station and a peak drew the same
        seven days three times.
        """
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
        # The day is here.
        assert 'data-testid="resort-weather-0-panel"' in content
        # The week is not.
        assert 'data-testid="resort-weather-0-forecast-panel"' not in content
        assert 'data-date="2026-08-31"' not in content
        # But it is one click away, per location.
        assert 'data-testid="resort-weather-0-forecast-link"' in content
        assert reverse("public:location_weather", args=[location.pk]) in content

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

    @freeze_time(MIDDAY)
    def test_the_week_is_not_drawn_here_only_linked(self) -> None:
        """SNOW-783: the card shows the day and links to the week.

        The card is already a scrolling surface; a seven-day strip with
        its hourly tables was most of its height.
        """
        user = UserFactory.create()
        favourite = FavouriteFactory.create(user=user, name="The col")
        WeatherFactory.create(
            location=favourite.location,
            observed_on=datetime.date(2026, 8, 30),
            sunrise=SUNRISE,
            sunset=SUNSET,
            forecast=[_forecast_day("2026-08-31")],
        )

        client = Client()
        client.force_login(user)
        response = client.get(
            reverse("favourites:detail", kwargs={"uuid": favourite.uuid})
        )

        content = response.content.decode()
        assert 'data-testid="favourite-weather-panel"' in content
        assert 'data-testid="favourite-forecast-panel"' not in content
        assert 'data-testid="favourite-card-forecast-link"' in content
        assert (
            reverse("public:location_weather", args=[favourite.location_id]) in content
        )


@pytest.mark.django_db
class TestLocationForecastPage:
    """The location forecast page — the one surface that draws the week."""

    @freeze_time(MIDDAY)
    def test_the_outlook_comes_from_the_same_rows_forecast_column(self) -> None:
        """The multi-day strip is one row's ``forecast[]``, not N rows.

        Moved here from the resort page by SNOW-783, which is where the
        week now lives.
        """
        location = LocationFactory.create(name="Attelas", elevation_m=2200.0)
        # public(), so the page is reachable anonymously.
        ResortLocationFactory.create(resort=ResortFactory.create(), location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
            forecast=[_forecast_day("2026-08-31"), _forecast_day("2026-09-01")],
        )

        response = Client().get(reverse("public:location_weather", args=[location.pk]))

        content = response.content.decode()
        assert 'data-date="2026-08-30"' in content
        assert 'data-date="2026-08-31"' in content
        assert 'data-date="2026-09-01"' in content

    def test_a_favourite_pin_is_reachable_by_its_owner_and_nobody_else(self) -> None:
        """SNOW-783: the card links here, so the owner must get through.

        ``public()`` excludes favourite locations so the map feed cannot
        leak one. That contract is about strangers — the owner following
        their own card's "Full forecast" link is not a leak, and a 404
        there would make the link a dead end.
        """
        owner = UserFactory.create()
        stranger = UserFactory.create()
        favourite = FavouriteFactory.create(user=owner, name="The col")
        url = reverse("public:location_weather", args=[favourite.location_id])

        owner_client = Client()
        owner_client.force_login(owner)
        assert owner_client.get(url).status_code == 200

        stranger_client = Client()
        stranger_client.force_login(stranger)
        assert stranger_client.get(url).status_code == 404

        assert Client().get(url).status_code == 404
