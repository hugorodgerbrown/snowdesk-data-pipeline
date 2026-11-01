"""
tests/public/test_place_sheet.py — the phone place sheet's body
(apps.public.place_sheet, SNOW-1100).

Covers both views, ``resort_sheet_partial`` and ``favourite_sheet_partial``:
  - The guards: a plain GET is a 400 (require_htmx), an unknown resort a
    404, a malformed ``?d=`` a 400; a pin is owner-only, so another reader's
    pin and an anonymous request are both a 404.
  - PEEK: the name, the danger tile and the region badge in the
    ``data-sheet-peek`` header; a resort's Save star, rendered neutral
    (SNOW-1318: a disabled outline beside a hidden sign-in star, painted by
    the map's script); a saved pin's "…" menu and inline-rename row.
  - The detents the body allows: all three when there is something for
    FULL to show, peek and half otherwise.
  - A pin's problems are judged against its Location's height, the pin's
    own legacy height only as a fallback.
  - A saved pin's HALF: the day's weather at the pin; a forward day reads
    today's row and shows that row's forecast entry for the day; the
    forecast link and the bulletin link carry the day.
  - A resort's body (SNOW-1164): the resort page's own body for the map's
    day — the same sections the page renders, in one column, without the
    page's frame and without the sheet's old links onward — led by its
    "why it matters" line only when written. The forecast leads the
    bulletin on every day, rated or not, including when it is the one
    "No forecast available" line (SNOW-1203, SNOW-1205).
  - The map page carries the sheet, its script and the URL templates.
  - SNOW-1313: a pin's sheet is kept offline for its owner alone — it
    answers ``private, no-cache`` and names its viewer
    (``X-Snowdesk-Principal``); the forecast link carries the borrowed
    point's short id for the map to open its weather sheet in place.
  - SNOW-1318: a resort's sheet reads nothing about the reader, so every
    reader gets the same body and it answers ``private, max-age``.
"""

from __future__ import annotations

import datetime
import re
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.identity import PRINCIPAL_RESPONSE_HEADER
from apps.regions.models import Resort
from tests.factories import (
    AccountFactory,
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortFactory,
    ResortLocationFactory,
    UserFactory,
    WeatherFactory,
    weather_rows_from_days,
)
from tests.public.test_resort_detail import _todays_bulletin


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
    """Describe the row for ``day``."""
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


def _peek(content: str) -> str:
    """Return the sheet's peek block, up to where the body starts."""
    start = content.index("data-sheet-peek")
    ends = [
        content.find(marker, start)
        for marker in (
            'data-testid="place-sheet-resort-body"',
            'data-testid="place-sheet-level"',
        )
    ]
    return content[start : min(end for end in ends if end != -1)]


def _section_days(content: str) -> list[str]:
    """Return each dated eyebrow's day tag (``section-day``), as markup."""
    return re.findall(r'<span[^>]*data-testid="section-day"[^>]*>[^<]*</span>', content)


def _text(markup: str | bytes) -> str:
    """Return ``markup``'s text, tags dropped and whitespace collapsed."""
    if isinstance(markup, bytes):
        markup = markup.decode()
    return " ".join(re.sub(r"<[^>]+>", " ", markup).split())


def _star(content: str) -> str:
    """Return the resort's Save star, its opening tag to its closing one."""
    match = re.search(r"<button\b[^>]*data-resort-star\b.*?</button>", content, re.S)
    assert match is not None
    return match.group(0)


def _cache_directives(response: Any) -> set[str]:
    """Return a response's ``Cache-Control`` directives, as a set."""
    return {part.strip() for part in response["Cache-Control"].split(",")}


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

    def test_today_is_kept_for_ten_minutes(self, client: Client) -> None:
        """SNOW-1318: the region sheet's policy, private and short."""
        resort = _resort()
        response = _get(client, _resort_url(resort.slug))
        assert _cache_directives(response) == {"private", "max-age=600"}

    def test_a_forward_day_is_kept_for_ten_minutes(self, client: Client) -> None:
        resort = _resort()
        tomorrow = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
        response = _get(client, _resort_url(resort.slug, tomorrow))
        assert _cache_directives(response) == {"private", "max-age=600"}

    def test_a_past_day_is_kept_for_a_day(self, client: Client) -> None:
        resort = _resort()
        response = _get(client, _resort_url(resort.slug, "2026-02-03"))
        assert _cache_directives(response) == {"private", "max-age=86400"}

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
class TestPinOfflineCopy:
    """SNOW-1313: a pin's sheet may be kept, for its owner alone."""

    def test_the_owner_is_named_and_the_copy_may_be_kept(self, client: Client) -> None:
        """The worker stamps the kept copy from this header.

        ``private, no-cache`` rather than ``no-store``, which the worker
        refuses to keep.
        """
        account = AccountFactory.create()
        favourite = FavouriteFactory.create(user=account.user)
        client.force_login(account.user)
        response = _get(client, _favourite_url(favourite.uuid))
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-cache"
        assert response[PRINCIPAL_RESPONSE_HEADER] == str(account.uuid)

    def test_another_readers_pin_names_nobody(self, client: Client) -> None:
        favourite = FavouriteFactory.create()
        client.force_login(AccountFactory.create().user)
        response = _get(client, _favourite_url(favourite.uuid))
        assert response.status_code == 404
        assert not response.has_header(PRINCIPAL_RESPONSE_HEADER)

    def test_anonymous_names_nobody(self, client: Client) -> None:
        favourite = FavouriteFactory.create()
        response = _get(client, _favourite_url(favourite.uuid))
        assert response.status_code == 404
        assert not response.has_header(PRINCIPAL_RESPONSE_HEADER)

    def test_the_resort_sheet_is_kept_unstamped(self, client: Client) -> None:
        """SNOW-1318: the resort sheet names nobody, so it is kept public."""
        client.force_login(AccountFactory.create().user)
        resort = _resort()
        response = _get(client, _resort_url(resort.slug))
        assert "no-store" not in response["Cache-Control"]
        assert not response.has_header(PRINCIPAL_RESPONSE_HEADER)


@pytest.mark.django_db
class TestResortOfflineCopy:
    """SNOW-1318: an area download keeps a resort's sheet unstamped, so the
    worker serves the copy it saved to whoever opens it offline. Sound only
    while the body carries nothing about the reader — byte for byte, unlike
    the region sheet, which has its call to report.
    """

    def test_every_reader_gets_the_same_body(self, client: Client) -> None:
        resort = _resort()
        url = _resort_url(resort.slug, timezone.localdate().isoformat())
        anonymous = _get(client, url).content.decode()
        user = UserFactory.create(email="reader@example.com", first_name="Zebedee")
        client.force_login(user)
        signed_in = _get(client, url).content.decode()
        FavouriteFactory.create(user=user, resort=resort)
        saved = _get(client, url).content.decode()
        staff = UserFactory.create(is_staff=True)
        client.force_login(staff)
        as_staff = _get(client, url).content.decode()

        assert anonymous == signed_in == saved == as_staff
        for detail in ("reader@example.com", "Zebedee", "csrfmiddlewaretoken"):
            assert detail not in signed_in


@pytest.mark.django_db
class TestResortPeek:
    """A resort's peek row: name, rating, region and the Save star."""

    def test_header_is_the_pin_tile_and_the_name(self, client: Client) -> None:
        """Hugo, 21 Oct: no level, code or region in the header."""
        resort = _resort()
        RegionDayRatingFactory.create(
            region=resort.region, date=timezone.localdate(), max_rating="considerable"
        )
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-place-kind="resort"' in content
        peek = _peek(content)
        assert 'data-testid="resort-tile"' in peek
        assert "Verbier" in peek
        assert "danger-tile" not in peek
        assert "search-result-badge" not in peek
        assert "Considerable" not in peek
        # Hugo, 29 Oct 2026: no "Data from" row; the eyebrows name the day.
        assert "Data from" not in content

    def test_the_tile_draws_the_gondola_cabin(self, client: Client) -> None:
        """SNOW-1177: a resort's tile is the cabin, not the placemark."""
        resort = _resort()
        peek = _peek(_get(client, _resort_url(resort.slug)).content.decode())
        assert 'd="M3 6.5 21 3"' in peek
        assert "a7 7 0 1 0-14 0" not in peek

    def test_another_day_dates_each_dated_section(self, client: Client) -> None:
        """Hugo, 29 Oct 2026: the day is at the right of each dated eyebrow,
        in the info colours, and the sheet has no "Back to today" of its own.
        """
        resort = _resort()
        content = _get(client, _resort_url(resort.slug, "2026-02-03")).content.decode()
        assert 'data-testid="another-day-strip"' not in content
        assert "data-back-to-today" not in content
        days = _section_days(content)
        assert days
        for day in days:
            assert "data-another-day" in day
            assert _text(day) == "Tue 3 Feb"

    def test_today_dates_each_dated_section_quietly(self, client: Client) -> None:
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        days = _section_days(content)
        assert days
        for day in days:
            assert "data-another-day" not in day
            assert _text(day) == "Today"

    def test_today_shows_no_strip(self, client: Client) -> None:
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-testid="another-day-strip"' not in content

    def test_the_star_is_a_disabled_outline_until_painted(self, client: Client) -> None:
        """SNOW-1318: map_place_sheet.js paints it from the favourites feed."""
        resort = _resort()
        star = _star(_get(client, _resort_url(resort.slug)).content.decode())
        assert " disabled" in star
        assert 'data-favourited="false"' in star
        assert 'data-favourite-uuid=""' in star
        assert f'data-resort-slug="{resort.slug}"' in star
        assert 'fill="none"' in star
        assert 'data-label-save="Save resort"' in star
        assert 'data-label-remove="Remove from saved places"' in star

    def test_the_sign_in_star_is_rendered_hidden(self, client: Client) -> None:
        """The script shows it to a reader the map says is signed out."""
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        link = re.search(r"<a\b[^>]*data-resort-star-signin[^>]*>", content)
        assert link is not None
        assert " hidden" in link.group(0)
        assert f'href="{reverse("accounts:sign_in")}"' in link.group(0)
        # It keeps the resort's slug, name and pin, as the star does.
        assert f'data-resort-slug="{resort.slug}"' in link.group(0)
        assert 'data-resort-name="Verbier"' in link.group(0)

    def test_a_saved_resort_still_renders_the_neutral_star(
        self, client: Client
    ) -> None:
        """The reader's favourite is the feed's to say, never the body's."""
        resort = _resort()
        user = UserFactory.create()
        favourite = FavouriteFactory.create(user=user, resort=resort)
        client.force_login(user)
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-favourited="false"' in _star(content)
        assert str(favourite.uuid) not in content

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

    def test_the_tile_keeps_the_placemark(self, client: Client) -> None:
        """SNOW-1177: a saved pin is any point, not a resort — no cabin."""
        favourite = FavouriteFactory.create()
        client.force_login(favourite.user)
        peek = _peek(_get(client, _favourite_url(favourite.uuid)).content.decode())
        assert 'data-testid="resort-tile"' in peek
        assert "a7 7 0 1 0-14 0" in peek
        assert 'd="M3 6.5 21 3"' not in peek

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


def _pin(client: Client) -> Any:
    """A saved pin in a fresh region at 1500 m, signed in as its owner."""
    favourite = FavouriteFactory.create(region=MicroRegionFactory.create())
    assert favourite.location is not None
    favourite.location.elevation_m = 1500.0
    favourite.location.save()
    client.force_login(favourite.user)
    return favourite


def _curated_near(
    favourite: Any, following: list[dict[str, object]] | None = None, **weather: Any
) -> Any:
    """A curated forecast point 1.1 km north of a pin, with a row.

    A pin buys no fetch of its own (2026-10-28): its sheet borrows the
    nearest curated point, so a test that wants weather on the sheet puts
    it HERE, never on the pin's own location. ``following`` describes the
    rows for the days after (SNOW-1337: one row per day).
    """
    point = LocationFactory.create(
        name="Mont Fort",
        latitude=favourite.location.latitude + 0.01,
        longitude=favourite.location.longitude,
        elevation_m=3329.0,
    )
    ResortLocationFactory.create(location=point)
    WeatherFactory.create(location=point, **weather)
    weather_rows_from_days(point, following)
    return point


@pytest.mark.django_db
class TestPinWeather:
    """A saved pin's HALF ends under the day's weather nearest the pin."""

    def test_todays_row_is_the_half_detents_end(self, client: Client) -> None:
        favourite = _pin(client)
        _curated_near(favourite)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert 'data-testid="place-sheet-weather" data-sheet-half' in content
        # SNOW-1161: the week is the weather page's, not the sheet's.
        assert 'data-testid="place-sheet-week"' not in content
        assert 'data-testid="place-sheet-forecast-link"' in content

    def test_the_borrowed_point_is_named_with_its_height_and_distance(
        self, client: Client
    ) -> None:
        favourite = _pin(client)
        _curated_near(favourite)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        source = re.search(
            r'data-testid="place-sheet-forecast-source">(.*?)</p>', content, re.S
        )
        assert source is not None
        text = " ".join(source.group(1).split())
        assert text == "Forecast for Mont Fort (3329 m), 1.1 km from this pin."

    def test_the_pins_own_row_is_never_read(self, client: Client) -> None:
        """Rows fetched for a pin before 2026-10-28 are no longer shown."""
        favourite = _pin(client)
        WeatherFactory.create(location=favourite.location)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert 'data-testid="place-sheet-weather"' not in content

    def test_forward_day_reads_todays_forecast(self, client: Client) -> None:
        favourite = _pin(client)
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        _curated_near(favourite, following=[_forecast_day(tomorrow)])
        content = _get(
            client, _favourite_url(favourite.uuid, tomorrow.isoformat())
        ).content.decode()
        assert "place-sheet-forward-day" in content
        assert "Forecast ·" in content
        assert 'data-testid="place-sheet-forecast-source"' in content

    def test_forward_day_beyond_the_forecast_has_no_day(self, client: Client) -> None:
        favourite = _pin(client)
        _curated_near(favourite)
        later = timezone.localdate() + datetime.timedelta(days=3)
        content = _get(client, _favourite_url(favourite.uuid, later.isoformat()))
        assert b"place-sheet-forward-day" not in content.content
        assert b'data-testid="place-sheet-weather"' not in content.content

    def test_no_row_means_no_weather(self, client: Client) -> None:
        favourite = _pin(client)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert 'data-testid="place-sheet-weather"' not in content

    def test_forecast_link_is_the_borrowed_points_page_for_the_day(
        self, client: Client
    ) -> None:
        favourite = _pin(client)
        point = _curated_near(favourite, observed_on=datetime.date(2026, 2, 3))
        content = _get(client, _favourite_url(favourite.uuid, "2026-02-03")).content
        url = f"{point.get_absolute_url()}?date=2026-02-03"
        assert url.encode() in content

    def test_forecast_link_carries_the_borrowed_points_short_id(
        self, client: Client
    ) -> None:
        """SNOW-1313: the map opens that point's weather sheet in place."""
        favourite = _pin(client)
        point = _curated_near(favourite)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        link = re.search(
            r'<a[^>]*data-testid="place-sheet-forecast-link"[^>]*>', content, re.S
        )
        assert link is not None
        assert f'data-forecast-short-id="{point.short_id}"' in link.group(0)

    def test_no_source_means_no_short_id(self, client: Client) -> None:
        favourite = _pin(client)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert "data-forecast-short-id" not in content

    def test_point_without_a_page_gets_no_forecast_link(self, client: Client) -> None:
        favourite = _pin(client)
        point = _curated_near(favourite)
        point.short_id = None
        point.save()
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert "Full forecast" not in content
        assert 'href="?date=' not in content

    def test_rated_day_links_to_the_dated_bulletin(self, client: Client) -> None:
        favourite = _pin(client)
        day = datetime.date(2026, 2, 3)
        RegionDayRatingFactory.create(
            region=favourite.region, date=day, max_rating="moderate"
        )
        content = _get(
            client, _favourite_url(favourite.uuid, day.isoformat())
        ).content.decode()
        assert f'href="{favourite.region.get_absolute_url(day)}"' in content
        assert f"Read the {favourite.region.name} bulletin for 3 Feb" in content


@pytest.mark.django_db
class TestResortBody:
    """SNOW-1164: under the peek row, the resort page's own body."""

    def test_the_body_is_the_resort_pages_body(self, client: Client) -> None:
        resort = _resort(num_lifts=34, operator_name="Téléverbier")
        WeatherFactory.create(location=_primary_location(resort))
        sheet = _get(client, _resort_url(resort.slug)).content.decode()
        page = client.get(resort.get_absolute_url()).content.decode()
        assert 'data-testid="place-sheet-resort-body"' in sheet
        for testid in ("resort-forecast", "resort-region", "resort-facts"):
            assert f'data-testid="{testid}"' in sheet
            assert f'data-testid="{testid}"' in page
        assert "Téléverbier" in sheet

    def test_the_body_leaves_the_pages_frame_behind(self, client: Client) -> None:
        resort = _resort(latitude=46.1, longitude=7.4)
        content = _get(client, _resort_url(resort.slug)).content.decode()
        for testid in (
            "resort-heading",
            "resort-map-link",
            "resort-all-resorts-link",
            "resort-actions",
        ):
            assert f'data-testid="{testid}"' not in content

    def test_the_sheets_own_links_are_gone(self, client: Client) -> None:
        resort = _resort()
        RegionDayRatingFactory.create(
            region=resort.region, date=timezone.localdate(), max_rating="moderate"
        )
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-testid="place-sheet-links"' not in content
        assert "View resort" not in content
        assert 'data-testid="place-sheet-facts"' not in content

    def test_the_body_is_one_column_in_the_map(self, client: Client) -> None:
        resort = _resort(num_lifts=34)
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert "lg:flex-row" not in content
        assert "lg:w-80" not in content

    def test_the_body_shows_the_maps_day(self, client: Client) -> None:
        """The region row opens the region's page for the map's day."""
        resort = _resort()
        day = datetime.date(2026, 2, 3)
        content = _get(client, _resort_url(resort.slug, day.isoformat())).content
        assert f'href="{resort.region.get_absolute_url(day)}"'.encode() in content

    def test_the_bulletin_eyebrow_names_the_day(self, client: Client) -> None:
        """Hugo, 29 Oct 2026: the bulletin's eyebrow carries the map's day
        at its right — short, as the date control writes it.
        """
        resort = _resort()
        content = _get(client, _resort_url(resort.slug, "2026-02-03")).content.decode()
        start = content.index('id="resort-region-heading"')
        row = content[start : content.index("</div>", start)]
        assert "Tue 3 Feb" in _text(row)
        assert "Tuesday 3 February" not in content

    def test_forecast_links_carry_the_maps_day(self, client: Client) -> None:
        resort = _resort(latitude=46.1, longitude=7.4)
        assert resort.location is not None
        WeatherFactory.create(
            location=resort.location, observed_on=datetime.date(2026, 2, 3)
        )
        content = _get(client, _resort_url(resort.slug, "2026-02-03")).content
        url = f"{resort.location.get_absolute_url()}?date=2026-02-03"
        assert url.encode() in content

    def test_todays_forecast_links_are_bare(self, client: Client) -> None:
        resort = _resort(latitude=46.1, longitude=7.4)
        assert resort.location is not None
        WeatherFactory.create(location=resort.location)
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert f'href="{resort.location.get_absolute_url()}"' in content
        assert "?date=" not in content

    @pytest.mark.parametrize("rated", [True, False])
    @pytest.mark.parametrize("has_forecast", [True, False])
    def test_forecast_then_facts_then_the_region_row(
        self, client: Client, rated: bool, has_forecast: bool
    ) -> None:
        """Hugo, 25 Oct 2026: the resort's own data first, the region last.

        Whatever the day holds, the sheet shows no danger of the resort's
        own: the one neutral row naming the region leads, then the forecast
        (or its one "No forecast available" line), then the facts, folded.
        """
        resort = _resort(num_lifts=34)
        if has_forecast:
            WeatherFactory.create(location=_primary_location(resort))
        if rated:
            _todays_bulletin(resort.region)
        content = _get(client, _resort_url(resort.slug)).content.decode()
        region = content.index('data-testid="resort-region"')
        forecast = content.index('data-testid="resort-forecast"')
        assert region < forecast < content.index('data-testid="resort-facts"')
        assert 'data-testid="resort-facts-panel"' in content
        assert ('data-testid="resort-forecast-none"' in content) is not has_forecast
        for gone in ("resort-bulletin", "resort-problems", "resort-season"):
            assert f'data-testid="{gone}"' not in content

    def test_the_region_row_opens_the_region_panel(self, client: Client) -> None:
        """The callout is the region's row, keyed for the map."""
        resort = _resort()
        bulletin = _todays_bulletin(resort.region)
        RegionDayRatingFactory.create(
            region=resort.region,
            max_rating="considerable",
            source="SLF",
            source_bulletin=bulletin,
        )
        content = _get(client, _resort_url(resort.slug)).content.decode()
        start = content.index('data-testid="resort-region-link"')
        row = content[content.rindex("<a", 0, start) : content.index("</a>", start)]
        assert 'data-panel-open="region"' in row
        assert f'data-panel-id="{resort.region.region_id}"' in row

    def test_the_callout_shows_the_regions_level(self, client: Client) -> None:
        """A rated day: the level's tile, its name and the source (27 Oct)."""
        resort = _resort()
        bulletin = _todays_bulletin(resort.region)
        RegionDayRatingFactory.create(
            region=resort.region,
            max_rating="considerable",
            source="SLF",
            source_bulletin=bulletin,
        )
        content = _get(client, _resort_url(resort.slug)).content.decode()
        start = content.index('data-testid="resort-region-link"')
        row = content[content.rindex("<a", 0, start) : content.index("</a>", start)]
        assert 'data-testid="place-link-region-level"' in row
        assert 'data-level="considerable"' in row
        assert "Considerable" in row
        assert f"{resort.region.name} · View bulletin" in row
        assert "Avalanche bulletin • SLF" in content
        assert 'data-testid="resort-region-none"' not in content

    def test_no_bulletin_says_so_and_links_the_history(self, client: Client) -> None:
        """No rated day: the message and the region page in the callout."""
        resort = _resort()
        RegionDayRatingFactory.create(
            region=resort.region,
            date=timezone.localdate() - datetime.timedelta(days=3),
            max_rating="moderate",
            source="SLF",
        )
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert 'data-testid="resort-region-link"' not in content
        assert "No bulletin available on this date." in content
        assert 'data-testid="resort-region-history"' in content
        assert resort.region.get_absolute_url() in content
        # The eyebrow names the provider of the region's latest rated day.
        assert "Avalanche bulletin • SLF" in content

    def test_a_region_never_rated_names_no_source(self, client: Client) -> None:
        """No rating ever: the eyebrow is the plain section name."""
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert "Avalanche bulletin •" not in content
        assert "Avalanche bulletin" in content

    def test_the_body_carries_its_panel_key(self, client: Client) -> None:
        """The back pill names the panel a row was followed from."""
        resort = _resort()
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert f'data-panel-key="resort:{resort.slug}"' in content
        assert 'data-panel-label="Verbier"' in content

    def test_the_kind_line_names_a_ski_resort(self, client: Client) -> None:
        """Every map panel names its kind above its title (25 Oct 2026)."""
        resort = _resort()
        peek = _peek(_get(client, _resort_url(resort.slug)).content.decode())
        assert 'data-testid="sheet-kind"' in peek
        assert "Ski resort" in peek

    def test_a_saved_pin_is_a_saved_place(self, client: Client) -> None:
        """A saved pin is no resort and carries no panel key of its own."""
        resort = _resort()
        favourite = FavouriteFactory.create(region=resort.region)
        client.force_login(favourite.user)
        content = _get(client, _favourite_url(favourite.uuid)).content.decode()
        assert "Saved place" in _peek(content)
        assert "data-panel-key" not in content

    def test_written_why_it_matters_shows(self, client: Client) -> None:
        resort = _resort(why_it_matters="Four lifts, national freeride reputation.")
        content = _get(client, _resort_url(resort.slug)).content.decode()
        assert "Four lifts, national freeride reputation." in content

    def test_why_it_matters_opens_the_folded_about(self, client: Client) -> None:
        """27 Oct 2026: the line moved from the top into "About this resort"."""
        resort = _resort(why_it_matters="Four lifts, national freeride reputation.")
        content = _get(client, _resort_url(resort.slug)).content.decode()
        about = content.index('data-testid="resort-facts-panel"')
        line = content.index("Four lifts, national freeride reputation.")
        assert content.index('data-testid="resort-region"') < about < line

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
