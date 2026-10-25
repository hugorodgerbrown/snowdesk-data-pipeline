"""
tests/public/test_weather_detail.py — the weather card and the forecast page.

Two surfaces, one estate rule (SNOW-761):

  * ``/api/weather/<short_id>/detail/`` — the sheet the map opens when a
    weather symbol is tapped. Since 24 Oct 2026 the sheet is the page:
    under its peek row it renders the page's own body
    (``_weather_body.html``), the week opened on the map's day.
  * ``/weather/<short_id>/`` — the full forecast that link opens: the
    day picker, the selected day's line, and the hourly detail
    (SNOW-789).

The load-bearing assertions are the **privacy** ones. Both filter on
``Location.objects.public()``, and a location reachable only from a
``Favourite`` must 404 on both — otherwise a guessed id hands back the name,
coordinates and elevation of a stranger's private pin. That is a test rather
than a comment in the view for the same reason the feed's version is.

The second theme is that **the sheet and the page share one body** (Hugo,
24 Oct 2026, reversing SNOW-1101's day/week split): the sheet's assertions
check it renders the page's picker, day lines and meteograms.

The third, added by SNOW-789, is that **the page states each figure once**.
It used to print the same high and low three times over — a "Today" panel,
the day cells, and an outlook chart drawing them as shape — and name the
location again in every sub-header. Several assertions below are counts
for that reason: they fail on a restatement, not just on an absence.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

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


def _forecast(anchor: datetime.date, days: int = 6) -> list[dict[str, Any]]:
    """Build forward days, the first of them carrying an hourly series.

    Mirrors the real shape twice over: ``hourly`` is present on only the
    first FORWARD entry, because the row itself carries day 0's series
    and ``HOURLY_DAYS`` is 2 — a fixture giving every day one would
    misrepresent it — and six forward days plus the row's own is
    ``FORECAST_DAYS``, the seven the day picker's grid is built on. A
    shorter fixture would leave the picker's own shape untested.

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
        if index == 1:
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
def backfilled_location() -> Location:
    """A public location whose day was written by the backfill (SNOW-731).

    The distinguishing feature is ``forecast=None``: the historical endpoint
    serves a stitched timeline, not the outlook as issued that morning, so
    the backfill deliberately leaves the column null. A recovered row is one
    day and only one day.

    That is the discriminator for the forecast page's second shape — no day
    picker at all (SNOW-789) — and it is deliberately not "a past date",
    because the page must never branch on the calendar. ``hourly`` is
    present, so the one day still draws its meteogram.
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
        hourly=_hourly(TODAY),
        forecast=None,
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
    return reverse("api:weather_detail", kwargs={"short_id": location.short_id})


def _page_url(location: Location) -> str:
    """Build the forecast page's URL for a location."""
    return reverse("public:location_weather", kwargs={"short_id": location.short_id})


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
        """An id nothing owns is a 404, not a 500 — in either URL shape."""
        assert client.get("/api/weather/AAAAAAAAAAA/detail/").status_code == 404
        assert client.get("/weather/AAAAAAAAAAA/").status_code == 404
        assert client.get("/api/weather/999999/detail/").status_code == 404
        assert client.get("/weather/999999/").status_code == 404


@pytest.mark.django_db
class TestLegacyIntegerRedirects:
    """The pre-SNOW-797 ``<int:location_id>`` forms 301 to the short id."""

    def test_page_redirects_and_keeps_the_query_string(self, client: Client) -> None:
        """/weather/<pk>/?date=… lands on /weather/<short_id>/?date=…."""
        location = LocationFactory.create()
        ResortLocationFactory.create(location=location)

        response = client.get(f"/weather/{location.pk}/?date=2026-01-14")

        assert response.status_code == 301
        assert response["Location"] == f"/weather/{location.short_id}/?date=2026-01-14"

    def test_card_redirects(self, client: Client) -> None:
        """/api/weather/<pk>/detail/ lands on the short-id endpoint."""
        location = LocationFactory.create()
        ResortLocationFactory.create(location=location)

        response = client.get(f"/api/weather/{location.pk}/detail/")

        assert response.status_code == 301
        assert response["Location"] == f"/api/weather/{location.short_id}/detail/"

    def test_redirects_404_for_a_private_pin(
        self, client: Client, favourite_only_location: Location
    ) -> None:
        """A guessed pk must not confirm a stranger's pin exists."""
        assert client.get(f"/weather/{favourite_only_location.pk}/").status_code == 404
        assert (
            client.get(f"/api/weather/{favourite_only_location.pk}/detail/").status_code
            == 404
        )

    def test_owner_is_redirected_to_their_own_pin(self, client: Client) -> None:
        """The page's owner-visibility rule holds for the legacy form too."""
        location = LocationFactory.create(anonymous=True)
        favourite = FavouriteFactory.create(location=location)
        client.force_login(favourite.user)

        response = client.get(f"/weather/{location.pk}/")

        assert response.status_code == 301
        assert response["Location"] == f"/weather/{location.short_id}/"


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

    def test_header_names_the_height_and_the_day_row_follows(
        self, client: Client, resort_location: Location
    ) -> None:
        """Hugo, 21 Oct: "Belalp · 1934 m", then "Data from <day>"."""
        response = client.get(_card_url(resort_location), {"date": TODAY.isoformat()})
        html = response.json()["html"]
        start = html.index('data-testid="weather-detail-heading"')
        heading = html[start : html.index("</h2>", start)]
        assert "1450" in heading
        row_at = html.index('data-testid="sheet-date-row"')
        assert start < row_at < html.index('data-testid="weather-detail-line"')
        row = html[row_at : html.index("</button>", row_at)]
        assert "Data from" in row
        assert TODAY.strftime("%b %Y") in row

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

    def test_the_kind_line_names_a_weather_point(
        self, client: Client, resort_location: Location
    ) -> None:
        """Every map panel names its kind above its title (25 Oct 2026)."""
        html = client.get(_card_url(resort_location)).json()["html"]
        start = html.index('data-testid="sheet-kind"')
        assert "Weather point" in html[start : html.index("</p>", start)]
        assert start < html.index('data-testid="weather-detail-heading"')
        assert f'data-panel-key="weather:{resort_location.short_id}"' in html
        assert 'data-panel-label="Nendaz"' in html

    def test_where_this_is_names_the_resort_and_its_region(
        self, client: Client, resort_location: Location
    ) -> None:
        """A resort's point offers its resort and that resort's region."""
        resort = resort_location.resort_locations.get().resort
        html = client.get(_card_url(resort_location)).json()["html"]
        start = html.index('data-testid="weather-detail-places"')
        section = html[start : html.index("</section>", start)]
        assert f'data-panel-id="{resort.slug}"' in section
        assert f'data-panel-id="{resort.region.region_id}"' in section
        assert f'href="{resort.region.get_absolute_url()}"' in section
        assert section.index("weather-detail-resort-link") < section.index(
            "weather-detail-region-link"
        )

    def test_where_this_is_links_the_regions_page_for_the_day(
        self, client: Client, resort_location: Location
    ) -> None:
        """Off today the region row opens that day's bulletin page."""
        resort = resort_location.resort_locations.get().resort
        day = TODAY - datetime.timedelta(days=2)
        html = client.get(_card_url(resort_location), {"date": day.isoformat()}).json()[
            "html"
        ]
        assert f'href="{resort.region.get_absolute_url(day)}"' in html

    def test_a_centroid_offers_its_region_alone(self, client: Client) -> None:
        """A region centroid stands for no resort: one region row."""
        location = LocationFactory.create(anonymous=True, elevation_m=2221.0)
        region = MicroRegionFactory.create(
            name="Conthey-Fully", centroid_location=location
        )
        WeatherFactory.create(location=location, observed_on=TODAY)
        html = client.get(_card_url(location)).json()["html"]
        assert 'data-testid="weather-detail-region-link"' in html
        assert f'data-panel-id="{region.region_id}"' in html
        assert 'data-testid="weather-detail-resort-link"' not in html

    def test_a_point_that_is_neither_offers_no_places(self, client: Client) -> None:
        """No resort and no region: the section is omitted, not left empty."""
        location = LocationFactory.create(name="Col du Pillon", elevation_m=1546.0)
        html = render_to_string(
            "public/partials/_weather_detail.html",
            {
                "location": location,
                "heading": "Col du Pillon",
                "target_date": TODAY,
                "place_resort": None,
                "place_region": None,
                "place_region_url": "",
            },
        )
        assert 'data-testid="weather-detail-places"' not in html

    def test_names_a_centroid_by_its_region(self, client: Client) -> None:
        """A region centroid is headed by the region it stands for."""
        location = LocationFactory.create(anonymous=True, elevation_m=2400.0)
        MicroRegionFactory.create(name="Chablais", centroid_location=location)
        WeatherFactory.create(location=location, observed_on=TODAY)

        html = client.get(_card_url(location)).json()["html"]

        assert "Chablais" in html

    def test_renders_the_pages_body(
        self, client: Client, resort_location: Location
    ) -> None:
        """The sheet is the page: its picker, day lines and meteograms."""
        html = client.get(
            _card_url(resort_location), {"date": TODAY.isoformat()}
        ).json()["html"]

        assert 'data-testid="weather-detail-forecast-picker"' in html
        assert html.count('data-testid="weather-detail-forecast-day"') == 7
        assert html.count('data-testid="weather-detail-day-line"') == 7
        assert html.count('data-testid="weather-detail-hourly-panel"') == 2
        assert 'data-testid="weather-detail-provenance"' in html
        assert _checked_index(html) == 0

    def test_its_radios_are_a_group_of_their_own(
        self, client: Client, resort_location: Location
    ) -> None:
        """The map's picker never shares a radio group with a page's."""
        html = client.get(
            _card_url(resort_location), {"date": TODAY.isoformat()}
        ).json()["html"]

        assert 'name="map-weather-day"' in html
        assert 'name="location-weather-day"' not in html

    def test_no_longer_links_out_to_the_week(
        self, client: Client, resort_location: Location
    ) -> None:
        """The week is in the sheet, so the way out to it is gone."""
        html = client.get(
            _card_url(resort_location), {"date": TODAY.isoformat()}
        ).json()["html"]

        assert "This week →" not in html
        assert f"{_page_url(resort_location)}?date=" not in html

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


def _without_places(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the card's point one that is in no resort and no region.

    Every public point is a resort's or a region centroid, so the endpoint
    never serves one with neither; this is how the detent tests read the
    card's own narrowing without the "Where this is" rows lifting it to FULL.
    """
    monkeypatch.setattr(
        "apps.public.views._weather_point_places", lambda location: (None, None)
    )


def _detents(html: str) -> str:
    """Read the detents the card body declares for the phone sheet."""
    match = re.search(r'data-sheet-detents="([^"]*)"', html)
    assert match is not None
    return match.group(1)


@pytest.mark.django_db
class TestCardDetents:
    """The phone sheet's three heights, declared by the body (SNOW-1101).

    PEEK is the day's line, HALF the rest of the card, FULL the day's
    meteogram. The body narrows the list to what it can fill, so a sheet
    never rises to an empty height.
    """

    def test_the_peek_row_carries_the_days_line(
        self, client: Client, resort_location: Location
    ) -> None:
        """High/low, new snow and wind, on the row the reader drags by."""
        html = client.get(
            _card_url(resort_location), {"date": TODAY.isoformat()}
        ).json()["html"]

        match = re.search(
            r'data-testid="weather-detail-line"[^>]*>(.*?)</p>', html, re.DOTALL
        )
        assert match is not None
        line = " ".join(match.group(1).split())
        assert "2&deg;" in line
        assert "-6&deg;" in line
        assert "3 cm new" in line
        assert "22&nbsp;km/h" in line
        assert "data-sheet-peek data-sheet-drag" in html

    def test_a_row_with_hours_rises_to_the_day_strips(
        self, client: Client, resort_location: Location
    ) -> None:
        """HALF ends under the week, FULL is the selected day below it."""
        html = client.get(
            _card_url(resort_location), {"date": TODAY.isoformat()}
        ).json()["html"]

        assert _detents(html) == "peek,half,full"
        assert 'data-testid="weather-detail-strips"' in html
        half = html.index("data-sheet-half")
        assert html.index('data-testid="weather-detail-forecast-picker"') > half
        assert half < html.index('data-testid="weather-detail-day-line"')
        assert half < html.index('data-testid="weather-detail-strips"')

    def test_a_one_day_row_without_hours_stops_at_half(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No picker and no series, so no FULL to rise to."""
        _without_places(monkeypatch)
        location = LocationFactory.create(anonymous=True, elevation_m=1450.0)
        MicroRegionFactory.create(name="Chablais", centroid_location=location)
        WeatherFactory.create(
            location=location, observed_on=TODAY, hourly=None, forecast=None
        )

        html = client.get(_card_url(location), {"date": TODAY.isoformat()}).json()[
            "html"
        ]

        assert _detents(html) == "peek,half"
        assert 'data-testid="weather-detail-strips"' not in html

    def test_a_day_with_no_row_stays_at_peek(
        self,
        client: Client,
        resort_location: Location,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The line says so, and there is nothing above it."""
        _without_places(monkeypatch)
        html = client.get(_card_url(resort_location), {"date": "2020-01-01"}).json()[
            "html"
        ]

        assert _detents(html) == "peek"
        assert "No weather recorded" in html

    def test_a_day_with_no_row_still_rises_to_its_places(
        self, client: Client, resort_location: Location
    ) -> None:
        """The "Where this is" rows sit below the peek, so FULL must reach them."""
        html = client.get(_card_url(resort_location), {"date": "2020-01-01"}).json()[
            "html"
        ]

        assert 'data-testid="weather-detail-places"' in html
        assert _detents(html) == "peek,half,full"

    def test_the_surface_declares_all_three_and_loads_the_legend(self) -> None:
        """The map's sheet offers FULL, and the chart's key has its handler."""
        html = render_to_string("public/partials/_weather_surface.html")

        assert 'data-detents="peek,half,full"' in html
        assert "js/hourly_chart_legend.js" in html


@pytest.mark.django_db
class TestCardDayStrips:
    """The sheet draws each day as linked strips, not the meteogram (SNOW-1219)."""

    def _html(self, client: Client, location: Location) -> str:
        """Fetch the sheet on the fixtures' own day."""
        html: str = client.get(_card_url(location), {"date": TODAY.isoformat()}).json()[
            "html"
        ]
        return html

    def test_days_with_hours_get_strips_and_the_rest_figures(
        self, client: Client, resort_location: Location
    ) -> None:
        """Two days carry a series; the other five open their daily figures."""
        html = self._html(client, resort_location)

        assert html.count('data-testid="weather-detail-strips"') == 2
        assert html.count('data-testid="weather-detail-figures"') == 5
        assert "weather-detail-hourly-legend-open" not in html

    def test_one_selector_with_eight_blocks(
        self, client: Client, resort_location: Location
    ) -> None:
        """Eight block buttons per day, exactly one pressed."""
        strips = self._html(client, resort_location).split(
            'data-testid="weather-detail-strips"'
        )[1]
        first_day = strips.split('data-testid="weather-detail-strips"')[0]

        assert first_day.count('data-testid="weather-detail-strips-block-button"') == 8
        assert first_day.count('aria-pressed="true"') == 1
        assert "--block: 3" in first_day

    def test_selector_carries_hour_dots_and_the_night(
        self, client: Client, resort_location: Location
    ) -> None:
        """Sixteen hour dots per day, and the night shaded either side of the light."""
        strips = self._html(client, resort_location).split(
            'data-testid="weather-detail-strips"'
        )[1]
        first_day = strips.split('data-testid="weather-detail-strips"')[0]

        assert first_day.count('data-testid="weather-detail-strips-hour-dot"') == 16
        assert 'data-testid="weather-detail-strips-night-before"' in first_day
        assert 'data-testid="weather-detail-strips-night-after"' in first_day

    def test_four_strips_each_with_a_figure_row(
        self, client: Client, resort_location: Location
    ) -> None:
        """Temperature, freezing level, snow / rain and wind, figures under each."""
        html = self._html(client, resort_location)

        for strip in ("temp", "freezing", "precip", "wind"):
            assert f'data-testid="weather-detail-strips-{strip}-header"' in html
            assert f'data-testid="weather-detail-strips-{strip}-figures"' in html

    def test_each_strip_has_one_band_over_plot_and_figures(
        self, client: Client, resort_location: Location
    ) -> None:
        """One band per strip, and it sits outside the plot, spanning both rows.

        A band inside the plot plus a tinted figure cell was two shapes: a
        seam where the rounded corners met, and two timings when it moved.
        """
        strips = self._html(client, resort_location).split(
            'data-testid="weather-detail-strips"'
        )[1]

        assert strips.count('class="day-strips-band col-start-2 row-span-2') == 4
        plot = strips[strips.index('class="day-strips-plot') :]
        plot = plot[: plot.index("</svg>")]
        assert "day-strips-band" not in plot

    def test_the_snow_rain_title_is_its_own_colour_key(
        self, client: Client, resort_location: Location
    ) -> None:
        """Snow and Rain each carry a square in the fill their bars use."""
        html = self._html(client, resort_location)

        key = html[html.index('data-testid="weather-detail-strips-precip-key"') :]
        key = key[: key.index("</span>\n", key.index("bg-chart-precip"))]
        assert key.index("Snow") < key.index("bg-chart-snow")
        assert key.index("bg-chart-snow") < key.index("Rain")
        assert key.index("Rain") < key.index("bg-chart-precip")

    def test_only_the_timeline_scrubs(
        self, client: Client, resort_location: Location
    ) -> None:
        """A chart is not a control: a touch on one is a scroll, not a time."""
        strips = self._html(client, resort_location).split(
            'data-testid="weather-detail-strips"'
        )[1]

        assert "data-day-strips-track" in strips
        assert "data-day-strips-plot" not in strips
        assert "cursor-pointer touch-pan-y" not in strips

    def test_the_wind_arrows_are_labelled_with_a_compass(
        self, client: Client, resort_location: Location
    ) -> None:
        """The arrow row's gutter carries the compass mark, as ticks label plots."""
        html = self._html(client, resort_location)

        gutter = html[html.index('data-testid="weather-detail-strips-wind-compass"') :]
        assert "<circle" in gutter[: gutter.index("</span>")]

    def test_the_elevation_is_named_by_its_height_alone(
        self, client: Client, resort_location: Location
    ) -> None:
        """The rule on the freezing strip says "1,450 m", not "you"."""
        html = self._html(client, resort_location)

        assert 'data-testid="weather-detail-strips-elevation"' in html
        assert "1,450 m" in html
        assert ">you" not in html

    def test_a_later_day_says_when_its_hours_arrive(
        self, client: Client, resort_location: Location
    ) -> None:
        """Day 3 gets its hours when it is tomorrow: the day after TODAY."""
        html = self._html(client, resort_location)

        tomorrow = (TODAY + datetime.timedelta(days=1)).strftime("%A")
        assert f"Hour by hour from {tomorrow}." in html

    def test_the_sheets_day_line_is_brief(
        self, client: Client, resort_location: Location
    ) -> None:
        """The strips and figures state the freezing level; daylight stays."""
        html = self._html(client, resort_location)

        line = html[html.index('data-testid="weather-detail-day-line-detail"') :]
        line = line[: line.index("</p>")]
        assert "daylight" in line
        assert "Freezing" not in line

    def test_the_surface_loads_the_strips_scripts(self) -> None:
        """The selector needs its two modules, core first."""
        html = render_to_string("public/partials/_weather_surface.html")

        assert html.index("js/day_strips_core.js") < html.index("js/day_strips.js")

    def test_the_page_draws_the_same_strips(
        self, client: Client, resort_location: Location
    ) -> None:
        """The page is the sheet (Hugo, 25 Oct 2026): strips, no meteogram."""
        html = _page(client, resort_location)

        assert "data-day-strips" in html
        assert 'data-testid="location-weather-hourly"' not in html
        assert "js/day_strips.js" in html


def _page(client: Client, location: Location) -> str:
    """Fetch the forecast page for ``location`` on the fixtures' own day.

    The date rides in every time. Without it the view falls back to the
    real today, which has no fixture row and renders the "nothing
    recorded" shape — a page that is legitimately empty, and a test that
    passes for the wrong reason.

    Args:
        client: The test client.
        location: The location whose page to fetch.

    Returns:
        The rendered HTML.

    """
    return client.get(_page_url(location), {"date": TODAY.isoformat()}).content.decode()


def _main(html: str) -> str:
    """Return just the page's ``<main>``, without the head or the chrome.

    Every "appears once" assertion below is about the page body. The
    ``<head>`` states the location's name several times over by design —
    ``<title>``, ``og:title``, ``og:description`` — and counting those
    would be counting the metadata contract rather than the layout.

    Args:
        html: The full rendered page.

    Returns:
        The substring from ``<main`` to the closing ``</main>``.

    """
    body = html[html.index("<main") :]
    return body[: body.index("</main>")]


@pytest.mark.django_db
class TestForecastPage:
    """The destination, rebuilt around the day picker (SNOW-789).

    Five regions — masthead, picker, selected-day line, meteogram,
    provenance — and three page shapes discriminated by whether the row
    carries forward days, never by the date.
    """

    def test_a_live_row_renders_a_cell_per_day_each_one_a_control(
        self, client: Client, resort_location: Location
    ) -> None:
        """Seven cells, today leftmost, every one of them pressable.

        THE BEHAVIOURAL CHANGE OF THE TICKET. The strip this replaces gave
        a radio only to the two days carrying an hourly series, so five of
        seven cells did nothing when pressed.
        """
        response = client.get(_page_url(resort_location), {"date": TODAY.isoformat()})

        assert response.status_code == 200
        html = _main(response.content.decode())
        assert html.count('data-testid="location-weather-forecast-day"') == 7
        assert html.count('name="location-weather-day"') == 7
        for index in range(7):
            assert f'data-day-index="{index}"' in html
        # Today leads, and the six forward days follow it in order.
        dates = re.findall(r'data-date="(\d{4}-\d{2}-\d{2})"', html)
        assert dates == [
            (TODAY + datetime.timedelta(days=offset)).isoformat() for offset in range(7)
        ]

    def test_only_the_days_carrying_hours_get_strips(
        self, client: Client, resort_location: Location
    ) -> None:
        """``selectable`` still means something — just something narrower.

        It no longer decides which cells are controls (all of them are);
        it decides which of them reveals day strips. HOURLY_DAYS is 2, so
        five of the seven cells reveal the day's figures instead, as the
        sheet does (SNOW-1219).
        """
        html = _main(_page(client, resort_location))

        assert html.count('data-testid="location-weather-hourly-panel"') == 2
        assert html.count('data-testid="location-weather-figures-panel"') == 5
        # Every day still gets its own line, chart or no chart.
        for index in range(7):
            assert f'data-day-line="{index}"' in html

    def test_the_first_cell_states_that_it_is_today(
        self, client: Client, resort_location: Location
    ) -> None:
        """``aria-current="date"`` means THE CURRENT DATE, not "selected".

        The radio's own checked state carries selection. Day 0 is today by
        construction — the window starts on the row's own day — so this is
        static on the first cell, and exactly one cell has it.
        """
        html = _main(_page(client, resort_location))

        assert html.count('aria-current="date"') == 1
        first_cell = html[: html.index('data-day-cell="1"')]
        assert 'aria-current="date"' in first_cell

    def test_each_cell_is_labelled_as_a_sentence(
        self, client: Client, resort_location: Location
    ) -> None:
        """A cell is three unlabelled numbers to a screen reader otherwise.

        The visible cell is a weekday, an icon and two temperatures; read
        aloud that is "Mon 2 -5". The radio carries the sentence instead,
        and the icon is ``aria-hidden`` so it does not read the condition
        twice.
        """
        html = _main(_page(client, resort_location))

        labels = re.findall(r'aria-label="([^"]+)"', html)
        cell_labels = [label for label in labels if "high" in label]
        assert len(cell_labels) == 7
        assert cell_labels[0].startswith(TODAY.strftime("%A"))
        assert "degrees" in cell_labels[0]
        # The icon says nothing a screen reader has to hear twice.
        assert 'alt=""' in html

    def test_the_day_line_carries_daylight_but_not_hi_lo(
        self, client: Client, resort_location: Location
    ) -> None:
        """The selected day is stated once, and it is not the cell again.

        High and low are in the cell the reader just pressed and in the
        strips below; the freezing level is the strips' own row, so the
        line is brief, as it is in the sheet (SNOW-1219).
        """
        html = _main(_page(client, resort_location))

        line = html[html.index('data-testid="location-weather-day-line-detail"') :]
        line = line[: line.index("</p>")]
        assert "Freezing" not in line
        assert "daylight" in line
        assert "high" not in line
        assert "low" not in line

    def test_the_location_is_named_once_in_the_body(
        self, client: Client, resort_location: Location
    ) -> None:
        """The masthead names the place; only the way onward repeats it.

        The layout this replaced re-stated it in the "Today" panel and in
        every section sub-header. The fixture is an anonymous location
        named by its resort, which is the ordinary case, so the one other
        mention is the "Where this is" row leading to that resort — the
        sheet's row too.
        """
        html = _main(_page(client, resort_location))

        assert html.count("Nendaz") == 2
        places = html[html.index('data-testid="location-weather-places"') :]
        assert "Nendaz" in places

    def test_a_backfilled_row_has_no_picker_but_still_has_its_day(
        self, client: Client, backfilled_location: Location
    ) -> None:
        """The second page shape: one day, recovered, and nothing to pick.

        SNOW-731 leaves ``forecast`` null on a backfilled row on purpose —
        the upstream serves a stitched timeline, which is not the same
        object as "what the following week looked like on one particular
        morning". A null column yields exactly one day, so there is no
        picker and therefore no radio, which means the day line MUST
        render with no ``.forecast-day-line`` hiding class: the ``:has()``
        reveal could never match it and the page would be blank below the
        masthead.

        The week is what the backfill costs, and only the week. The day
        line and its strips are the whole value of recovering a historical
        day, so they are asserted present rather than merely assumed.
        """
        response = client.get(
            _page_url(backfilled_location), {"date": TODAY.isoformat()}
        )

        assert response.status_code == 200
        html = _main(response.content.decode())
        assert 'data-testid="location-weather-forecast-picker"' not in html
        assert 'name="location-weather-day"' not in html
        assert "forecast-day-line" not in html
        assert "data-day-line=" not in html
        # ...and the day and its strips are both there.
        assert 'data-testid="location-weather-day-line"' in html
        assert "data-day-strips" in html

    def test_every_cell_carries_the_selection_marker(
        self, client: Client, resort_location: Location
    ) -> None:
        """The second channel is markup in all seven cells, not just one.

        Only the CSS fills the checked cell's bar, so the bar has to exist
        everywhere or selecting a day would add an element and change that
        cell's height — the twitch the handoff's "nothing else changes"
        rule is there to prevent.
        """
        html = _main(_page(client, resort_location))

        assert html.count('data-testid="location-weather-forecast-marker"') == 7

    def test_the_strip_scripts_reach_the_page_that_ships_the_strips(
        self, client: Client, resort_location: Location
    ) -> None:
        """The strips' selected block follows a tap only with their scripts.

        Both halves matter: the tags alone would pass on a page with no
        strips to bind to, and the strips alone would be inert. The
        meteogram's legend script went with the meteogram.
        """
        html = _page(client, resort_location)

        assert "js/day_strips_core.js" in html
        assert "js/day_strips.js" in html
        assert "data-day-strips" in html
        assert "js/hourly_chart_legend.js" not in html

    def test_the_page_states_when_it_was_fetched_and_who_from(
        self, client: Client, resort_location: Location
    ) -> None:
        """Provenance closes the page: a time, and Open-Meteo."""
        html = _main(_page(client, resort_location))

        provenance = html[html.index('data-testid="location-weather-provenance"') :]
        assert "Open-Meteo" in provenance[: provenance.index("</p>")]

    def test_the_outlook_chart_and_the_old_strip_are_gone(
        self, client: Client, resort_location: Location
    ) -> None:
        """Both restated figures the cells and the meteogram already carry.

        The chart drew as SHAPE the same seven highs and lows the cells
        print as numbers, and the strip's testid belonged to a partial
        this ticket deleted.
        """
        html = client.get(_page_url(resort_location)).content.decode()

        assert "forecast-chart" not in html
        assert "-forecast-panel" not in html
        assert "forecast-day-strip" not in html

    def test_the_hourly_table_is_gone(
        self, client: Client, resort_location: Location
    ) -> None:
        """SNOW-786: the chart replaced the 24-row table, it did not join it.

        Both answered "what happens through this day"; the table existed
        only because there was no chart. Rendering both would be the
        duplication that ticket removed, at 24 rows a day.
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

    def test_a_resort_pin_is_named_by_its_resort_without_a_link(
        self, client: Client
    ) -> None:
        """SNOW-1144: a pin Location with no ResortLocation still names its resort."""
        resort = ResortFactory.create(name="Ovronnaz", latitude=46.2, longitude=7.1)
        location = resort.location
        assert location is not None
        WeatherFactory.create(location=location, observed_on=TODAY)

        html = _page(client, location)

        assert "Ovronnaz" in html
        assert resort.get_absolute_url() in html
        card = client.get(_card_url(location)).json()["html"]
        assert "Ovronnaz" in card

    def test_links_on_to_the_resort_and_the_region(self, client: Client) -> None:
        """A location reaching both offers both ways onward.

        They are the sheet's "Where this is" rows (Hugo, 25 Oct 2026), which
        replaced the masthead's "View resort →" / "View bulletin →": 461 of
        the 540 public locations are centroids whose only way on is the
        bulletin, so both must stay.
        """
        location = LocationFactory.create(anonymous=True, elevation_m=1450.0)
        resort = ResortFactory.create(name="Nendaz")
        ResortLocationFactory.create(resort=resort, location=location)
        MicroRegionFactory.create(name="Chablais", centroid_location=location)
        WeatherFactory.create(location=location, observed_on=TODAY)

        html = _page(client, location)
        places = html[html.index('data-testid="location-weather-places"') :]
        places = places[: places.index("</section>")]

        assert resort.get_absolute_url() in places
        assert 'data-testid="location-weather-region-link"' in places

    def test_says_so_when_there_is_no_reading_for_the_day(
        self, client: Client, resort_location: Location
    ) -> None:
        """The third page shape. An empty page must read as absent data.

        Normal for a past date until the historical backfill lands, so it
        is a sentence rather than a blank page or an error.
        """
        response = client.get(_page_url(resort_location), {"date": "2020-01-01"})

        assert response.status_code == 200
        assert 'data-testid="location-weather-empty"' in response.content.decode()


# Midday on the fixtures' own day, so ``timezone.localdate()`` is TODAY and
# the fixture row's ``forecast`` entries are the days after it.
_ON_TODAY = freeze_time("2026-01-12T11:00:00+00:00")


def _forward(days: int) -> str:
    """Return the ISO date ``days`` after TODAY."""
    return (TODAY + datetime.timedelta(days=days)).isoformat()


@pytest.mark.django_db
@_ON_TODAY
class TestCardForwardDay:
    """A day after today reads today's forecast, marked as one (SNOW-1105).

    The fetch writes no row ahead of today, so the card used to say "No
    weather recorded" for every forward day the map's weather layer drew.
    """

    def test_reads_the_forecast_entry_for_the_day(
        self, client: Client, resort_location: Location
    ) -> None:
        """Day 2's own figures, from today's row's ``forecast`` column."""
        html = client.get(_card_url(resort_location), {"date": _forward(2)}).json()[
            "html"
        ]

        assert "No weather recorded" not in html
        assert 'data-testid="weather-detail-empty"' not in html
        assert "4&deg;" in html  # that day's high
        assert _checked_index(html) == 2  # the week, opened on that day

    def test_is_marked_as_a_forecast_with_when_it_was_issued(
        self, client: Client, resort_location: Location
    ) -> None:
        """Hugo's condition: a forward day shows, as long as it says so."""
        html = client.get(_card_url(resort_location), {"date": _forward(2)}).json()[
            "html"
        ]

        assert 'data-testid="weather-detail-forecast-tag"' in html
        assert 'data-testid="weather-detail-forecast-note"' in html
        assert "Forecast issued" in html

    def test_today_is_not_marked_as_a_forecast(
        self, client: Client, resort_location: Location
    ) -> None:
        """The tag means "after today", so today's card carries none."""
        html = client.get(
            _card_url(resort_location), {"date": TODAY.isoformat()}
        ).json()["html"]

        assert "weather-detail-forecast-tag" not in html
        assert "weather-detail-forecast-note" not in html

    def test_a_forward_day_with_hours_rises_to_its_strips(
        self, client: Client, resort_location: Location
    ) -> None:
        """Tomorrow carries a series (HOURLY_DAYS is 2), so FULL is its strips."""
        html = client.get(_card_url(resort_location), {"date": _forward(1)}).json()[
            "html"
        ]

        assert _detents(html) == "peek,half,full"
        assert 'data-testid="weather-detail-strips"' in html

    def test_a_forward_day_without_hours_still_rises_to_its_day(
        self, client: Client, resort_location: Location
    ) -> None:
        """Past the hourly horizon FULL is the day's line, with no chart."""
        html = client.get(_card_url(resort_location), {"date": _forward(3)}).json()[
            "html"
        ]

        assert _detents(html) == "peek,half,full"
        assert _checked_index(html) == 3

    def test_a_day_past_the_window_says_the_forecast_does_not_reach_it(
        self, client: Client, resort_location: Location
    ) -> None:
        """Not "nothing was recorded": nothing has been forecast yet."""
        html = client.get(_card_url(resort_location), {"date": _forward(10)}).json()[
            "html"
        ]

        # Nothing to show for the day, but the resort's place rows still
        # sit below the peek.
        assert _detents(html) == "peek,half,full"
        assert "The forecast does not reach this day yet." in html
        assert "No weather recorded" not in html


def _checked_index(html: str) -> int:
    """Return the ``data-day-index`` of the one checked picker cell."""
    checked = re.findall(r'data-day-index="(\d)"[^>]*\bchecked\b', html)
    assert len(checked) == 1
    return int(checked[0])


@pytest.mark.django_db
@_ON_TODAY
class TestPageForwardDay:
    """``?date=`` naming a forward day selects it in today's row (SNOW-1105)."""

    def test_opens_on_the_requested_day(
        self, client: Client, resort_location: Location
    ) -> None:
        """Today's week, with the requested day's cell checked."""
        html = _main(
            client.get(
                _page_url(resort_location), {"date": _forward(3)}
            ).content.decode()
        )

        assert 'data-testid="location-weather-empty"' not in html
        assert _checked_index(html) == 3

    def test_today_opens_on_the_first_cell(
        self, client: Client, resort_location: Location
    ) -> None:
        """No date, or today, keeps the picker's default."""
        html = _main(client.get(_page_url(resort_location)).content.decode())

        assert _checked_index(html) == 0

    def test_a_day_past_the_window_says_so(
        self, client: Client, resort_location: Location
    ) -> None:
        """Nothing to show, and the page says so — as the sheet does.

        Opening today's week under a day the forecast does not reach would
        state some other day's figures as if they were that day's.
        """
        html = _main(
            client.get(
                _page_url(resort_location), {"date": _forward(10)}
            ).content.decode()
        )

        assert 'name="location-weather-day"' not in html
        assert "The forecast does not reach this day yet." in html


@pytest.mark.django_db
@_ON_TODAY
class TestPageIsTheSheet:
    """The forecast page renders the map sheet's content (Hugo, 25 Oct 2026).

    It keeps its own frame — the ``<h1>``, the meta and canonical, the
    another-day strip — and nothing else of its own.
    """

    def test_the_page_and_the_sheet_share_the_header_and_summary(
        self, client: Client, resort_location: Location
    ) -> None:
        """The kind, the name as the h1, and the day's figures line."""
        html = _main(client.get(_page_url(resort_location)).content.decode())

        assert 'data-testid="weather-masthead-kind"' in html
        assert "<h1" in html
        assert 'data-testid="location-weather-line"' in html
        # The page has no date row, so the line names its own day: picking
        # another in the week must not leave these figures unattributed.
        assert 'data-testid="location-weather-date"' in html
        assert "Mon 12 Jan" in html
        # The sheet's own × and date row stay in the sheet.
        assert 'data-action="dismiss"' not in html

    def test_a_forward_day_is_marked_as_a_forecast(
        self, client: Client, resort_location: Location
    ) -> None:
        """The summary line's tag and the issued note, as in the sheet."""
        html = _main(
            client.get(
                _page_url(resort_location), {"date": _forward(2)}
            ).content.decode()
        )

        assert 'data-testid="location-weather-forecast-tag"' in html
        assert 'data-testid="location-weather-forecast-note"' in html

    def test_another_day_says_so_and_links_back_to_today(
        self, client: Client, resort_location: Location
    ) -> None:
        """Off today the strip under the nav links to the bare URL."""
        html = client.get(
            _page_url(resort_location), {"date": _forward(2)}
        ).content.decode()

        strip = html[html.index('data-testid="another-day-strip"') :]
        strip = strip[: strip.index("</div>")]
        assert f'href="{resort_location.get_absolute_url()}"' in strip

    def test_today_has_no_another_day_strip(
        self, client: Client, resort_location: Location
    ) -> None:
        """The bare page is today, and says nothing about it."""
        html = client.get(_page_url(resort_location)).content.decode()

        assert 'data-testid="another-day-strip"' not in html

    def test_the_canonical_is_still_the_bare_url_for_today(
        self, client: Client, resort_location: Location
    ) -> None:
        """The deep-link and SEO role is unchanged."""
        html = client.get(_page_url(resort_location)).content.decode()

        base = settings.SITE_BASE_URL.rstrip("/")
        canonical = f"{base}{resort_location.get_absolute_url()}"
        assert f'<link rel="canonical" href="{canonical}">' in html
