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
import pathlib
import re

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone
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


def _hourly_series(date: str) -> list[dict[str, object]]:
    """Build a full 24-hour series for one day.

    Enough of a series for ``build_hourly_chart`` to return geometry —
    it needs at least the temperatures to resolve a vertical scale.

    Args:
        date: The day's ISO date.

    Returns:
        Twenty-four hourly rows.

    """
    return [
        {
            "time": f"{date}T{hour:02d}:00",
            "temperature_2m": -2.0 + hour * 0.25,
            "precipitation": 0.2,
            "snowfall": 0.5,
            "wind_speed_10m": 12.0,
            "wind_gusts_10m": 24.0,
            "wind_direction_10m": 270.0,
            "freezing_level_height": 2100.0,
        }
        for hour in range(24)
    ]


def _forecast_day(
    date: str,
    weather_code: int = 71,
    hourly: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build one ``forecast[]`` entry, optionally with a nested hourly series.

    Args:
        date: The forward day's ISO date.
        weather_code: The day's WMO code.
        hourly: The day's hourly rows. Omitted entirely when None, which is
            the real shape past ``HOURLY_DAYS`` — the key is absent, not
            null.

    Returns:
        The entry dict.

    """
    if hourly is not None:
        return {**_forecast_day(date, weather_code), "hourly": hourly}
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
        assert "light_snow.svg" not in content


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
        assert reverse("public:location_weather", args=[location.short_id]) in content

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
            reverse("favourites:card", kwargs={"uuid": favourite.uuid}),
            HTTP_HX_REQUEST="true",
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
            reverse("favourites:card", kwargs={"uuid": favourite.uuid}),
            HTTP_HX_REQUEST="true",
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
            reverse("favourites:card", kwargs={"uuid": favourite.uuid}),
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        assert 'data-testid="favourite-weather-panel"' in content
        assert 'data-testid="favourite-forecast-panel"' not in content
        assert 'data-testid="favourite-card-forecast-link"' in content
        assert favourite.location is not None
        assert favourite.location.get_absolute_url() in content


@pytest.mark.django_db
class TestLocationForecastPage:
    """The location forecast page — the one surface that draws the week."""

    @freeze_time(MIDDAY)
    def test_the_outlook_comes_from_the_same_rows_forecast_column(self) -> None:
        """The picker's cells are one row's ``forecast[]``, not N rows.

        Moved here from the resort page by SNOW-783, which is where the
        week now lives; the cells are the day picker's since SNOW-789.
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

        response = Client().get(
            reverse("public:location_weather", args=[location.short_id])
        )

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
        assert favourite.location is not None
        url = favourite.location.get_absolute_url()

        owner_client = Client()
        owner_client.force_login(owner)
        assert owner_client.get(url).status_code == 200

        stranger_client = Client()
        stranger_client.force_login(stranger)
        assert stranger_client.get(url).status_code == 404

        assert Client().get(url).status_code == 404

    @freeze_time(MIDDAY)
    def test_every_day_is_a_control_and_only_two_carry_a_meteogram(self) -> None:
        """SNOW-789: seven cells, seven radios, two meteograms.

        The strip this replaces gave a radio only to the days carrying an
        hourly series, so five of seven columns were inert and the week
        could not be browsed. ``selectable`` survives, but it now decides
        only whether the selected day reveals a meteogram — which is why
        the control count and the panel count deliberately disagree here.
        """
        location = LocationFactory.create(name="Attelas", elevation_m=2200.0)
        ResortLocationFactory.create(resort=ResortFactory.create(), location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
            hourly=_hourly_series("2026-08-30"),
            forecast=[
                _forecast_day("2026-08-31", hourly=_hourly_series("2026-08-31")),
                _forecast_day("2026-09-01"),
                _forecast_day("2026-09-02"),
            ],
        )

        html = (
            Client()
            .get(reverse("public:location_weather", args=[location.short_id]))
            .content.decode()
        )

        # Four days in the row, so four cells and four controls.
        assert html.count('data-testid="location-weather-forecast-day"') == 4
        assert html.count('name="location-weather-day"') == 4
        for index in range(4):
            assert f'data-day-index="{index}"' in html
            assert f'data-day-line="{index}"' in html
        # Only the two carrying a series get a meteogram.
        assert html.count('data-testid="location-weather-hourly-panel"') == 2
        # Exactly one is checked on load, so the page is never blank.
        # Counted on the input tag: "checked" also appears inside the
        # peer-checked: utilities on every cell's class string.
        checked = re.findall(r"<input\b[^>]*\bchecked\b[^>]*>", html)
        assert len(checked) == 1

    @freeze_time(MIDDAY)
    def test_a_day_past_the_horizon_renders_no_chart_region_at_all(self) -> None:
        """An absent meteogram is absent markup, not an empty frame.

        Selecting day 3 leaves the page with a day line and nothing under
        it. A hidden-but-present panel would ship a chart's worth of SVG
        for a day that has no hours to draw.
        """
        location = LocationFactory.create()
        ResortLocationFactory.create(resort=ResortFactory.create(), location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
            hourly=_hourly_series("2026-08-30"),
            forecast=[_forecast_day("2026-08-31"), _forecast_day("2026-09-01")],
        )

        html = (
            Client()
            .get(reverse("public:location_weather", args=[location.short_id]))
            .content.decode()
        )

        # Three cells, three controls, three day lines...
        assert html.count('name="location-weather-day"') == 3
        assert html.count('data-day-line="2"') == 1
        # ...and one meteogram, for the only day carrying a series. The
        # panel for day 2 is ABSENT, not present-and-empty.
        assert html.count('data-testid="location-weather-hourly-panel"') == 1
        assert 'data-hourly-day="0"' in html
        assert 'data-hourly-day="2"' not in html
        # No disabled control anywhere: every cell is pressable now.
        assert not re.findall(r"<input\b[^>]*\bdisabled\b[^>]*>", html)

    def test_the_reveal_rule_is_adjacent_not_general_sibling(self) -> None:
        """The regression the `~`-vs-`:has()` trap would produce.

        Tailwind's ``peer-checked:`` compiles to the GENERAL sibling
        combinator, so a naive reveal shows the checked panel and every
        panel after it — right on day one, both charts on day two. The
        rules are hand-written in ``src/css/main.css`` for that reason,
        and this asserts nobody has swapped them back for the utility.
        """
        css = (
            pathlib.Path(__file__).resolve().parents[2] / "src" / "css" / "main.css"
        ).read_text()

        selector_block = css[css.index(".forecast-hourly-panel {") :]
        # Every reveal pairs an explicit day index on both ends — for the
        # meteogram (SNOW-787) and for the day line (SNOW-789).
        assert 'input[data-day-index="0"]:checked' in selector_block
        assert '.forecast-hourly-panel[data-hourly-day="0"]' in selector_block
        assert '.forecast-day-line[data-day-line="6"]' in selector_block
        # And both are hidden by default, so an unmatched index shows
        # nothing rather than everything.
        assert ".forecast-hourly-panel {\n  display: none;\n}" in css
        assert ".forecast-day-line {\n  display: none;\n}" in css

    def test_the_selected_day_is_not_signalled_by_colour_alone(self) -> None:
        """The checked cell carries a filled bar as well as an accent border.

        The handoff specified the border alone and justified it by saying
        ``aria-current`` moved with the selection. It does not — the
        attribute means THE CURRENT DATE, so it is static on day 0 — which
        left a reader who cannot separate the two border colours with no
        signal at all.

        Asserted against the rule rather than a rendered page because the
        fill is CSS: the marker is in the markup either way, and only the
        rule says which cell gets it. The bar is a fixed-size element whose
        FILL changes, so stepping along the week never resizes a cell.
        """
        css = (
            pathlib.Path(__file__).resolve().parents[2] / "src" / "css" / "main.css"
        ).read_text()

        assert (
            ".forecast-day-picker input:checked ~ label .weather-day-marker {\n"
            "  background-color: var(--color-accent);\n"
            "}"
        ) in css

    @freeze_time(MIDDAY)
    def test_each_control_is_wrapped_with_only_its_own_cell(self) -> None:
        """The other half of the general-sibling trap (SNOW-787).

        ``peer-checked:`` is ``~``, so with every input and label flat in
        the picker the checked day's input matches every LATER cell's
        label too and highlights the rest of the week. Wrapping each
        input/label pair bounds the ``~`` to one pair. CSS is not
        evaluated here, so this asserts the structure the rule depends on:
        no wrapper may hold two controls.

        SNOW-789 made this sharper — every cell has a control now, so
        every wrapper holds exactly one rather than at most one.
        """
        location = LocationFactory.create()
        ResortLocationFactory.create(resort=ResortFactory.create(), location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
            hourly=_hourly_series("2026-08-30"),
            forecast=[
                _forecast_day("2026-08-31", hourly=_hourly_series("2026-08-31")),
                _forecast_day("2026-09-01"),
            ],
        )

        html = (
            Client()
            .get(reverse("public:location_weather", args=[location.short_id]))
            .content.decode()
        )

        picker = html[html.index("forecast-day-picker") :]
        picker = picker[: picker.index("</fieldset>")]
        # One wrapper per cell, and no wrapper holds two controls — so a
        # checked input can never reach a second cell's label.
        wrappers = re.split(r"<div data-day-cell=", picker)[1:]
        assert len(wrappers) == 3
        for wrapper in wrappers:
            assert wrapper.count("<label") == 1
            assert len(re.findall(r"<input\b", wrapper)) == 1


@pytest.mark.django_db
class TestIconHalo:
    """The ``.weather-icon`` hook reaches every server-rendered icon.

    SNOW-791: a set whose cloud is pale reads by its silhouette rather than
    its fill, so it needs an edge on a light surface. The map dilates one in
    canvas; the three server-rendered surfaces get it from ``.weather-icon``
    in ``src/css/main.css``.

    The class is the whole mechanism on this side, and it lives in a
    template attribute where nothing else would miss it: a refactor that
    dropped it would leave a pale cloud on a near-white plate with every
    other assertion — filename, testid, layout — still passing. So it is
    asserted per surface, the same way the filenames are.

    **These pin ``?icons=yr``.** The default set paints its own edge and so
    must NOT carry the class; asserting the mechanism means asking a set
    that needs it. ``test_the_hook_is_absent_for_a_set_that_draws_its_own_edge``
    is the other half.
    """

    @freeze_time(MIDDAY)
    def test_the_hook_is_absent_for_a_set_that_draws_its_own_edge(self) -> None:
        """SNOW-791: the halo is a blur, so it is only worth its cost sometimes.

        ``.weather-icon`` softens every mark it passes over — at 27 px it
        turns a six-armed flake into a blob. A set whose artwork already
        carries a silhouette edge must not be given a second one.
        """
        location = LocationFactory.create(elevation_m=2200.0)
        ResortLocationFactory.create(resort=ResortFactory.create(), location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
        )
        url = reverse("public:location_weather", args=[location.short_id])
        client = Client()

        needs = client.get(f"{url}?date={PAGE_DATE.isoformat()}&icons=yr")
        assert 'class="weather-icon' in needs.content.decode()

        draws_own = client.get(f"{url}?date={PAGE_DATE.isoformat()}&icons=snowdesk")
        assert 'class="weather-icon' not in draws_own.content.decode()

    @freeze_time(MIDDAY)
    def test_the_resort_pages_panel_carries_the_hook(self) -> None:
        """``_weather_panel.html``, via the resort page."""
        resort = ResortFactory.create()
        location = LocationFactory.create(elevation_m=1500.0)
        ResortLocationFactory.create(resort=resort, location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
        )

        content = Client().get(f"{resort.get_absolute_url()}?icons=yr").content.decode()

        assert 'data-testid="resort-weather"' in content
        assert 'class="weather-icon' in content

    @freeze_time(MIDDAY)
    def test_the_picker_and_the_day_line_both_carry_the_hook(self) -> None:
        """``_weather_day_picker.html`` and ``_weather_day_line.html``.

        Both live on the location forecast page and both draw an icon, so
        one assertion over the whole page would pass on either alone. The
        page is sliced at the picker's ``</fieldset>`` to tell them apart.
        """
        location = LocationFactory.create(elevation_m=2200.0)
        ResortLocationFactory.create(resort=ResortFactory.create(), location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
            forecast=[_forecast_day("2026-08-31")],
        )

        html = (
            Client()
            .get(
                f"{reverse('public:location_weather', args=[location.short_id])}?icons=yr"
            )
            .content.decode()
        )

        split = html.index("</fieldset>")
        picker, below = html[:split], html[split:]
        assert 'class="weather-icon' in picker
        assert 'class="weather-icon' in below


@pytest.mark.django_db
class TestIconSetSwitcher:
    """The DEBUG-only strip for comparing candidate icon sets (SNOW-791)."""

    @freeze_time(MIDDAY)
    def test_switching_sets_keeps_every_other_query_parameter(self) -> None:
        """A switch link must not drop the page's own parameters.

        The strip first shipped with a bare ``?icons=<name>`` href, which
        replaces the whole query string. On this page that dropped
        ``?date=``, so switching fell back to today, today has no row, and
        every icon vanished — which reads as the switch being broken rather
        than the date being lost.
        """
        location = LocationFactory.create(elevation_m=2200.0)
        ResortLocationFactory.create(resort=ResortFactory.create(), location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
            forecast=[_forecast_day("2026-08-31")],
        )
        url = reverse("public:location_weather", args=[location.short_id])

        content = Client().get(f"{url}?date={PAGE_DATE.isoformat()}").content.decode()

        hrefs = re.findall(r'href="(\?[^"]*icons=[^"]*)"', content)
        assert hrefs, "the switcher rendered no links"
        for href in hrefs:
            assert f"date={PAGE_DATE.isoformat()}" in href, href

    @freeze_time(MIDDAY)
    def test_the_chosen_set_reaches_the_rendered_icon(self) -> None:
        """``?icons=`` picks the directory the icons are served from."""
        location = LocationFactory.create(elevation_m=2200.0)
        ResortLocationFactory.create(resort=ResortFactory.create(), location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            weather_code=71,
            sunrise=SUNRISE,
            sunset=SUNSET,
        )
        url = reverse("public:location_weather", args=[location.short_id])

        client = Client()
        for set_name in ("snowdesk", "meteocons", "yr"):
            content = client.get(
                f"{url}?date={PAGE_DATE.isoformat()}&icons={set_name}"
            ).content.decode()
            assert f"icons/weather/{set_name}/light_snow.svg" in content, set_name


@pytest.mark.django_db
class TestMeteogramMarks:
    """SNOW-790 — what the chart draws, and where each mark lives."""

    @freeze_time(MIDDAY)
    def _render(self) -> str:
        """
        Render the location forecast page with one charted day.

        Returns:
            The page HTML.

        """
        location = LocationFactory.create(name="Attelas", elevation_m=2200.0)
        ResortLocationFactory.create(resort=ResortFactory.create(), location=location)
        WeatherFactory.create(
            location=location,
            observed_on=PAGE_DATE,
            sunrise=SUNRISE,
            sunset=SUNSET,
            hourly=_hourly_series("2026-08-30"),
            forecast=[],
        )
        return (
            Client()
            .get(reverse("public:location_weather", args=[location.short_id]))
            .content.decode()
        )

    def test_the_daylight_bar_is_on_the_temperature_axis_alone(self) -> None:
        """
        One axis thickens; the other two keep their bare label rows.

        The band answers "when", which is the axis's own question. Over the
        precipitation and wind plots it would say nothing a bar or a gust
        depends on — the SNOW-723 finding this ticket kept.
        """
        html = self._render()

        assert 'data-testid="location-weather-hourly-axis-track"' in html
        assert 'data-testid="location-weather-hourly-daylight"' in html
        temp_axis = html.index('data-testid="location-weather-hourly-temp-axis"')
        precip_axis = html.index('data-testid="location-weather-hourly-precip-axis"')
        assert (
            html.index('data-testid="location-weather-hourly-daylight"') < precip_axis
        )
        assert (
            html.index('data-testid="location-weather-hourly-axis-track"') > temp_axis
        )
        # One bar on the card, not one per axis.
        assert html.count('data-testid="location-weather-hourly-axis-track"') == 1

    def test_the_clock_is_a_notch_in_the_bar_not_a_rule_through_the_plots(
        self,
    ) -> None:
        """
        The cursor left all four SVGs and became a marker on the axis.

        It used to be a full-height hairline crossing every series — the
        one mark on the drawing that was not data but was drawn like it.
        """
        html = self._render()

        assert 'data-testid="location-weather-hourly-now"' in html
        assert html.count('data-testid="location-weather-hourly-now"') == 1

    def test_the_elevation_rule_is_drawn_in_the_metre_scales_own_ink(self) -> None:
        """
        Anything measured in metres is blue.

        The rule exists to be read against the freezing level, and was the
        only metre-scale mark not coloured like one.
        """
        html = self._render()

        chart = html[html.index('data-testid="location-weather-hourly-temp"') :]
        chart = chart[: chart.index("</svg>")]
        # The full-width horizontal rules only. The hour ticks keep
        # border-strong and are not what this is about — one of them shares
        # the elevation rule's x1, which is why the match runs to x2.
        rules = re.findall(
            r'<line\s+x1="40"\s+y1="[^"]*"\s+x2="560"\s+y2="[^"]*"\s+class="([^"]+)"',
            chart,
        )
        assert "stroke-accent" in rules
        assert "stroke-border-strong" not in rules

    def test_the_elevation_rule_is_dotted_so_it_reads_as_furniture(self) -> None:
        """
        Blue alone made a solid full-width hairline read as a series.

        The dots put it back beside the freezing level's long dash as a
        fixed property of the location rather than something the day does.
        """
        html = self._render()

        chart = html[html.index('data-testid="location-weather-hourly-temp"') :]
        chart = chart[: chart.index("</svg>")]
        rule = re.search(
            r'<line\s+x1="40"\s+y1="[^"]*"\s+x2="560"\s+y2="[^"]*"'
            r'\s+class="stroke-accent"[^/]*?stroke-dasharray="1 3"',
            chart,
        )
        assert rule is not None

    def test_both_vertical_scales_carry_tick_marks(self) -> None:
        """
        SNOW-790: the °C and metre figures were floating beside the plot.

        One mark per label on each gutter, drawn outward from the plot's
        own edges at 40 and 560.
        """
        html = self._render()

        chart = html[html.index('data-testid="location-weather-hourly-temp"') :]
        chart = chart[: chart.index("</svg>")]
        left = re.findall(r'<line\s+x1="35"\s+y1="([^"]*)"\s+x2="40"', chart)
        right = re.findall(r'<line\s+x1="560"\s+y1="([^"]*)"\s+x2="565"', chart)
        assert left
        assert right
        # A tick sits at its value's own height, not at the label's offset.
        assert len(set(left)) == len(left)

    def test_only_the_temperature_plot_carries_left_and_right_edges(self) -> None:
        """
        An edge closes off a scale, and one chart has scales.

        Temperature carries °C down one gutter and metres down the other,
        each label joined to the plot by its own tick, so the edges finish
        that frame. Precipitation and wind have no vertical scale and no
        gutter labels, and an edge there would be a box drawn round
        nothing.

        **Matched by span, not by x.** The edges stand at 40 and 560, which
        is exactly where the hour-0 and hour-24 ticks stand — a vertical
        line at either x proves nothing, and an earlier version of this
        test passed by matching those ticks whether the edges were drawn or
        not. An edge runs most of the plot's height; a tick runs seven
        units.
        """
        html = self._render()

        def tall_verticals(testid: str) -> list[str]:
            chart = html[
                html.index(f'data-testid="location-weather-hourly-{testid}"') :
            ]
            chart = chart[: chart.index("</svg>")]
            found = re.findall(
                r'<line\s+x1="(40|560)"\s+y1="([\d.]+)"\s+x2="\1"\s+y2="([\d.]+)"',
                chart,
            )
            return [x for x, y1, y2 in found if float(y2) - float(y1) > 50]

        assert sorted(tall_verticals("temp")) == ["40", "560"]
        assert tall_verticals("precip") == []

    def test_the_zero_rule_stays_on_the_plot_but_not_in_the_key(self) -> None:
        """
        It meets a labelled °C axis at zero and says what it is.

        A key is for marks a reader cannot place. The rule is still drawn —
        removing the row must not take the line with it.
        """
        html = self._render()

        assert 'stroke-dasharray="3 3"' in html
        legend = html[html.index('data-testid="location-weather-hourly-legend"') :]
        legend = legend[: legend.index("</dl>")]
        assert "0&deg;C" not in legend
        assert "The time now" not in legend

    def test_the_key_is_nine_labelled_rows(self) -> None:
        """
        Nine marks, one bare label each.

        Ten rows of prose made a sheet tall enough to cover the chart it
        was explaining. The daylight bar is the ninth and last, the only
        entry that names something on the axis rather than a series.
        """
        html = self._render()

        legend = html[html.index('data-testid="location-weather-hourly-legend"') :]
        legend = legend[: legend.index("</dl>")]
        assert legend.count("<dt") == 9
        assert legend.count("<dd") == 9
        assert "Freezing level (m)" in legend
        assert "Location elevation (m)" in legend
        assert "Sunrise to sunset" in legend

    def test_the_daylight_swatch_uses_the_axis_bars_own_fills(self) -> None:
        """
        A key drawn in approximations teaches a mark that is not there.

        The swatch has to be matchable by colour against the bar it
        explains, so it takes the same two tokens.
        """
        html = self._render()

        legend = html[html.index('data-testid="location-weather-hourly-legend"') :]
        legend = legend[: legend.index("</dl>")]
        assert "fill-chart-night" in legend
        assert "fill-chart-daylight" in legend

    def test_the_daylight_pair_is_spoken_for_a_reader_who_cannot_see_it(self) -> None:
        """
        The bar is aria-hidden, so the chart's own label carries the pair.

        The legend's "About this forecast" block is the other candidate
        home and never renders on this page — it is supplied by the
        component library alone.
        """
        html = self._render()

        assert "daylight 06:30 to 20:15" in html


# ---------------------------------------------------------------------------
# SNOW-799 — the weather page's canonical URL
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLocationWeatherCanonicalUrl:
    """The bare page is canonical for today; a dated page for its own day."""

    @staticmethod
    def _canonical(content: str) -> str:
        """Return the href of the page's <link rel="canonical">."""
        match = re.search(r'<link rel="canonical" href="([^"]+)">', content)
        assert match, "no canonical link"
        return match.group(1)

    @staticmethod
    def _location() -> Location:
        """A public location the page will render for."""
        location = LocationFactory.create(name="Mont Fort")
        ResortLocationFactory.create(location=location)
        return location

    def test_no_date_is_the_bare_url(self) -> None:
        """Undated: canonical is /weather/<short_id>/ with no query string."""
        location = self._location()
        content = Client().get(location.get_absolute_url()).content.decode()
        expected = f"{settings.SITE_BASE_URL.rstrip('/')}{location.get_absolute_url()}"
        assert self._canonical(content) == expected
        assert f'<meta property="og:url" content="{expected}">' in content

    def test_today_is_the_bare_url(self) -> None:
        """?date=today picks the same row the bare page shows — one canonical."""
        location = self._location()
        today = timezone.localdate().isoformat()
        content = (
            Client().get(f"{location.get_absolute_url()}?date={today}").content.decode()
        )
        assert self._canonical(content).endswith(location.get_absolute_url())

    def test_a_past_day_keeps_its_date(self) -> None:
        """A dated page is its own citable thing, canonical with ?date=."""
        location = self._location()
        content = (
            Client()
            .get(f"{location.get_absolute_url()}?date=2026-01-14")
            .content.decode()
        )
        assert self._canonical(content).endswith(
            f"{location.get_absolute_url()}?date=2026-01-14"
        )
