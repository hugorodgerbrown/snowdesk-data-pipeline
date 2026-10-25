"""
tests/public/test_resort_detail.py — Tests for the resort detail page (SNOW-504).

Covers ``apps.public.views.resort_detail`` (``/resorts/<slug>/``, SNOW-796):
  - 200 + content: resort name and parent region name.
  - A resort has no avalanche danger of its own (Hugo, 25 Oct 2026): no
    level, no problem cards, no season danger calendar, whatever the
    region's bulletin says — one neutral row naming the region, which links
    its bulletin page for the day; and the title and description make no
    danger claim. This retired the SNOW-1124 / SNOW-1156 / SNOW-1203
    bulletin card and the SNOW-1126 season strip the page carried.
  - SNOW-1155: the header frame — the mono eyebrow (alternative name ·
    canton · country, each part omitted when blank, the element omitted
    when all are), the "See on the map" link to ``/map/?resort=<slug>``,
    and the facts block as a definition list.
  - The pre-slug ``/resorts/<id>/<slug>/`` form 301s to the canonical URL
    whatever suffix it carries (``resort_legacy_redirect``), and does not
    loop.
  - A resort with ``needs_review=True`` or null coordinates still renders.
  - An unknown slug returns 404.
  - Favourite-star state: sign-in CTA for anonymous/ineligible visitors,
    the button (unfavourited or favourited) for eligible ones.
  - SNOW-807: each curated location is a link to its weather page.
  - SNOW-1125: each location row carries today's brief one-day weather
    (icon, condition, high/low, snowfall, wind — no freezing level, no sun
    times), read in one batched query; no row today means the link alone.
  - SNOW-855: the meta description branches on whether the resort has any
    curated locations — it named a village, a mid-station and a summit for
    every resort, including those with no locations at all.
  - SNOW-1122: the title and description name the resort beside the terms
    people search for, and "weather" only when there are forecasts.
  - SNOW-1123: the schema.org SkiResort JSON-LD — core properties, optional
    ones omitted when unset, and a ``</script>`` that cannot break out.
  - Resort facts block (SNOW-695, a ``<dl>`` since SNOW-1155): the curated
    Resort columns the page stored but never rendered. Every row renders
    when curated, an unset row is omitted, the whole block is omitted
    rather than rendered empty when nothing is curated at all, elevation
    carries a thousands separator, and notes is a full-width row.
"""

from __future__ import annotations

import datetime
import html
import json
import re
from html.parser import HTMLParser
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.bulletins.models import Bulletin
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    build_render_model,
)
from apps.regions.models import MicroRegion, Resort
from tests.factories import (
    BulletinFactory,
    FavouriteFactory,
    LocationFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    ResortFactory,
    ResortLocationFactory,
    SubRegionFactory,
    UserFactory,
    WeatherFactory,
)


def _todays_bulletin(
    region: MicroRegion,
    *,
    valid_from: datetime.datetime | None = None,
    **problem_overrides: Any,
) -> Bulletin:
    """Create an SLF bulletin covering all of today with one wind-slab problem.

    The render model is built from the CAAML properties exactly as ingestion
    builds it, so the page reads the same shape production stores.
    ``problem_overrides`` replace keys of the one problem; a ``None`` value
    removes the key (``elevation=None`` is a problem with no elevation).
    """
    today = timezone.now().date()
    start = valid_from or datetime.datetime(
        today.year, today.month, today.day, tzinfo=datetime.UTC
    )
    problem: dict[str, Any] = {
        "problemType": "wind_slab",
        "comment": "<p>Fresh wind slabs on shady slopes.</p>",
        "aspects": ["N", "NE", "E"],
        "elevation": {"lowerBound": "2200"},
        "dangerRatingValue": "considerable",
        "validTimePeriod": "all_day",
    }
    problem.update(problem_overrides)
    problem = {key: value for key, value in problem.items() if value is not None}
    properties: dict[str, Any] = {
        "dangerRatings": [{"mainValue": "considerable"}],
        "avalancheProblems": [problem],
        "customData": {
            "CH": {"aggregation": [{"category": "dry", "problemTypes": ["wind_slab"]}]}
        },
    }
    bulletin = BulletinFactory.create(
        raw_data={"type": "Feature", "geometry": None, "properties": properties},
        render_model=build_render_model(properties),
        render_model_version=RENDER_MODEL_VERSION,
        issued_at=start,
        valid_from=start,
        valid_to=start + datetime.timedelta(hours=23, minutes=59),
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    return bulletin


@pytest.mark.django_db
class TestResortDetailContent:
    """200 response with the expected content."""

    def test_returns_200_with_resort_and_region_content(self) -> None:
        """The page shows the resort name and parent region name."""
        region = MicroRegionFactory.create(name="Martigny – Verbier")
        resort = ResortFactory.create(name="Verbier", region=region, canton="VS")

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200
        content = response.content.decode()
        assert "Verbier" in content
        assert "Martigny" in content

    def test_includes_name_alt_when_set(self) -> None:
        """An alternative/marketing name renders when set."""
        resort = ResortFactory.create(name="Verbier", name_alt="4 Vallées")

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert "4 Vallées" in response.content.decode()

    def test_shows_no_danger_even_when_a_bulletin_covers_today(self) -> None:
        """A resort has no avalanche danger of its own (Hugo, 25 Oct 2026).

        The region's level, its problems and its season calendar are the
        region's, on the region's page: none of them render here.
        """
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        _todays_bulletin(region)

        content = Client().get(resort.get_absolute_url()).content.decode()

        for gone in (
            "day-windows-panel",
            "resort-danger",
            "resort-problems",
            "resort-season",
            "resort-no-rating",
        ):
            assert f'data-testid="{gone}"' not in content
        assert "danger-tile" not in content

    def test_the_region_row_names_the_region_and_links_its_bulletin(self) -> None:
        """One neutral row, no level, linking today's bulletin page."""
        region = MicroRegionFactory.create(name="Martigny – Verbier")
        resort = ResortFactory.create(region=region)
        _todays_bulletin(region)

        content = Client().get(resort.get_absolute_url()).content.decode()

        start = content.index('data-testid="resort-region-link"')
        row = content[content.rindex("<a", 0, start) : content.index("</a>", start)]
        assert f'href="{region.get_absolute_url()}"' in row
        assert 'data-panel-open="region"' in row
        assert f'data-panel-id="{region.region_id}"' in row
        assert "Martigny – Verbier" in html.unescape(row)
        assert "The avalanche region this resort is in" in row
        assert "danger-tile" not in row


@pytest.mark.django_db
@freeze_time("2026-02-20 12:00:00+00:00")
@pytest.mark.django_db
class TestResortLegacyRedirect:
    """The pre-SNOW-796 ``/resorts/<id>/<slug>/`` form 301s to ``/resorts/<slug>/``."""

    def test_legacy_url_301s_to_canonical(self) -> None:
        """The integer form redirects to resort.get_absolute_url()."""
        resort = ResortFactory.create(name="Verbier")

        client = Client()
        response = client.get(f"/resorts/{resort.pk}/verbier/")

        assert response.status_code == 301
        assert response["Location"] == "/resorts/verbier/"

    def test_legacy_suffix_is_ignored(self) -> None:
        """A stale name suffix still lands on the stored slug's page."""
        resort = ResortFactory.create(name="Verbier")

        client = Client()
        response = client.get(f"/resorts/{resort.pk}/wrong-slug/")

        assert response.status_code == 301
        assert response["Location"] == resort.get_absolute_url()

    def test_canonical_slug_renders_without_redirect(self) -> None:
        """The canonical slug renders directly with no redirect."""
        resort = ResortFactory.create(name="Verbier")

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200

    def test_redirect_target_itself_is_canonical(self) -> None:
        """Following the 301 lands on a 200 — the redirect doesn't loop."""
        resort = ResortFactory.create(name="Verbier")

        client = Client()
        response = client.get(f"/resorts/{resort.pk}/wrong-slug/", follow=True)

        assert response.status_code == 200
        assert response.redirect_chain == [(resort.get_absolute_url(), 301)]

    def test_unknown_pk_returns_404(self) -> None:
        """An integer nothing owns is a 404, not a redirect to nowhere."""
        client = Client()
        response = client.get("/resorts/999999/nowhere/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestResortDetailEdgeCases:
    """A resort needing review or missing coordinates still renders."""

    def test_needs_review_resort_still_renders(self) -> None:
        """needs_review=True does not hide the page."""
        resort = ResortFactory.create(needs_review=True)

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200

    def test_null_coordinates_resort_still_renders(self) -> None:
        """A resort with no latitude/longitude still renders."""
        resort = ResortFactory.create(latitude=None, longitude=None)

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200

    def test_unknown_slug_returns_404(self) -> None:
        """An unknown slug returns 404."""
        client = Client()
        response = client.get("/resorts/nowhere/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestResortDetailFavouriteState:
    """Favourite-star state mirrors apps.public.api.resort_popup's contract."""

    def test_anonymous_shows_signin_cta(self) -> None:
        """An anonymous visitor sees the sign-in CTA, not the toggle button."""
        resort = ResortFactory.create()

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-favourite-signin"' in content
        assert 'data-testid="resort-favourite-toggle"' not in content

    def test_anonymous_signin_cta_is_the_save_resort_button(self) -> None:
        """SNOW-1155: the same "Save resort" button shape, linking to sign-in."""
        resort = ResortFactory.create()

        content = Client().get(resort.get_absolute_url()).content.decode()
        link = re.search(
            r'<a\s+href="([^"]+)"\s+data-testid="resort-favourite-signin"'
            r'\s+class="([^"]+)"\s*>(.*?)</a>',
            content,
            re.S,
        )

        assert link is not None
        assert link.group(1) == reverse("accounts:sign_in")
        assert "border-text-1" in link.group(2)  # button_classes secondary
        assert "Save resort" in link.group(3)
        assert "<svg" in link.group(3)

    def test_authenticated_not_favourited_shows_unfavourited_button(self) -> None:
        """An eligible, not-yet-favouriting user sees the toggle, unfavourited."""
        resort = ResortFactory.create()
        user = UserFactory.create()

        client = Client()
        client.force_login(user)
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-favourite-toggle"' in content
        assert 'data-favourited="false"' in content

    def test_authenticated_already_favourited_shows_saved_state(self) -> None:
        """An already-favourited resort shows the toggle in its saved state."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        user = UserFactory.create()
        FavouriteFactory.create(
            user=user,
            resort=resort,
            latitude=46.1,
            longitude=7.4,
        )

        client = Client()
        client.force_login(user)
        response = client.get(resort.get_absolute_url())

        assert 'data-favourited="true"' in response.content.decode()


@pytest.mark.django_db
class TestResortDetailUrl:
    """URL-reversal sanity check."""

    def test_url_reverses_to_expected_path(self) -> None:
        """public:resort reverses to /resorts/<slug>/ — no pk in it."""
        resort = ResortFactory.create(name="Verbier")
        url = reverse("public:resort", kwargs={"slug": resort.slug})
        assert url == "/resorts/verbier/"
        assert str(resort.pk) not in url


# SNOW-1125/1157: a fixed winter day. 11:00 UTC is midday in Europe/Zurich
# and 21:00 UTC is evening on the same local date, so
# ``timezone.localdate()`` (the day the view reads) is TODAY under both.
TODAY = datetime.date(2026, 1, 15)
MIDDAY = "2026-01-15T11:00:00+00:00"
EVENING = "2026-01-15T21:00:00+00:00"
SUNRISE = datetime.datetime(2026, 1, 15, 7, 0, tzinfo=datetime.UTC)
SUNSET = datetime.datetime(2026, 1, 15, 16, 0, tzinfo=datetime.UTC)


def _forecast_day(offset: int, **overrides: Any) -> dict[str, Any]:
    """Return one ``forecast[]`` entry ``offset`` days after TODAY."""
    day = TODAY + datetime.timedelta(days=offset)
    entry: dict[str, Any] = {
        "date": day.isoformat(),
        "weather_code": 73,
        "sunrise": f"{day.isoformat()}T08:00:00+01:00",
        "sunset": f"{day.isoformat()}T17:00:00+01:00",
        "temperature_2m_max": -2.0,
        "temperature_2m_min": -7.0,
        "snowfall_sum": 0.0,
    }
    entry.update(overrides)
    return entry


def _todays_weather(location: Any, **overrides: Any) -> Any:
    """Create today's Weather row for a location with a six-day forecast."""
    fields: dict[str, Any] = {
        "location": location,
        "observed_on": TODAY,
        "fetched_at": datetime.datetime(2026, 1, 15, 11, 0, tzinfo=datetime.UTC),
        "sunrise": SUNRISE,
        "sunset": SUNSET,
        "weather_code": 0,
        "temperature_2m_max": 4.0,
        "temperature_2m_min": -6.0,
        "snowfall_sum": 12.0,
        "wind_speed_10m_max": 35.0,
        "wind_gusts_10m_max": 60.0,
        "wind_direction_10m_dominant": 315.0,
        "freezing_level_height": 1900.0,
        "forecast": [_forecast_day(offset) for offset in range(1, 7)],
    }
    fields.update(overrides)
    return WeatherFactory.create(**fields)


class _ElementSource(HTMLParser):
    """Collect the source of the first element carrying a given ``data-testid``.

    Tracks nesting depth from that element's start tag to its matching end
    tag, so nested ``</div>`` tags inside it do not end the capture early.
    """

    _VOID = frozenset({"br", "hr", "img", "input", "link", "meta", "source"})

    def __init__(self, testid: str) -> None:
        """Start looking for ``[data-testid=testid]``."""
        super().__init__(convert_charrefs=False)
        self.testid = testid
        self.depth = 0
        self.done = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open (or deepen) the capture."""
        if self.done:
            return
        if self.depth == 0 and ("data-testid", self.testid) not in attrs:
            return
        self.parts.append(self.get_starttag_text() or "")
        if tag not in self._VOID:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        """Close one level; finish at the element's own end tag."""
        if self.done or self.depth == 0:
            return
        self.parts.append(f"</{tag}>")
        self.depth -= 1
        if self.depth == 0:
            self.done = True

    def handle_data(self, data: str) -> None:
        """Keep text inside the capture."""
        if self.depth and not self.done:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        """Keep a named entity (``&deg;``) inside the capture, as written."""
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        """Keep a numeric entity inside the capture, as written."""
        self.handle_data(f"&#{name};")


def _section(content: str, testid: str) -> str:
    """Return the markup of the first ``[data-testid=testid]`` element."""
    parser = _ElementSource(testid)
    parser.feed(content)
    assert parser.done, f"{testid} not found"
    return "".join(parser.parts)


def _role_link(resort: Resort, role: str, **location_fields: Any) -> Any:
    """Link a new location to ``resort`` in ``role`` and return the location."""
    location = LocationFactory.create(**location_fields)
    ResortLocationFactory.create(resort=resort, location=location, role=role)
    return location


@pytest.mark.django_db
@freeze_time(MIDDAY)
class TestResortForecastPoints:
    """SNOW-1157: Base / Mid / Summit, from the roles, the pin standing in for Base.

    Each test gives one point today's row: with none anywhere the card gives
    way to one line (SNOW-1205).
    """

    def test_one_chip_per_role_in_base_mid_summit_order(self) -> None:
        """Each role's location is a chip, in that order, with its elevation."""
        resort = ResortFactory.create(name="Verbier")
        _todays_weather(_role_link(resort, "TOP", name="Mont Fort", elevation_m=3329.6))
        _role_link(resort, "BASE", name="Verbier village", elevation_m=1494.0)
        _role_link(resort, "MID", name="Les Ruinettes", elevation_m=2200.0)

        content = Client().get(resort.get_absolute_url()).content.decode()

        chips = _section(content, "resort-forecast-chips")
        base = chips.index('data-testid="resort-forecast-chip-base"')
        mid = chips.index('data-testid="resort-forecast-chip-mid"')
        top = chips.index('data-testid="resort-forecast-chip-top"')
        assert base < mid < top
        text = re.sub(r"\s+", " ", chips)
        assert "Base · 1,494 m" in text
        assert "Mid · 2,200 m" in text
        assert "Summit · 3,330 m" in text

    def test_the_chips_are_a_labelled_group_of_radios_with_base_checked(
        self,
    ) -> None:
        """Hidden radios share a name; the first point is checked; no script."""
        resort = ResortFactory.create()
        _todays_weather(_role_link(resort, "BASE", elevation_m=1500.0))
        _role_link(resort, "TOP", elevation_m=3000.0)

        content = Client().get(resort.get_absolute_url()).content.decode()

        section = _section(content, "resort-forecast")
        radios = re.findall(r"<input[^>]*>", section)
        assert len(radios) == 2
        assert all('name="resort-forecast-point"' in radio for radio in radios)
        assert "checked" in radios[0]
        assert "checked" not in radios[1]
        assert 'role="group"' in section
        assert 'aria-label="Forecast location"' in section
        assert 'for="resort-forecast-point-0"' in section
        assert "<script" not in section

    def test_the_pin_stands_in_for_base(self) -> None:
        """No BASE link: the resort's own pin is the Base point."""
        resort = ResortFactory.create(name="Ovronnaz", geocoded=True)
        assert resort.location is not None
        _todays_weather(_role_link(resort, "TOP", elevation_m=2500.0))

        content = Client().get(resort.get_absolute_url()).content.decode()

        pane = _section(content, "resort-forecast-pane-base")
        assert f'href="{resort.location.get_absolute_url()}"' in pane
        assert 'data-testid="resort-forecast-chip-top"' in content
        assert 'data-testid="resort-forecast-chip-mid"' not in content

    def test_a_base_link_beats_the_pin(self) -> None:
        """A curated BASE location is the Base point, not the pin."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        village = _role_link(resort, "BASE", elevation_m=1500.0)
        _todays_weather(village)

        content = Client().get(resort.get_absolute_url()).content.decode()

        pane = _section(content, "resort-forecast-pane-base")
        assert f'href="{village.get_absolute_url()}"' in pane
        assert resort.location.get_absolute_url() not in content

    def test_the_primary_link_leads_within_a_role(self) -> None:
        """Two TOP links: the primary one is the Summit point."""
        resort = ResortFactory.create()
        _role_link(resort, "TOP", name="Aaa peak")
        primary = LocationFactory.create(name="Zzz peak")
        ResortLocationFactory.create(
            resort=resort, location=primary, role="TOP", is_primary=True
        )
        _todays_weather(primary)

        content = Client().get(resort.get_absolute_url()).content.decode()

        pane = _section(content, "resort-forecast-pane-top")
        assert f'href="{primary.get_absolute_url()}"' in pane
        assert content.count('data-testid="resort-forecast-chip-top"') == 1

    def test_a_single_point_is_still_one_chip(self) -> None:
        """A resort with only a pin shows a Base chip over its strip."""
        resort = ResortFactory.create(geocoded=True)
        _todays_weather(resort.location)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-forecast-chip-base"' in content
        assert content.count("<label") >= 1
        assert 'data-testid="resort-forecast-chip-mid"' not in content
        assert 'data-testid="resort-forecast-chip-top"' not in content

    def test_unknown_elevation_omits_the_elevation(self) -> None:
        """A chip reads just its role when the elevation is not resolved."""
        resort = ResortFactory.create()
        _todays_weather(_role_link(resort, "MID", elevation_m=None))

        content = Client().get(resort.get_absolute_url()).content.decode()

        chip = _section(content, "resort-forecast-chip-mid")
        assert re.sub(r"<[^>]+>|\s+", "", chip) == "Mid"

    def test_a_blank_role_link_is_not_a_point(self) -> None:
        """A role-less link names no altitude: no chip, and alone no section."""
        resort = ResortFactory.create()
        ResortLocationFactory.create(
            resort=resort, location=LocationFactory.create(anonymous=True), role=""
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-forecast"' not in content

    def test_no_pin_and_no_role_links_means_no_section(self) -> None:
        """An unpinned resort with no links renders no Forecast section."""
        resort = ResortFactory.create()

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-forecast"' not in content
        assert "Forecast •" not in content

    def test_the_old_location_list_is_gone(self) -> None:
        """SNOW-807's list of links with one-day rows is replaced."""
        resort = ResortFactory.create(geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-locations"' not in content
        assert "resort-location-0" not in content
        assert "weather-panel" not in content


@pytest.mark.django_db
class TestResortForecastStrip:
    """SNOW-1157: seven tiles per point, today first, from one row."""

    @freeze_time(MIDDAY)
    def test_eyebrow_names_the_forecast_and_today(self) -> None:
        """The h2 reads "Forecast • Thursday 15 January"."""
        resort = ResortFactory.create(geocoded=True)
        _todays_weather(resort.location)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert re.search(
            r'<h2[^>]*id="resort-forecast-point-heading"[^>]*>'
            r"\s*Forecast • Thursday 15 January\s*</h2>",
            content,
        )

    @freeze_time(MIDDAY)
    def test_seven_tiles_today_first(self) -> None:
        """Today's tile leads with Max / Min / New snow; six days follow."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        _todays_weather(resort.location)

        content = Client().get(resort.get_absolute_url()).content.decode()

        pane = _section(content, "resort-forecast-pane-base")
        today = pane.index('data-testid="resort-forecast-today"')
        assert pane.count('data-testid="resort-forecast-today"') == 1
        assert pane.count('data-testid="resort-forecast-day"') == 6
        assert today < pane.index('data-testid="resort-forecast-day"')
        tile = _section(pane, "resort-forecast-today")
        assert "<dl" in tile
        for label in ("Max", "Min", "New snow"):
            assert re.search(rf"<dt[^>]*>\s*{label}\s*</dt>", tile)
        assert "4&deg;" in tile
        assert "-6&deg;" in tile
        assert "12 cm" in tile
        assert "clear-day.svg" in tile
        dates = re.findall(r'data-date="([\d-]+)"', pane)
        assert dates == [
            (TODAY + datetime.timedelta(days=offset)).isoformat() for offset in range(7)
        ]

    @freeze_time(MIDDAY)
    def test_the_other_tiles_show_day_icon_high_low_and_snow(self) -> None:
        """An unlabelled tile: weekday, icon, high, low and new snow."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        _todays_weather(
            resort.location,
            forecast=[_forecast_day(1, snowfall_sum=8.4, temperature_2m_max=-1.6)],
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        tile = _section(content, "resort-forecast-day")
        assert re.search(r">\s*Fri\s*<", tile)
        assert "moderate_snow.svg" in tile
        assert "-2&deg;" in tile
        assert "-7&deg;" in tile
        assert "8 cm" in tile
        assert "<dt" not in tile

    @freeze_time(MIDDAY)
    def test_zero_new_snow_reads_zero_cm(self) -> None:
        """0 cm is a statement on an avalanche page, never a blank."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        _todays_weather(
            resort.location,
            snowfall_sum=0.0,
            forecast=[_forecast_day(1, snowfall_sum=0.0)],
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert re.search(r">\s*0 cm\s*<", _section(content, "resort-forecast-today"))
        assert re.search(r">\s*0 cm\s*<", _section(content, "resort-forecast-day"))

    @freeze_time(MIDDAY)
    def test_a_short_forecast_renders_what_exists(self) -> None:
        """Two forward days: three tiles, no padding."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        _todays_weather(resort.location, forecast=[_forecast_day(1), _forecast_day(2)])

        content = Client().get(resort.get_absolute_url()).content.decode()

        pane = _section(content, "resort-forecast-pane-base")
        assert pane.count('data-testid="resort-forecast-today"') == 1
        assert pane.count('data-testid="resort-forecast-day"') == 2

    @freeze_time(MIDDAY)
    def test_a_backfilled_row_is_today_alone(self) -> None:
        """``forecast=None`` (a backfilled row) is one tile, not an error."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        _todays_weather(resort.location, forecast=None)

        content = Client().get(resort.get_absolute_url()).content.decode()

        pane = _section(content, "resort-forecast-pane-base")
        assert pane.count('data-testid="resort-forecast-today"') == 1
        assert 'data-testid="resort-forecast-day"' not in pane

    @freeze_time(MIDDAY)
    def test_a_longer_window_is_capped_at_seven(self) -> None:
        """Eight forward days still draw seven tiles."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        _todays_weather(
            resort.location, forecast=[_forecast_day(offset) for offset in range(1, 9)]
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert content.count('data-testid="resort-forecast-day"') == 6

    @freeze_time(MIDDAY)
    def test_each_tile_is_a_group_with_a_spoken_summary(self) -> None:
        """Today's label leads with "Today,"; the others name their weekday."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        _todays_weather(resort.location, forecast=[_forecast_day(1)])

        content = Client().get(resort.get_absolute_url()).content.decode()

        today = _section(content, "resort-forecast-today")
        day = _section(content, "resort-forecast-day")
        assert 'role="group"' in today
        assert 'role="group"' in day
        assert (
            'aria-label="Today, Thursday, clear, high 4°, low -6°, 12 cm new snow"'
            in today
        )
        assert 'aria-label="Friday, snow, high -2°, low -7°, 0 cm new snow"' in day
        assert 'alt=""' in day

    @freeze_time(MIDDAY)
    def test_missing_temperatures_drop_out_of_the_summary(self) -> None:
        """A dropped variable reads "—" and is left out of the label."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        _todays_weather(
            resort.location,
            forecast=[
                _forecast_day(
                    1,
                    temperature_2m_max=None,
                    temperature_2m_min=None,
                    snowfall_sum=None,
                )
            ],
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        day = _section(content, "resort-forecast-day")
        assert 'aria-label="Friday, snow"' in day
        assert day.count("&mdash;") == 3

    @freeze_time(EVENING)
    def test_after_sunset_today_draws_the_night_icon(self) -> None:
        """The day/night decision reads the request's own instant."""
        resort = ResortFactory.create(geocoded=True)
        assert resort.location is not None
        _todays_weather(resort.location)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert "clear-night.svg" in _section(content, "resort-forecast-today")

    @freeze_time(MIDDAY)
    def test_each_pane_carries_its_own_attribution_and_link(self) -> None:
        """The attribution line and the point's own forecast link, per pane."""
        resort = ResortFactory.create()
        base = _role_link(resort, "BASE", elevation_m=1500.0)
        top = _role_link(resort, "TOP", elevation_m=3000.0)
        _todays_weather(base)
        _todays_weather(top)

        content = Client().get(resort.get_absolute_url()).content.decode()

        for role, location in (("base", base), ("top", top)):
            pane = _section(content, f"resort-forecast-pane-{role}")
            footer = _section(pane, f"resort-forecast-provenance-{role}")
            assert re.search(r"Open-Meteo &middot; updated \d\d:\d\d", footer)
            link = _section(footer, f"resort-forecast-provenance-{role}-link")
            assert f'href="/weather/{location.short_id}/"' in link
            assert "Read the full 7-day forecast →" in link

    @freeze_time(MIDDAY)
    def test_a_point_without_a_short_id_still_credits_open_meteo(self) -> None:
        """No weather page yet (backfill window): the attribution, no link."""
        resort = ResortFactory.create()
        location = _role_link(resort, "BASE")
        _todays_weather(location)
        type(location).objects.filter(pk=location.pk).update(short_id=None)

        content = Client().get(resort.get_absolute_url()).content.decode()

        pane = _section(content, "resort-forecast-pane-base")
        footer = _section(pane, "resort-forecast-provenance-base")
        assert re.search(r"Open-Meteo &middot; updated \d\d:\d\d", footer)
        assert "Read the full 7-day forecast" not in pane

    @freeze_time(MIDDAY)
    def test_a_point_with_no_row_today_says_so_and_keeps_its_link(self) -> None:
        """Beside a point with a row today, a stale one says No forecast yet."""
        resort = ResortFactory.create()
        base = _role_link(resort, "BASE")
        _todays_weather(base, observed_on=datetime.date(2026, 1, 14))
        _todays_weather(_role_link(resort, "TOP"))

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert "resort-forecast-none" not in content
        pane = _section(content, "resort-forecast-pane-base")
        assert "No forecast yet." in pane
        assert "resort-forecast-today" not in pane
        assert "Open-Meteo" not in pane
        assert f'href="{base.get_absolute_url()}"' in pane

    @freeze_time(MIDDAY)
    def test_no_forecast_at_any_point_is_one_line(self) -> None:
        """SNOW-1205: no row anywhere draws one line, no card, chip or link."""
        resort = ResortFactory.create()
        location = _role_link(resort, "BASE")
        _todays_weather(location, observed_on=datetime.date(2026, 1, 14))

        content = Client().get(resort.get_absolute_url()).content.decode()

        section = _section(content, "resort-forecast")
        line = _section(section, "resort-forecast-none")
        assert "No forecast available for 15 January 2026" in line
        assert "resort-forecast-chips" not in section
        assert "No forecast yet." not in section
        assert "Read the full 7-day forecast" not in section
        assert location.get_absolute_url() not in section

    @freeze_time(MIDDAY)
    def test_the_query_count_does_not_grow_with_the_points(
        self, django_assert_num_queries: Any
    ) -> None:
        """One batched Weather read, whatever the number of points."""

        def resort_with(roles: tuple[str, ...]) -> Resort:
            resort = ResortFactory.create()
            for role in roles:
                _todays_weather(_role_link(resort, role))
            return resort

        one = resort_with(("BASE",))
        three = resort_with(("BASE", "MID", "TOP"))
        client = Client()
        # Warm any per-process caches (waffle flags, content types) first.
        client.get(resort_with(("MID",)).get_absolute_url())

        with CaptureQueriesContext(connection) as single:
            client.get(one.get_absolute_url())
        with django_assert_num_queries(len(single.captured_queries)):
            content = client.get(three.get_absolute_url()).content.decode()

        assert content.count('data-testid="resort-forecast-today"') == 3


def _meta_description(content: str) -> str:
    """Return the rendered meta description, or "" when the page emits none."""
    match = re.search(r'<meta name="description" content="([^"]*)"', content)
    return match.group(1) if match else ""


@pytest.mark.django_db
class TestResortMetaDescription:
    """SNOW-855: the description promises only what this resort links to."""

    def test_role_labelled_locations_promise_forecasts_without_naming_tiers(
        self,
    ) -> None:
        """A curated resort gets the forecast sentence, minus the three tiers."""
        resort = ResortFactory.create(name="Verbier")
        ResortLocationFactory.create(
            resort=resort,
            location=LocationFactory.create(name="Mont Fort", kind="PEAK"),
            role="TOP",
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = _meta_description(content)
        assert "forecast" in description
        assert "village, mid-station and summit" not in description

    def test_a_pinned_resort_promises_forecasts_without_naming_tiers(
        self,
    ) -> None:
        """SNOW-1157: the pin alone is a Base point, so the forecast is promised."""
        resort = ResortFactory.create(name="Verbier", geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = _meta_description(content)
        assert "forecast" in description
        assert "village, mid-station and summit" not in description

    def test_a_blank_role_link_alone_claims_no_forecast(self) -> None:
        """SNOW-1157: a role-less link is no point, so nothing is promised."""
        resort = ResortFactory.create(name="Verbier")
        ResortLocationFactory.create(
            resort=resort, location=LocationFactory.create(anonymous=True), role=""
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert "forecast" not in _meta_description(content)
        assert "weather" not in _page_title(content)

    def test_no_locations_names_the_bulletin_and_claims_no_forecast(self) -> None:
        """No Forecast section means no forecast promise in the description."""
        region = MicroRegionFactory.create(name="Val de Bagnes")
        # Unpinned: since SNOW-1144 a pinned resort always lists its pin.
        resort = ResortFactory.create(name="Verbier", region=region)

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = _meta_description(content)
        assert "Val de Bagnes avalanche bulletin" in description
        assert "forecast" not in description

    def test_the_with_locations_description_is_one_clean_line(self) -> None:
        """The second blocktrans is squished too, not just the first.

        ``TestNoWhitespaceLeaks`` in tests/public/test_page_meta.py walks
        every page, but its resort has no locations, so it only ever renders
        the other branch. ``|squish`` in the shared emitter protects both —
        this is what would catch a description that stopped going through it.
        """
        resort = ResortFactory.create(name="Verbier")
        ResortLocationFactory.create(
            resort=resort,
            location=LocationFactory.create(name="Mont Fort", kind="PEAK"),
            role="TOP",
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = _meta_description(content)
        assert "\n" not in description
        assert "  " not in description
        assert description == description.strip()


def _page_title(content: str) -> str:
    """Return the rendered <title> text, unescaped for '&'."""
    match = re.search(r"<title>([^<]*)</title>", content)
    return match.group(1).replace("&amp;", "&").strip() if match else ""


@pytest.mark.django_db
class TestResortSearchTerms:
    """SNOW-1122: title and description name the resort beside the search terms."""

    def test_title_with_locations_names_conditions_avalanche_and_weather(
        self,
    ) -> None:
        """A resort with forecasts gets all four target terms in its title."""
        resort = ResortFactory.create(name="Adelboden")
        ResortLocationFactory.create(
            resort=resort,
            location=LocationFactory.create(name="Adelboden", kind="VILLAGE"),
            role="BASE",
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        title = _page_title(content)
        assert title.startswith(
            "Adelboden conditions — weather & avalanche bulletin · Snowdesk"
        )

    def test_title_without_locations_does_not_promise_weather(self) -> None:
        """No Forecasts section, so "weather" stays out of the title."""
        resort = ResortFactory.create(name="Adelboden")

        content = Client().get(resort.get_absolute_url()).content.decode()

        title = _page_title(content)
        assert title.startswith("Adelboden resort & avalanche bulletin · Snowdesk")
        assert "weather" not in title

    def test_description_makes_no_danger_claim(self) -> None:
        """A resort has no danger of its own, rated day or not (25 Oct 2026)."""
        resort = ResortFactory.create(name="Adelboden")
        _todays_bulletin(resort.region)

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = _meta_description(content)
        assert description.startswith("Adelboden")
        assert "Danger" not in description
        assert "  " not in description

    def test_forecast_description_fits_a_search_snippet(self) -> None:
        """The longest branch stays inside the ~155-character snippet budget."""
        region = MicroRegionFactory.create(name="Bernese Oberland")
        resort = ResortFactory.create(name="Adelboden", region=region)
        ResortLocationFactory.create(
            resort=resort,
            location=LocationFactory.create(name="Adelboden", kind="VILLAGE"),
            role="BASE",
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = html.unescape(_meta_description(content))
        assert "Bernese Oberland avalanche bulletin" in description
        assert len(description) <= 155

    def test_body_carries_the_bulletin_heading(self) -> None:
        """The target terms appear in the visible page, not only the meta tags.

        SNOW-1156 retired the "Avalanche danger and mountain conditions"
        heading; the region row's own h2 carries "avalanche" (25 Oct 2026).
        """
        resort = ResortFactory.create(
            name="Adelboden", region=MicroRegionFactory.create(name="Frutigen")
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert re.search(
            r'<h2[^>]*id="resort-region-heading"[^>]*>\s*Avalanche bulletin',
            content,
        )
        assert "Frutigen" in content


def _json_ld(content: str) -> dict[str, Any]:
    """Return the page's parsed JSON-LD payload."""
    match = re.search(
        r'<script type="application/ld\+json"[^>]*>(.*?)</script>', content, re.S
    )
    assert match, "expected a JSON-LD script tag"
    payload: dict[str, Any] = json.loads(
        re.sub(r"\{#.*?#\}", "", match.group(1), flags=re.S)
    )
    return payload


@pytest.mark.django_db
class TestResortStructuredData:
    """SNOW-1123: the page emits schema.org SkiResort JSON-LD."""

    def test_payload_names_the_resort_its_url_and_its_address(self) -> None:
        """The core properties match the resort row."""
        region = MicroRegionFactory.create(name="Martigny – Verbier")
        resort = ResortFactory.create(
            name="Verbier",
            region=region,
            canton="VS",
            name_alt="4 Vallées",
            website="https://www.verbier4vallees.ch/",
            geocoded=True,
        )

        data = _json_ld(Client().get(resort.get_absolute_url()).content.decode())

        assert data["@context"] == "https://schema.org"
        assert data["@type"] == "SkiResort"
        assert data["name"] == "Verbier"
        assert data["alternateName"] == "4 Vallées"
        assert data["url"] == f"{settings.SITE_BASE_URL.rstrip('/')}/resorts/verbier/"
        assert data["address"]["@type"] == "PostalAddress"
        assert data["address"]["addressRegion"] == "VS"
        assert data["address"]["addressCountry"] == (region.subregion.major.country)
        assert data["geo"] == {
            "@type": "GeoCoordinates",
            "latitude": 46.1,
            "longitude": 7.4,
        }
        assert data["sameAs"] == "https://www.verbier4vallees.ch/"
        assert data["containedInPlace"]["name"] == "Martigny – Verbier"
        assert data["subjectOf"]["url"].endswith(region.get_absolute_url())

    def test_geo_comes_from_the_pin_location(self) -> None:
        """SNOW-1144: GeoCoordinates read the resort's Location, not the mirror."""
        pin = LocationFactory.create(anonymous=True, latitude=46.5, longitude=7.5)
        resort = ResortFactory.create(latitude=46.0, longitude=7.0, location=pin)

        data = _json_ld(Client().get(resort.get_absolute_url()).content.decode())

        assert (data["geo"]["latitude"], data["geo"]["longitude"]) == (46.5, 7.5)

    def test_touring_terrain_is_a_place_not_a_ski_resort(self) -> None:
        """A lift-less pass is not a SkiResort, so it is typed Place."""
        resort = ResortFactory.create(name="Grimsel", kind=Resort.Kind.TOURING_TERRAIN)

        data = _json_ld(Client().get(resort.get_absolute_url()).content.decode())

        assert data["@type"] == "Place"
        assert data["name"] == "Grimsel"

    def test_optional_properties_are_omitted_when_unset(self) -> None:
        """No coordinates, no alternate name, no website: no empty keys."""
        resort = ResortFactory.create(name="Verbier")

        data = _json_ld(Client().get(resort.get_absolute_url()).content.decode())

        assert "geo" not in data
        assert "alternateName" not in data
        assert "sameAs" not in data

    def test_a_script_close_in_the_name_cannot_break_out(self) -> None:
        """``</script>`` in a curated string is escaped inside the tag."""
        resort = ResortFactory.create(name="Evil</script><b>x</b>")

        content = Client().get(resort.get_absolute_url()).content.decode()

        block = content.split('<script type="application/ld+json"', 1)[1]
        block = block.split("</script>", 1)[0]
        assert "<\\/script>" in block
        assert _json_ld(content)["name"] == "Evil</script><b>x</b>"


def _valais_region(**kwargs: Any) -> MicroRegion:
    """Create a micro-region under CH-4 Valais, as Verbier's is."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_en="Valais")
    subregion = SubRegionFactory.create(prefix="CH-41", major=major)
    return MicroRegionFactory.create(region_id="CH-4115", subregion=subregion, **kwargs)


def _eyebrow_text(content: str) -> str | None:
    """Return the rendered eyebrow's text, or None when there is none."""
    match = re.search(r'data-testid="resort-eyebrow"\s*>([^<]*)<', content)
    return html.unescape(match.group(1)).strip() if match else None


@pytest.mark.django_db
class TestResortHeader:
    """SNOW-1155: the header frame — eyebrow, title, tagline, two actions."""

    def test_eyebrow_names_alternative_name_canton_and_country(self) -> None:
        """All three parts render in order, joined by a middle dot."""
        resort = ResortFactory.create(
            name="Verbier", name_alt="Les 4 Vallées", region=_valais_region()
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert _eyebrow_text(content) == "Les 4 Vallées · Valais · Switzerland"
        # A paragraph, not a heading: the page's one h1 is the name.
        assert re.search(r'<p\s+class="[^"]*"\s+data-testid="resort-eyebrow"', content)

    def test_eyebrow_omits_a_blank_alternative_name(self) -> None:
        """No name_alt means no leading part and no dangling separator."""
        resort = ResortFactory.create(name_alt="", region=_valais_region())

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert _eyebrow_text(content) == "Valais · Switzerland"

    def test_eyebrow_omits_a_blank_canton(self) -> None:
        """A major region with no English name drops the canton part."""
        major = MajorRegionFactory.create(prefix="CH-9", country="CH", name_en="")
        region = MicroRegionFactory.create(
            region_id="CH-9111",
            subregion=SubRegionFactory.create(prefix="CH-91", major=major),
        )
        resort = ResortFactory.create(name_alt="Alt", region=region)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert _eyebrow_text(content) == "Alt · Switzerland"

    def test_eyebrow_omits_an_unknown_country(self) -> None:
        """A country code with no English name is left out, not shown raw."""
        major = MajorRegionFactory.create(prefix="DE-1", country="DE", name_en="Bayern")
        region = MicroRegionFactory.create(
            region_id="DE-1111",
            subregion=SubRegionFactory.create(prefix="DE-11", major=major),
        )
        resort = ResortFactory.create(name_alt="", region=region)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert _eyebrow_text(content) == "Bayern"

    def test_no_parts_means_no_eyebrow_element(self) -> None:
        """Every part blank renders no empty eyebrow."""
        major = MajorRegionFactory.create(prefix="XX-1", country="XX", name_en="")
        region = MicroRegionFactory.create(
            region_id="XX-1111",
            subregion=SubRegionFactory.create(prefix="XX-11", major=major),
        )
        resort = ResortFactory.create(name_alt="", region=region)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-eyebrow"' not in content
        assert 'data-testid="resort-heading"' in content

    def test_the_old_location_lines_are_gone(self) -> None:
        """The separate canton · region line no longer renders."""
        resort = ResortFactory.create(canton="VS", region=_valais_region())

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-location"' not in content

    def test_see_on_the_map_links_to_the_resort_on_the_map(self) -> None:
        """The primary action flies the map to this resort's pin."""
        resort = ResortFactory.create(name="Verbier", geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        link = re.search(
            r'<a\s+href="([^"]+)"[^>]*data-testid="resort-map-link"', content
        )
        assert link is not None
        assert link.group(1) == f"{reverse('public:map')}?resort={resort.slug}"
        assert "See on the map" in content

    def test_see_on_the_map_is_hidden_without_a_pin(self) -> None:
        """A resort with no pin is not on the map feed, so no map action."""
        resort = ResortFactory.create(latitude=None, longitude=None, location=None)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-map-link"' not in content
        assert "See on the map" not in content

    def test_the_reports_link_is_gone(self) -> None:
        """SNOW-807's "Reports near here on the map" was replaced (SNOW-1155)."""
        resort = ResortFactory.create(geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-observations"' not in content
        assert "panel=reports" not in content
        assert "Reports near here" not in content

    def test_actions_sit_in_the_header_before_the_sections(self) -> None:
        """Save and the map link are in the header, above the sections."""
        resort = ResortFactory.create(geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        actions = content.index('data-testid="resort-actions"')
        assert actions < content.index('data-testid="resort-region"')
        assert actions < content.index('data-testid="resort-favourite-signin"')
        assert actions < content.index('data-testid="resort-map-link"')

    def test_the_facts_come_after_the_main_column(self) -> None:
        """About this resort is last in the source — last on a phone."""
        resort = ResortFactory.create(num_lifts=34, geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert content.index('data-testid="resort-facts"') > content.index(
            'data-testid="resort-region"'
        )
        assert content.index('data-testid="resort-facts"') > content.index(
            'data-testid="resort-forecast"'
        )


@pytest.mark.django_db
class TestResortWhyItMatters:
    """The curated "why it matters" line on the resort page (SNOW-542).

    The field is curated over time rather than in one pass, so all three
    blank branches are behaviour worth pinning: staff get a curation hint,
    anonymous visitors get a register prompt (the empty slot is the most
    natural place on the page to ask for a sign-up), and a signed-in reader
    — who has no way to contribute copy — gets nothing at all.
    """

    def test_curated_line_renders(self) -> None:
        """A populated line renders as prose under the heading."""
        resort = ResortFactory.create(
            why_it_matters="High plateau above Adelboden, own cable car."
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        html = response.content.decode()
        assert 'data-testid="resort-why-it-matters"' in html
        assert "High plateau above Adelboden, own cable car." in html

    def test_blank_line_anonymous_prompts_register(self) -> None:
        """An anonymous visitor sees the register prompt, naming the resort."""
        resort = ResortFactory.create(name="Haldigrat")

        client = Client()
        response = client.get(resort.get_absolute_url())

        html = response.content.decode()
        assert 'data-testid="resort-why-it-matters-signup"' in html
        assert reverse("accounts:register") in html

    def test_blank_line_staff_shows_curation_hint(self) -> None:
        """Staff see the curation hint instead of the register prompt."""
        resort = ResortFactory.create()

        client = Client()
        client.force_login(UserFactory.create(is_staff=True))
        response = client.get(resort.get_absolute_url())

        html = response.content.decode()
        assert 'data-testid="resort-why-it-matters-hint"' in html
        assert "resort-why-it-matters-signup" not in html

    def test_blank_line_signed_in_reader_sees_nothing(self) -> None:
        """A signed-in non-staff reader gets no prompt — there is nowhere to send them."""
        resort = ResortFactory.create()

        client = Client()
        client.force_login(UserFactory.create(is_staff=False))
        response = client.get(resort.get_absolute_url())

        assert "resort-why-it-matters" not in response.content.decode()


@pytest.mark.django_db
class TestResortFacts:
    """The curated Resort columns the page stored but never rendered (SNOW-695).

    Unlike ``_resort_meta_row.html`` — the map popup's row, which keeps a
    dashed placeholder so missing curation stays visible to staff — this
    block omits an unset cell entirely and omits the whole container when
    nothing is curated. It is a public detail page, not a curation surface.
    """

    @staticmethod
    def _fully_curated() -> dict[str, object]:
        """Return kwargs setting every field the facts block reads."""
        return {
            "operator_name": "Zermatt Bergbahnen AG",
            "website": "https://www.zermatt.ch/",
            "notes": "Lift-served access to the Theodul glacier.",
            "num_lifts": 34,
            "num_runs": 53,
            "total_piste_km": 196.5,
            "base_elevation_m": 1620,
            "top_elevation_m": 3899,
            "typical_season_open": "11-23",
            "typical_season_close": "04-27",
        }

    def test_every_curated_field_renders(self) -> None:
        """A fully curated resort renders each cell."""
        resort = ResortFactory.create(**self._fully_curated())

        client = Client()
        content = client.get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-facts"' in content
        for testid in (
            "resort-facts-elevation",
            "resort-facts-lifts",
            "resort-facts-runs",
            "resort-facts-piste",
            "resort-facts-season",
            "resort-facts-operator",
            "resort-facts-website",
            "resort-facts-notes",
        ):
            assert f'data-testid="{testid}"' in content, testid
        assert "1,620–3,899 m" in content
        assert "34" in content
        assert "196.5 km" in content
        assert "23 Nov – 27 Apr" in content
        assert "Zermatt Bergbahnen AG" in content
        assert "Theodul glacier" in content
        # Compare the rendered href exactly rather than asking whether the
        # URL appears somewhere on the page: the loose check would pass on a
        # link pointing anywhere that merely contained this string.
        website_cell = content.split('data-testid="resort-facts-website"')[1]
        href = re.search(r'href="([^"]+)"', website_cell)
        assert href is not None
        assert href.group(1) == self._fully_curated()["website"]

    def test_nothing_curated_omits_the_whole_block(self) -> None:
        """No curated field renders no container — not an empty one.

        The factory's defaults are the uncurated state, which is the
        majority case for the fixture: this is the assertion that keeps a
        thin resort page from growing an empty box.
        """
        resort = ResortFactory.create()

        client = Client()
        content = client.get(resort.get_absolute_url()).content.decode()

        assert "resort-facts" not in content
        # The page itself still renders.
        assert 'data-testid="resort-detail"' in content

    def test_partial_curation_renders_only_the_cells_it_has(self) -> None:
        """A base elevation with no top degrades to a one-sided reading."""
        resort = ResortFactory.create(
            num_lifts=4,
            base_elevation_m=1343,
            top_elevation_m=None,
            typical_season_open="12-14",
            typical_season_close="",
        )

        client = Client()
        content = client.get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-facts"' in content
        assert 'data-testid="resort-facts-elevation"' in content
        assert "from 1,343 m" in content
        assert "from 14 Dec" in content
        # Nothing curated for these, so no cell at all.
        assert "resort-facts-runs" not in content
        assert "resort-facts-piste" not in content
        assert "resort-facts-operator" not in content
        assert "resort-facts-website" not in content
        assert "resort-facts-notes" not in content

    def test_facts_are_a_definition_list_under_their_own_eyebrow(self) -> None:
        """One <dl>, a <dt>/<dd> per row; the eyebrow sits above the card."""
        resort = ResortFactory.create(**self._fully_curated())

        content = Client().get(resort.get_absolute_url()).content.decode()
        block = content[content.index('data-testid="resort-facts"') :]
        block = block[: block.index("</section>")]

        assert block.count("<dl") == 1
        assert block.count("<dt") == 8
        assert block.count("<dd") == 8
        heading = block.index('id="resort-facts-heading"')
        assert "About this resort" in block[heading : block.index("<dl")]
        assert heading < block.index("rounded-card")

    def test_website_row_reads_official_website_with_an_external_mark(
        self,
    ) -> None:
        """The value is the words, underlined, with the external-link glyph."""
        resort = ResortFactory.create(website="https://www.verbier.ch/")

        content = Client().get(resort.get_absolute_url()).content.decode()
        row = content.split('data-testid="resort-facts-website"')[1]
        row = row[: row.index("</dd>")]

        assert "Official website" in row
        assert "underline" in row
        assert 'target="_blank"' in row
        assert "M14 4h6v6" in row  # includes/_icon_external_link.html

    def test_notes_is_a_full_width_left_aligned_row(self) -> None:
        """Notes drops the label/value split and reads as prose."""
        resort = ResortFactory.create(notes="Glacier access from the top lift.")

        content = Client().get(resort.get_absolute_url()).content.decode()
        row = content.split('data-testid="resort-facts-notes"')[1]
        row = row[: row.index("</dd>")]

        assert "Glacier access from the top lift." in row
        assert "text-left" in row
        assert (
            "justify-between"
            not in content.split('data-testid="resort-facts-notes"')[0].rsplit(
                "<div", 1
            )[1]
        )

    def test_no_notes_means_no_notes_row(self) -> None:
        """An unset notes field renders no row, even with other facts set."""
        resort = ResortFactory.create(num_lifts=12, notes="")

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-facts-lifts"' in content
        assert "resort-facts-notes" not in content

    def test_zero_lifts_renders_rather_than_vanishing(self) -> None:
        """A genuine zero is a fact; the cells test `is not None`, not truthiness."""
        resort = ResortFactory.create(num_lifts=0, total_piste_km=0.0)

        client = Client()
        content = client.get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-facts-lifts"' in content
        assert 'data-testid="resort-facts-piste"' in content
        assert "0 km" in content


@pytest.mark.django_db
class TestResortDatedPage:
    """SNOW-1164: the page takes a day, as the bulletin page does."""

    _DAY = datetime.date(2026, 2, 3)

    def _dated_url(self, resort: Resort, day: datetime.date | str) -> str:
        """Reverse the dated resort URL."""
        date_str = day if isinstance(day, str) else day.isoformat()
        return reverse(
            "public:resort_date", kwargs={"slug": resort.slug, "date_str": date_str}
        )

    def test_get_absolute_url_names_another_day(self) -> None:
        resort = ResortFactory.create()
        assert resort.get_absolute_url(self._DAY) == self._dated_url(resort, self._DAY)
        assert resort.get_absolute_url(timezone.localdate()) == reverse(
            "public:resort", kwargs={"slug": resort.slug}
        )

    def test_todays_dated_url_redirects_to_the_bare_one(self) -> None:
        resort = ResortFactory.create()
        response = Client().get(self._dated_url(resort, timezone.localdate()))
        assert response.status_code == 302
        assert response["Location"] == resort.get_absolute_url()

    def test_a_malformed_day_is_404(self) -> None:
        resort = ResortFactory.create()
        assert Client().get(self._dated_url(resort, "not-a-day")).status_code == 404

    def test_another_day_links_that_days_bulletin(self) -> None:
        """The region row links the region's page for the day shown."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        content = Client().get(self._dated_url(resort, self._DAY)).content.decode()
        assert f'href="{region.get_absolute_url(self._DAY)}"' in content
        assert 'data-testid="day-windows-panel"' not in content

    def test_another_day_is_noindex_and_today_is_not(self) -> None:
        resort = ResortFactory.create()
        dated = Client().get(self._dated_url(resort, self._DAY)).content.decode()
        bare = Client().get(resort.get_absolute_url()).content.decode()
        assert 'name="robots" content="noindex' in dated
        assert 'name="robots"' not in bare

    def test_another_day_links_back_to_today(self) -> None:
        """SNOW-1165: the strip names the day and links to the bare URL."""
        resort = ResortFactory.create()
        content = Client().get(self._dated_url(resort, self._DAY)).content.decode()
        start = content.index('data-testid="another-day-strip"')
        strip = content[start : content.index("</div>", start)]
        assert "Showing Tue 3 Feb 2026" in strip
        assert f'<a href="{resort.get_absolute_url()}"' in strip
        assert "Back to today" in strip
        # Above the page, not inside the shared body.
        assert start < content.index('data-testid="resort-detail"')

    def test_today_has_no_strip(self) -> None:
        """SNOW-1165: the bare URL is today, so it carries no strip."""
        resort = ResortFactory.create()
        content = Client().get(resort.get_absolute_url()).content.decode()
        assert 'data-testid="another-day-strip"' not in content
