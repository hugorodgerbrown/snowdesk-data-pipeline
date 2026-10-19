"""
tests/public/test_resort_detail.py — Tests for the resort detail page (SNOW-504).

Covers ``apps.public.views.resort_detail`` (``/resorts/<slug>/``, SNOW-796):
  - 200 + content: resort name, canton, parent region name, today's danger
    chip, and a "View bulletin" link to the region's evergreen bulletin.
  - The pre-slug ``/resorts/<id>/<slug>/`` form 301s to the canonical URL
    whatever suffix it carries (``resort_legacy_redirect``), and does not
    loop.
  - A resort with ``needs_review=True`` or null coordinates still renders.
  - An unknown slug returns 404.
  - Favourite-star state: sign-in CTA for anonymous/ineligible visitors,
    the button (unfavourited or favourited) for eligible ones.
  - SNOW-807: the danger area is one link to the bulletin; each curated
    location is a link to its weather page; the observations section is one link to the map with the reports sheet
    open, flown to the resort.
  - SNOW-1125: each location row carries today's brief one-day weather
    (icon, condition, high/low, snowfall, wind — no freezing level, no sun
    times), read in one batched query; no row today means the link alone.
  - SNOW-855: the meta description branches on whether the resort has any
    curated locations — it named a village, a mid-station and a summit for
    every resort, including those with no locations at all.
  - SNOW-1122: the title and description name the resort beside the terms
    people search for, "weather" only when there are forecasts, and today's
    danger in words only when rated.
  - SNOW-1123: the schema.org SkiResort JSON-LD — core properties, optional
    ones omitted when unset, and a ``</script>`` that cannot break out.
  - Resort facts block (SNOW-695): the curated Resort columns the page
    stored but never rendered. Every cell renders when curated, an unset
    cell is omitted, and the whole block is omitted rather than rendered
    empty when nothing is curated at all.
"""

from __future__ import annotations

import datetime
import html
import json
import re
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from freezegun import freeze_time

from apps.bulletins.models import RegionDayRating
from apps.regions.models import Resort
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortFactory,
    ResortLocationFactory,
    UserFactory,
    WeatherFactory,
)


@pytest.mark.django_db
class TestResortDetailContent:
    """200 response with the expected content."""

    def test_returns_200_with_resort_and_region_content(self) -> None:
        """The page shows the resort name, canton, and parent region name."""
        region = MicroRegionFactory.create(name="Martigny – Verbier")
        resort = ResortFactory.create(name="Verbier", region=region, canton="VS")

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200
        content = response.content.decode()
        assert "Verbier" in content
        assert "VS" in content
        assert "Martigny" in content

    def test_includes_name_alt_when_set(self) -> None:
        """An alternative/marketing name renders when set."""
        resort = ResortFactory.create(name="Verbier", name_alt="4 Vallées")

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert "4 Vallées" in response.content.decode()

    def test_shows_todays_danger_chip_when_rating_exists(self) -> None:
        """Today's RegionDayRating renders the danger-tile chip."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        RegionDayRatingFactory.create(
            region=region, max_rating=RegionDayRating.Rating.CONSIDERABLE
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-danger-chip"' in content

    def test_shows_no_rating_note_when_no_bulletin(self) -> None:
        """No RegionDayRating for today → the no-rating note renders."""
        resort = ResortFactory.create()

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-no-rating"' in content
        assert 'data-testid="resort-danger-chip"' not in content

    def test_includes_bulletin_link(self) -> None:
        """A "View bulletin" link points at the parent region's bulletin page."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert region.get_absolute_url() in response.content.decode()

    @pytest.mark.parametrize("rated", [True, False])
    def test_the_danger_area_is_one_link_to_the_bulletin(self, rated: bool) -> None:
        """SNOW-807: chip or note, the whole area is one tappable link.

        tests/e2e/test_resort_page.py clicks ``[data-testid="resort-danger"] a``,
        so the container must hold exactly one anchor whichever state it is in.
        """
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        if rated:
            RegionDayRatingFactory.create(
                region=region, max_rating=RegionDayRating.Rating.CONSIDERABLE
            )

        content = Client().get(resort.get_absolute_url()).content.decode()

        area = re.search(
            r'<div[^>]*data-testid="resort-danger"[^>]*>(.*?)</div>', content, re.S
        )
        assert area is not None
        assert len(re.findall(r"<a\b", area.group(1))) == 1
        assert f'href="{region.get_absolute_url()}"' in area.group(1)
        assert "View bulletin" in area.group(1)


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


@pytest.mark.django_db
class TestResortLocations:
    """SNOW-807: the resort's curated locations, each a link to its weather page."""

    def test_each_curated_location_links_to_its_weather_page(self) -> None:
        """Primary first, then role and name; label, role and elevation on the row."""
        resort = ResortFactory.create(name="Verbier")
        summit = LocationFactory.create(
            name="Mont Fort", kind="PEAK", elevation_m=3328.0
        )
        village = LocationFactory.create(
            name="Verbier village", kind="VILLAGE", elevation_m=1500.0
        )
        ResortLocationFactory.create(resort=resort, location=summit, role="TOP")
        ResortLocationFactory.create(
            resort=resort, location=village, role="BASE", is_primary=True
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-locations"' in content
        first = content.index('data-testid="resort-location-0"')
        second = content.index('data-testid="resort-location-1"')
        assert first < second
        assert f'href="{village.get_absolute_url()}"' in content[first:second]
        assert "Verbier village" in content[first:second]
        assert "Base" in content[first:second]
        assert "1500 m" in content[first:second]
        assert f'href="{summit.get_absolute_url()}"' in content[second:]
        assert "3328 m" in content[second:]

    def test_an_anonymous_pin_is_labelled_with_the_resorts_name(self) -> None:
        """The location link_resort_locations mints has no name of its own."""
        resort = ResortFactory.create(name="Verbier")
        ResortLocationFactory.create(
            resort=resort, location=LocationFactory.create(anonymous=True)
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert re.search(r'data-testid="resort-location-0-link"\s*>Verbier<', content)

    def test_no_curated_locations_means_no_section(self) -> None:
        """A freshly geocoded resort renders no heading over an empty list."""
        resort = ResortFactory.create(geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-locations"' not in content
        assert "Forecasts" not in content


# SNOW-1125: a fixed winter day. 11:00 UTC is midday in Europe/Zurich and
# 21:00 UTC is evening on the same local date, so ``timezone.localdate()``
# (the day the view reads) is TODAY under both.
TODAY = datetime.date(2026, 1, 15)
MIDDAY = "2026-01-15T11:00:00+00:00"
EVENING = "2026-01-15T21:00:00+00:00"
SUNRISE = datetime.datetime(2026, 1, 15, 7, 0, tzinfo=datetime.UTC)
SUNSET = datetime.datetime(2026, 1, 15, 16, 0, tzinfo=datetime.UTC)


def _todays_weather(location: Any, **overrides: Any) -> Any:
    """Create today's Weather row for a location with fixed sun times."""
    fields: dict[str, Any] = {
        "location": location,
        "observed_on": TODAY,
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
    }
    fields.update(overrides)
    return WeatherFactory.create(**fields)


def _resort_with_locations(count: int) -> Resort:
    """Return a resort with ``count`` linked locations, each with a row today."""
    resort = ResortFactory.create()
    for index in range(count):
        location = LocationFactory.create(
            name=f"Point {index}", elevation_m=1500.0 + index * 500
        )
        ResortLocationFactory.create(
            resort=resort, location=location, is_primary=index == 0
        )
        _todays_weather(location)
    return resort


@pytest.mark.django_db
class TestResortLocationWeather:
    """SNOW-1125: each location row carries today's brief one-day weather."""

    @freeze_time(MIDDAY)
    def test_a_location_with_todays_row_shows_its_day(self) -> None:
        """Condition, high/low, snowfall and wind beside the forecast link."""
        resort = ResortFactory.create()
        location = LocationFactory.create(name="Mont Fort", elevation_m=3328.0)
        ResortLocationFactory.create(resort=resort, location=location)
        _todays_weather(location)

        content = Client().get(resort.get_absolute_url()).content.decode()

        row = content[content.index('data-testid="resort-location-0"') :]
        row = row[: row.index("</li>")]
        assert f'href="/weather/{location.short_id}/"' in row
        assert 'data-testid="resort-location-0-weather-panel"' in row
        assert "clear-day.svg" in row
        assert "Clear" in row
        assert "4&deg;" in row
        assert "-6&deg;" in row
        assert "12 cm" in row
        assert 'data-testid="resort-location-0-weather-wind"' in row
        assert "35 gusting 60 km/h" in row

    @freeze_time(MIDDAY)
    def test_freezing_level_and_sun_times_are_left_to_the_forecast_page(
        self,
    ) -> None:
        """The brief panel drops what belongs to the location's own page."""
        resort = _resort_with_locations(1)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-location-0-weather-panel"' in content
        assert "freezing-level" not in content
        assert "1900 m" not in content
        assert "icons/weather/sunrise" not in content
        assert "07:00" not in content

    @freeze_time(MIDDAY)
    def test_a_location_with_no_row_today_keeps_its_link_alone(self) -> None:
        """Yesterday's row is not today's: the link renders, no panel."""
        resort = ResortFactory.create()
        location = LocationFactory.create(name="Mont Fort")
        ResortLocationFactory.create(resort=resort, location=location)
        _todays_weather(location, observed_on=datetime.date(2026, 1, 14))

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-location-0-link"' in content
        assert f'href="{location.get_absolute_url()}"' in content
        assert "resort-location-0-weather" not in content
        assert "weather-panel" not in content

    @freeze_time(EVENING)
    def test_after_sunset_the_night_icon_is_drawn(self) -> None:
        """The day/night decision reads the request's own instant."""
        resort = _resort_with_locations(1)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert "clear-night.svg" in content
        assert "clear-day.svg" not in content
        assert 'data-time-of-day="night"' in content

    @freeze_time(MIDDAY)
    def test_the_query_count_does_not_grow_with_the_locations(
        self, django_assert_num_queries: Any
    ) -> None:
        """One batched Weather read, whatever the number of locations."""
        one = _resort_with_locations(1)
        three = _resort_with_locations(3)
        client = Client()
        # Warm any per-process caches (waffle flags, content types) first.
        client.get(_resort_with_locations(2).get_absolute_url())

        with CaptureQueriesContext(connection) as single:
            client.get(one.get_absolute_url())
        with django_assert_num_queries(len(single.captured_queries)):
            content = client.get(three.get_absolute_url()).content.decode()

        assert content.count("-weather-panel") == 3


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

    def test_a_blank_role_location_promises_forecasts_without_naming_tiers(
        self,
    ) -> None:
        """An auto-linked resort has no roles, so it can name no tiers either."""
        resort = ResortFactory.create(name="Verbier")
        ResortLocationFactory.create(
            resort=resort, location=LocationFactory.create(anonymous=True), role=""
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = _meta_description(content)
        assert "forecast" in description
        assert "village, mid-station and summit" not in description

    def test_no_locations_names_the_bulletin_and_claims_no_forecast(self) -> None:
        """No Forecasts section means no forecast promise in the description."""
        region = MicroRegionFactory.create(name="Val de Bagnes")
        resort = ResortFactory.create(name="Verbier", region=region, geocoded=True)

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
            "Adelboden conditions — avalanche bulletin & weather · Snowdesk"
        )

    def test_title_without_locations_does_not_promise_weather(self) -> None:
        """No Forecasts section, so "weather" stays out of the title."""
        resort = ResortFactory.create(name="Adelboden")

        content = Client().get(resort.get_absolute_url()).content.decode()

        title = _page_title(content)
        assert title.startswith("Adelboden avalanche conditions & bulletin · Snowdesk")
        assert "weather" not in title

    def test_description_names_todays_danger_when_rated(self) -> None:
        """A rating for today puts the level in words into the description."""
        resort = ResortFactory.create(name="Adelboden")
        RegionDayRatingFactory.create(
            region=resort.region, max_rating=RegionDayRating.Rating.CONSIDERABLE
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = _meta_description(content)
        assert description.startswith("Adelboden avalanche conditions")
        assert "Danger today: 3 – Considerable." in description

    def test_description_omits_danger_when_unrated(self) -> None:
        """No rating today, no danger claim — and no stray double space."""
        resort = ResortFactory.create(name="Adelboden")

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = _meta_description(content)
        assert "Danger today" not in description
        assert "  " not in description

    def test_description_omits_danger_for_a_no_rating_row(self) -> None:
        """A "no_rating" row exists but has no level, so no danger claim."""
        resort = ResortFactory.create(name="Adelboden")
        RegionDayRatingFactory.create(
            region=resort.region, max_rating=RegionDayRating.Rating.NO_RATING
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        description = _meta_description(content)
        assert "Danger today" not in description
        assert "–" not in description

    def test_rated_forecast_description_fits_a_search_snippet(self) -> None:
        """The longest branch stays inside the ~155-character snippet budget."""
        region = MicroRegionFactory.create(name="Bernese Oberland")
        resort = ResortFactory.create(name="Adelboden", region=region)
        ResortLocationFactory.create(
            resort=resort,
            location=LocationFactory.create(name="Adelboden", kind="VILLAGE"),
            role="BASE",
        )
        RegionDayRatingFactory.create(
            region=region, max_rating=RegionDayRating.Rating.CONSIDERABLE
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert len(html.unescape(_meta_description(content))) <= 155

    def test_body_carries_the_conditions_subheading(self) -> None:
        """The target terms appear in the visible page, not only the meta tags."""
        resort = ResortFactory.create(name="Adelboden")

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert "Avalanche danger and mountain conditions" in content


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


@pytest.mark.django_db
class TestResortObservationsLink:
    """SNOW-807: reports are on the map, with the sheet open and the camera here."""

    def test_links_to_the_map_with_the_reports_sheet_open_at_this_resort(
        self,
    ) -> None:
        resort = ResortFactory.create(name="Verbier", geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-observations"' in content
        assert f'href="/map/?panel=reports&amp;resort={resort.slug}"' in content
        assert "Reported nearby" not in content
        assert "Reported in this region" not in content


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
        assert "1620&ndash;3899 m" in content
        assert "34" in content
        assert "196.5 km" in content
        assert "23 Nov&ndash;27 Apr" in content
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
        assert "from 1343 m" in content
        assert "from 14 Dec" in content
        # Nothing curated for these, so no cell at all.
        assert "resort-facts-runs" not in content
        assert "resort-facts-piste" not in content
        assert "resort-facts-operator" not in content
        assert "resort-facts-website" not in content
        assert "resort-facts-notes" not in content

    def test_zero_lifts_renders_rather_than_vanishing(self) -> None:
        """A genuine zero is a fact; the cells test `is not None`, not truthiness."""
        resort = ResortFactory.create(num_lifts=0, total_piste_km=0.0)

        client = Client()
        content = client.get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-facts-lifts"' in content
        assert 'data-testid="resort-facts-piste"' in content
        assert "0 km" in content
