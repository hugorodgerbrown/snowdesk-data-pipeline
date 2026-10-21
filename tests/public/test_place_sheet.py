"""
tests/public/test_place_sheet.py — the phone place sheet's body
(apps.public.place_sheet, SNOW-1100).

Covers both views, ``resort_sheet_partial`` and ``favourite_sheet_partial``:
  - The guards: a plain GET is a 400 (require_htmx), an unknown resort a
    404, a malformed ``?d=`` a 400; a pin is owner-only, so another reader's
    pin and an anonymous request are both a 404.
  - PEEK: the name, the danger tile and the region badge in the
    ``data-sheet-peek`` header; a resort's Save star (or the sign-in star
    when anonymous); a saved pin's "…" menu and inline-rename row.
  - The detents the body allows: all three when there is something for
    FULL to show, peek and half otherwise.
  - A pin's problems are judged against its Location's height, the pin's
    own legacy height only as a fallback.
  - HALF: a resort's "why it matters" line only when written, its one-line
    fact summary, then the day's weather; a forward day reads today's row
    and shows that row's forecast entry for the day. No week (SNOW-1161).
  - FULL: the problems and the links on — the day's bulletin first, then
    the resort page — and the forecast link carrying the day. No facts
    list and no dashed placeholder (SNOW-1161).
  - The map page carries the sheet, its script and the URL templates.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

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


def _resort_url(slug: str | None, day: str | None = None) -> str:
    """Build the resort sheet URL, with an optional ``?d=``."""
    assert slug is not None
    url = reverse("public:resort_sheet_partial", kwargs={"slug": slug})
    return f"{url}?d={day}" if day else url


def _favourite_url(uuid: Any, day: str | None = None) -> str:
    """Build the favourite sheet URL, with an optional ``?d=``."""
    url = reverse("public:favourite_sheet_partial", kwargs={"uuid": uuid})
    return f"{url}?d={day}" if day else url


def _get(client: Client, url: str) -> Any:
    """GET as the sheet script does."""
    return client.get(url, HTTP_HX_REQUEST="true")


def _forecast_day(day: datetime.date) -> dict[str, object]:
    """Build one ``forecast[]`` entry for ``day``."""
    iso = day.isoformat()
    return {
        "date": iso,
        "weather_code": 71,
        "sunrise": f"{iso}T06:30:00+02:00",
        "sunset": f"{iso}T20:15:00+02:00",
        "temperature_2m_max": 3.0,
        "temperature_2m_min": -5.0,
        "snowfall_sum": 4.0,
    }


def _resort(**kwargs: Any) -> Resort:
    """A resort in a fresh region, with a primary village location."""
    region = MicroRegionFactory.create()
    resort = ResortFactory.create(region=region, name="Verbier", **kwargs)
    location = LocationFactory.create(elevation_m=1500.0)
    ResortLocationFactory.create(resort=resort, location=location, is_primary=True)
    return resort


def _primary_location(resort: Resort) -> Any:
    """Return the resort's primary location."""
    return resort.resort_locations.get(is_primary=True).location


@pytest.mark.django_db
class TestGuards:
    """The request guards."""

    def test_plain_get_is_rejected(self, client: Client) -> None:
        resort = _resort()
        assert client.get(_resort_url(resort.slug)).status_code == 400

    def test_unknown_resort_is_404(self, client: Client) -> None:
        assert _get(client, _resort_url("nowhere")).status_code == 404

    def test_malformed_date_is_400(self, client: Client) -> None:
        resort = _resort()
        response = _get(client, _resort_url(resort.slug, "not-a-date"))
        assert response.status_code == 400

    def test_responses_are_never_cached(self, client: Client) -> None:
        resort = _resort()
        response = _get(client, _resort_url(resort.slug))
        assert response["Cache-Control"] == "private, no-store"

    def test_anonymous_pin_is_404(self, client: Client) -> None:
        favourite = FavouriteFactory.create()
        assert _get(client, _favourite_url(favourite.uuid)).status_code == 404

    def test_another_readers_pin_is_404(self, client: Client) -> None:
        favourite = FavouriteFactory.create()
        client.force_login(UserFactory.create())
        assert _get(client, _favourite_url(favourite.uuid)).status_code == 404

    def test_pin_malformed_date_is_400(self, client: Client) -> None:
        favourite = FavouriteFactory.create()
        client.force_login(favourite.user)
        response = _get(client, _favourite_url(favourite.uuid, "nope"))
        assert response.status_code == 400


@pytest.mark.django_db
class TestResortPeek:
    """A resort's peek row: name, rating, region and the Save star."""

    def test_header_names_the_resort_and_its_regions_level(
        self, client: Client
    ) -> None:
        resort = _resort()
        RegionDayRatingFactory.create(
            region=resort.region, date=timezone.localdate(), max_rating="considerable"
        )
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-place-kind="resort"' in content
        assert "data-sheet-peek" in content
        assert "Verbier" in content
        assert 'data-testid="place-sheet-rating"' in content
        assert "Considerable" in content
        assert resort.region.region_id in content

    def test_anonymous_reader_gets_the_sign_in_star(self, client: Client) -> None:
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert "data-resort-star" not in content
        assert f'href="{reverse("accounts:sign_in")}"' in content

    def test_signed_in_reader_gets_the_save_star(self, client: Client) -> None:
        resort = _resort()
        client.force_login(UserFactory.create())
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert "data-resort-star" in content
        assert 'data-favourited="false"' in content

    def test_a_saved_resort_shows_the_filled_star(self, client: Client) -> None:
        resort = _resort()
        user = UserFactory.create()
        favourite = FavouriteFactory.create(user=user, resort=resort)
        client.force_login(user)
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-favourited="true"' in content
        assert f'data-favourite-uuid="{favourite.uuid}"' in content

    def test_resort_has_no_row_menu(self, client: Client) -> None:
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert "place-sheet-actions" not in content
        assert "data-row-renameable" not in content


@pytest.mark.django_db
class TestFavouritePeek:
    """A saved pin's peek row: the name and the panel row's own menu."""

    def test_header_carries_the_row_menu_and_rename(self, client: Client) -> None:
        favourite = FavouriteFactory.create(name="Lac des Vaux")
        client.force_login(favourite.user)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert 'data-place-kind="favourite"' in content
        assert "Lac des Vaux" in content
        assert 'id="place-sheet-row"' in content
        assert "data-row-renameable" in content
        assert "data-row-rename-input" in content
        assert 'id="place-sheet-actions"' in content
        assert "data-row-remove" in content

    def test_unnamed_pin_is_labelled(self, client: Client) -> None:
        favourite = FavouriteFactory.create(name="")
        client.force_login(favourite.user)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert "Unnamed pin" in content

    def test_uncovered_pin_says_so(self, client: Client) -> None:
        favourite = FavouriteFactory.create(region=None)
        client.force_login(favourite.user)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert "No avalanche bulletin covers this spot" in content


@pytest.mark.django_db
class TestProblemRelevance:
    """A pin's problems are judged against its Location's height."""

    def _annotated_with(self, client: Client, favourite: Any) -> Any:
        """Render the pin's sheet and return the height the problems got."""
        client.force_login(favourite.user)
        with (
            mock.patch(
                "apps.public.place_sheet.select_bulletin_for_date",
                return_value=object(),
            ),
            mock.patch(
                "apps.public.place_sheet.problem_cards_for_bulletin",
                return_value=[],
            ),
            mock.patch(
                "apps.public.place_sheet.annotate_problem_relevance",
                return_value=[],
            ) as annotate,
        ):
            _get(client, _favourite_url(favourite.uuid))
        return annotate.call_args.args[1]

    def test_the_locations_height_wins(self, client: Client) -> None:
        favourite = FavouriteFactory.create(
            region=MicroRegionFactory.create(), elevation=None
        )
        assert favourite.location is not None
        favourite.location.elevation_m = 2410.0
        favourite.location.save()
        assert self._annotated_with(client, favourite) == 2410.0

    def test_the_pins_own_height_is_the_fallback(self, client: Client) -> None:
        favourite = FavouriteFactory.create(
            region=MicroRegionFactory.create(), elevation=1800.0
        )
        assert favourite.location is not None
        favourite.location.elevation_m = None
        favourite.location.save()
        assert self._annotated_with(client, favourite) == 1800.0


@pytest.mark.django_db
class TestDetents:
    """FULL needs something to reveal; without it the sheet stops at half."""

    def test_resort_allows_all_three(self, client: Client) -> None:
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-sheet-detents="peek,half,full"' in content

    def test_bare_pin_stops_at_half(self, client: Client) -> None:
        favourite = FavouriteFactory.create(location=None, region=None)
        client.force_login(favourite.user)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert 'data-sheet-detents="peek,half"' in content


@pytest.mark.django_db
class TestWeather:
    """HALF ends under the day's weather; FULL carries the week."""

    def test_todays_row_is_the_half_detents_end(self, client: Client) -> None:
        resort = _resort()
        WeatherFactory.create(location=_primary_location(resort))
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-testid="place-sheet-weather" data-sheet-half' in content
        assert "1500 m" in content
        # SNOW-1161: the week is the weather page's, not the sheet's.
        assert 'data-testid="place-sheet-week"' not in content
        assert 'data-testid="place-sheet-forecast-link"' in content

    def test_forward_day_reads_todays_forecast(self, client: Client) -> None:
        resort = _resort()
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        WeatherFactory.create(
            location=_primary_location(resort), forecast=[_forecast_day(tomorrow)]
        )
        content = _get(
            client, _resort_url(resort.slug, tomorrow.isoformat())
        ).content.decode()
        assert "place-sheet-forward-day" in content
        assert 'data-testid="place-sheet-week"' not in content
        assert "Forecast ·" in content

    def test_forward_day_beyond_the_forecast_has_no_day(self, client: Client) -> None:
        resort = _resort()
        WeatherFactory.create(location=_primary_location(resort))
        later = timezone.localdate() + datetime.timedelta(days=3)
        content = _get(client, _resort_url(resort.slug, later.isoformat())).content
        assert b"place-sheet-forward-day" not in content
        assert b'data-testid="place-sheet-weather"' not in content

    def test_no_row_means_no_weather(self, client: Client) -> None:
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-testid="place-sheet-weather"' not in content
        assert 'data-testid="place-sheet-week"' not in content


@pytest.mark.django_db
class TestFull:
    """FULL: a resort's details and the links onward."""

    def test_resort_shows_its_summary_and_page(self, client: Client) -> None:
        resort = _resort(
            base_elevation_m=1500,
            top_elevation_m=3330,
            num_lifts=34,
            total_piste_km=210,
            operator_name="",
        )
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert (
            'data-testid="place-sheet-facts">1,500–3,330 m · 34 lifts · 210 km piste<'
        ) in content
        assert f'href="{resort.get_absolute_url()}"' in content

    def test_resort_has_no_facts_list_and_no_placeholders(self, client: Client) -> None:
        """SNOW-1161: the full list is the page's; no blank is ever drawn."""
        resort = _resort(
            num_lifts=6,
            total_piste_km=35,
            operator_name="",
            website="",
            num_runs=None,
            base_elevation_m=None,
            top_elevation_m=None,
        )
        client.force_login(UserFactory.create(is_staff=True))
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-testid="place-sheet-details"' not in content
        assert "About this resort" not in content
        assert "border-dashed" not in content
        assert "Add operator name" not in content
        assert "6 lifts · 35 km piste" in content

    def test_a_resort_with_no_facts_has_no_summary(self, client: Client) -> None:
        resort = _resort(
            num_lifts=None,
            num_runs=None,
            total_piste_km=None,
            base_elevation_m=None,
            top_elevation_m=None,
        )
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-testid="place-sheet-facts"' not in content

    def test_written_why_it_matters_shows(self, client: Client) -> None:
        resort = _resort(why_it_matters="Four lifts, national freeride reputation.")
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert "Four lifts, national freeride reputation." in content

    @pytest.mark.parametrize("is_staff", [None, False, True])
    def test_unwritten_why_it_matters_shows_nothing(
        self, client: Client, is_staff: bool | None
    ) -> None:
        """SNOW-1161: the blank states (prompt, hint) are the page's alone."""
        resort = _resort(why_it_matters="")
        if is_staff is not None:
            client.force_login(UserFactory.create(is_staff=is_staff))
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert "resort-why-it-matters" not in content

    def test_forecast_link_carries_the_day(self, client: Client) -> None:
        resort = _resort()
        day = datetime.date(2026, 2, 3)
        content = _get(client, _resort_url(resort.slug, day.isoformat())).content
        location = _primary_location(resort)
        assert f"{location.get_absolute_url()}?date=2026-02-03".encode() in content

    def test_a_pinned_resort_reads_its_pin_not_its_primary_link(
        self, client: Client
    ) -> None:
        """SNOW-1144: the sheet's weather location is the resort's pin."""
        resort = _resort(latitude=46.1, longitude=7.4)
        assert resort.location is not None
        day = datetime.date(2026, 2, 3)
        content = _get(client, _resort_url(resort.slug, day.isoformat())).content
        pin_url = f"{resort.location.get_absolute_url()}?date=2026-02-03"
        village_url = f"{_primary_location(resort).get_absolute_url()}?date="
        assert pin_url.encode() in content
        assert village_url.encode() not in content

    def test_forward_day_links_to_the_day_itself(self, client: Client) -> None:
        """The page opens a forward day inside today's row (SNOW-1105)."""
        resort = _resort()
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        content = _get(client, _resort_url(resort.slug, tomorrow.isoformat())).content
        location = _primary_location(resort)
        assert f"{location.get_absolute_url()}?date={tomorrow}".encode() in content

    def test_location_without_a_page_gets_no_forecast_link(
        self, client: Client
    ) -> None:
        resort = _resort()
        location = _primary_location(resort)
        location.short_id = None
        location.save()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert "Full forecast" not in content
        assert 'href="?date=' not in content

    def test_rated_day_links_to_the_dated_bulletin(self, client: Client) -> None:
        resort = _resort()
        day = datetime.date(2026, 2, 3)
        RegionDayRatingFactory.create(
            region=resort.region, date=day, max_rating="moderate"
        )
        content = _get(
            client, _resort_url(resort.slug, day.isoformat())
        ).content.decode()
        assert f'href="{resort.region.get_absolute_url(day)}"' in content
        assert f"Read the {resort.region.name} bulletin for 3 Feb" in content
        # The bulletin is the primary button and comes before the resort.
        links = content[content.index('data-testid="place-sheet-links"') :]
        assert links.index(resort.region.get_absolute_url(day)) < links.index(
            resort.get_absolute_url()
        )

    def test_unrated_covered_day_says_no_bulletin(self, client: Client) -> None:
        """SNOW-1161: the peek row says it, with the page's empty tile."""
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        peek = content[
            : content.index("data-sheet-half")
            if "data-sheet-half" in content
            else len(content)
        ]
        assert 'data-testid="danger-tile-empty"' in peek
        assert "No bulletin ·" in peek
        assert "No bulletin available for" not in content
        assert "bulletin for" not in content

    def test_saved_resort_without_a_slug_has_no_resort_link(
        self, client: Client
    ) -> None:
        resort = _resort()
        favourite = FavouriteFactory.create(resort=resort, region=resort.region)
        Resort.objects.filter(pk=resort.pk).update(slug=None)
        client.force_login(favourite.user)
        response = _get(client, _favourite_url(favourite.uuid))
        assert response.status_code == 200
        assert "View resort" not in response.content.decode()

    def test_saved_resort_pin_links_to_its_resort(self, client: Client) -> None:
        resort = _resort()
        favourite = FavouriteFactory.create(resort=resort, region=resort.region)
        client.force_login(favourite.user)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert f'href="{resort.get_absolute_url()}"' in content
        assert 'data-testid="place-sheet-facts"' not in content


@pytest.mark.django_db
class TestMapPage:
    """The map page carries the sheet, its script and both URL templates."""

    def test_map_renders_the_place_sheet(self, client: Client) -> None:
        content = client.get(reverse("public:map")).content.decode()
        assert 'id="place-sheet"' in content
        assert "js/map_place_sheet.js" in content
        assert 'data-resort-sheet-url="/partials/place-sheet/resort/__SLUG__/"' in (
            content
        )
        assert (
            'data-favourite-sheet-url="/partials/place-sheet/favourite/'
            '00000000-0000-0000-0000-000000000000/"'
        ) in content
        assert 'id="place-sheet-strings-template"' in content
