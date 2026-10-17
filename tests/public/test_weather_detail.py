"""
tests/public/test_weather_detail.py — the weather card and the forecast page.

Two surfaces, one estate rule (SNOW-761):

  * ``/api/weather/<short_id>/detail/`` — the card the map opens when a
    weather symbol is tapped. Today's conditions and a link out.
  * ``/weather/<short_id>/`` — the full forecast that link opens: the
    day picker, the selected day's line, and the hourly detail
    (SNOW-789).

The load-bearing assertions are the **privacy** ones. Both filter on
``Location.objects.public()``, and a location reachable only from a
``Favourite`` must 404 on both — otherwise a guessed id hands back the name,
coordinates and elevation of a stranger's private pin. That is a test rather
than a comment in the view for the same reason the feed's version is.

The second theme is the **split of detail between the two**: the card was
carrying the chart, the day strip and 72 hourly rows, which was 79% of its
payload on a surface reached by a tap. Those assertions are what stop that
detail drifting back onto it.

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
        is what stops them drifting back. The one day's meteogram SNOW-1101
        put on the FULL detent is not one of them: it is the map's day,
        and the week stays the page's (``TestCardDetents``).
        """
        html = client.get(_card_url(resort_location)).json()["html"]

        assert "forecast-chart" not in html
        assert "-forecast-day" not in html
        assert "-hourly-list" not in html

    def test_links_out_to_the_week(
        self, client: Client, resort_location: Location
    ) -> None:
        """The card is the map's day; the week is the page's (SNOW-1101)."""
        html = client.get(
            _card_url(resort_location), {"date": TODAY.isoformat()}
        ).json()["html"]

        assert "This week →" in html

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

    def test_a_row_with_hours_rises_to_the_meteogram(
        self, client: Client, resort_location: Location
    ) -> None:
        """FULL is the map's day as a chart, and HALF ends above it."""
        html = client.get(
            _card_url(resort_location), {"date": TODAY.isoformat()}
        ).json()["html"]

        assert _detents(html) == "peek,half,full"
        assert 'data-testid="weather-detail-hourly"' in html
        assert html.index("data-sheet-half") < html.index(
            'data-testid="weather-detail-hourly"'
        )
        # One day's chart, never the week's.
        assert html.count('role="group"') == 1

    def test_a_row_without_hours_stops_at_half(self, client: Client) -> None:
        """No series, no chart, so no FULL to rise to."""
        location = LocationFactory.create(anonymous=True, elevation_m=1450.0)
        MicroRegionFactory.create(name="Chablais", centroid_location=location)
        WeatherFactory.create(location=location, observed_on=TODAY, hourly=None)

        html = client.get(_card_url(location), {"date": TODAY.isoformat()}).json()[
            "html"
        ]

        assert _detents(html) == "peek,half"
        assert 'data-testid="weather-detail-hourly"' not in html

    def test_a_day_with_no_row_stays_at_peek(
        self, client: Client, resort_location: Location
    ) -> None:
        """The line says so, and there is nothing above it."""
        html = client.get(_card_url(resort_location), {"date": "2020-01-01"}).json()[
            "html"
        ]

        assert _detents(html) == "peek"
        assert "No weather recorded" in html

    def test_the_surface_declares_all_three_and_loads_the_legend(self) -> None:
        """The map's sheet offers FULL, and the chart's key has its handler."""
        html = render_to_string("public/partials/_weather_surface.html")

        assert 'data-detents="peek,half,full"' in html
        assert "js/hourly_chart_legend.js" in html


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

    def test_only_the_days_carrying_hours_get_a_meteogram(
        self, client: Client, resort_location: Location
    ) -> None:
        """``selectable`` still means something — just something narrower.

        It no longer decides which cells are controls (all of them are);
        it decides which of them reveals an ``_hourly_chart``. HOURLY_DAYS
        is 2, so five of the seven cells select a day line and nothing
        else, and the markup for those five meteograms is ABSENT rather
        than present and empty.
        """
        html = _main(_page(client, resort_location))

        assert html.count('data-testid="location-weather-hourly-panel"') == 2
        assert 'data-hourly-day="0"' in html
        assert 'data-hourly-day="1"' in html
        for index in range(2, 7):
            assert f'data-hourly-day="{index}"' not in html
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

    def test_the_day_line_carries_freezing_and_daylight_but_not_hi_lo(
        self, client: Client, resort_location: Location
    ) -> None:
        """The selected day is stated once, and it is not the cell again.

        High and low are in the cell the reader just pressed and in the
        meteogram below; a third statement is the duplication the redesign
        removed.
        """
        html = _main(_page(client, resort_location))

        line = html[html.index('data-testid="location-weather-day-line-detail"') :]
        line = line[: line.index("</p>")]
        assert "1800" in line  # freezing level, m
        assert "daylight" in line
        assert "high" not in line
        assert "low" not in line

    def test_the_location_is_named_once_in_the_body(
        self, client: Client, resort_location: Location
    ) -> None:
        """The masthead names the place; nothing below repeats it.

        The layout this replaces re-stated it in the "Today" panel and in
        every section sub-header. The fixture is an anonymous location
        named by its resort, which is the ordinary case — 461 of the 540
        public locations are region centroids and the rest are mostly
        unnamed resort pins.
        """
        html = _main(_page(client, resort_location))

        assert html.count("Nendaz") == 1

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
        line and the meteogram are the whole value of recovering a
        historical day, so they are asserted present rather than merely
        assumed.
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
        # ...and the day and its meteogram are both there.
        assert 'data-testid="location-weather-day-line"' in html
        assert 'data-testid="location-weather-hourly"' in html

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

    def test_the_legend_script_reaches_the_page_that_ships_the_chart(
        self, client: Client, resort_location: Location
    ) -> None:
        """The meteogram's info button needs a listener to do anything.

        ``hourly_chart_legend.js`` was loaded by ``/_components/`` alone,
        so on the one page that actually ships the chart the button sat
        there inert with ``aria-expanded="false"`` and nothing bound to
        it. It is the only script this page needs; everything else here
        is CSS.

        Both halves matter: the tag alone would pass on a page with no
        chart to bind to, and the button alone is what the bug already
        looked like.
        """
        html = _page(client, resort_location)

        assert "js/hourly_chart_legend.js" in html
        assert "data-hourly-chart-legend-open" in html

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

    def test_links_on_to_the_resort_and_the_region(self, client: Client) -> None:
        """A location reaching both offers both onward links.

        They live in the masthead rather than a bottom nav (SNOW-789): 461
        of the 540 public locations are centroids whose only way on is the
        bulletin, so a masthead naming only the resort would strand most
        of this page's visitors.
        """
        location = LocationFactory.create(anonymous=True, elevation_m=1450.0)
        resort = ResortFactory.create(name="Nendaz")
        ResortLocationFactory.create(resort=resort, location=location)
        MicroRegionFactory.create(name="Chablais", centroid_location=location)
        WeatherFactory.create(location=location, observed_on=TODAY)

        masthead = _page(client, location)
        masthead = masthead[masthead.index('data-testid="weather-masthead"') :]
        masthead = masthead[: masthead.index("</header>")]

        assert resort.get_absolute_url() in masthead
        assert "View bulletin" in masthead

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
        assert "1700&nbsp;m" in html  # that day's freezing level, not today's
        assert "4&deg;" in html  # that day's high

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

    def test_a_forward_day_with_hours_rises_to_its_meteogram(
        self, client: Client, resort_location: Location
    ) -> None:
        """Tomorrow carries a series (HOURLY_DAYS is 2), so FULL is its chart."""
        html = client.get(_card_url(resort_location), {"date": _forward(1)}).json()[
            "html"
        ]

        assert _detents(html) == "peek,half,full"
        assert 'data-testid="weather-detail-hourly"' in html

    def test_a_forward_day_without_hours_stops_at_half(
        self, client: Client, resort_location: Location
    ) -> None:
        """Past the hourly horizon there is no chart to rise to."""
        html = client.get(_card_url(resort_location), {"date": _forward(3)}).json()[
            "html"
        ]

        assert _detents(html) == "peek,half"

    def test_a_day_past_the_window_says_the_forecast_does_not_reach_it(
        self, client: Client, resort_location: Location
    ) -> None:
        """Not "nothing was recorded": nothing has been forecast yet."""
        html = client.get(_card_url(resort_location), {"date": _forward(10)}).json()[
            "html"
        ]

        assert _detents(html) == "peek"
        assert "The forecast does not reach this day yet." in html
        assert "No weather recorded" not in html

    def test_links_to_the_page_on_the_same_day(
        self, client: Client, resort_location: Location
    ) -> None:
        """The page opens that day inside today's row (``TestPageForwardDay``)."""
        html = client.get(_card_url(resort_location), {"date": _forward(2)}).json()[
            "html"
        ]

        assert f"{_page_url(resort_location)}?date={_forward(2)}" in html


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

    def test_a_day_past_the_window_opens_on_today(
        self, client: Client, resort_location: Location
    ) -> None:
        """Nothing to select, so the row's own day — still today's week."""
        html = _main(
            client.get(
                _page_url(resort_location), {"date": _forward(10)}
            ).content.decode()
        )

        assert _checked_index(html) == 0
