"""
tests/public/test_region_sheet.py — the phone region sheet's body
(apps.public.region_sheet.region_sheet_partial, SNOW-1098).

Covers:
  - The guards: a plain GET is a 400 (require_htmx), an unknown region a
    404, a malformed ``?d=`` a 400.
  - PEEK: the region's name, the danger tile and level, the search row's
    flag-and-code badge and area name (not the date), the two proxy
    controls and the ×, inside the ``data-sheet-peek`` header.
  - The detents the body allows: all three on a rated day, peek and half
    otherwise (``data-sheet-detents``, read by static/js/map_sheet.js).
  - HALF and FULL: the breadcrumb, then the bulletin page's own body for
    the day (SNOW-1164) — the same sections the page renders, without the
    page's chrome or its superuser debug block — and the region's resorts
    linked to their pages. No link on to the bulletin: the sheet is it.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.regions.models import Resort
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    ResortFactory,
)


def _url(region_id: str, day: str | None = None) -> str:
    """Build the region sheet URL, with an optional ``?d=``."""
    url = reverse("public:region_sheet_partial", kwargs={"region_id": region_id})
    return f"{url}?d={day}" if day else url


def _get(client: Client, url: str) -> Any:
    """GET as HTMX does."""
    return client.get(url, HTTP_HX_REQUEST="true")


def _render_model() -> dict[str, Any]:
    """A current-version render model carrying one dry wind-slab problem."""
    problem = {
        "problem_type": "wind_slab",
        "comment_html": "<p>Wind slab comment text.</p>",
        "aspects": ["N", "NE", "E"],
        "elevation": {"lower": "2200", "upper": None, "treeline": False},
        "time_period": "all_day",
        "core_zone_text": None,
        "danger_rating_value": "considerable",
    }
    return {
        "version": RENDER_MODEL_VERSION,
        "source": "slf",
        "danger": {
            "key": "considerable",
            "number": "3",
            "subdivision": None,
            "ratings": [],
        },
        "danger_patterns": [],
        "traits": [
            {
                "category": "dry",
                "time_period": "all_day",
                "title": "Dry avalanches",
                "geography": {"source": "problems"},
                "problems": [problem],
                "prose": None,
                "danger_level": 3,
            }
        ],
        "metadata": {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": "2026-03-15T15:00:00+00:00",
            "unscheduled": False,
            "lang": "en",
        },
        "prose": {
            "snowpack_structure": None,
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
            "avalanche_activity": {"highlights": "", "comment": ""},
            "tendency_lead": None,
        },
    }


def _date_row(content: str) -> str:
    """Return the peek's date row (``sheet-date-row``), to its button."""
    start = content.index('data-testid="sheet-date-row"')
    return content[start - 60 : content.index("</button>", start)]


def _text(markup: str) -> str:
    """Return ``markup``'s text, tags dropped and whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", markup).split())


def _rated_today(region: Any) -> None:
    """Give ``region`` today's rating and a bulletin valid all of today."""
    today = timezone.localdate()
    RegionDayRatingFactory.create(region=region, date=today, max_rating="considerable")
    start = datetime.datetime.combine(today, datetime.time.min, tzinfo=datetime.UTC)
    bulletin = BulletinFactory.create(
        issued_at=start,
        valid_from=start,
        valid_to=start + datetime.timedelta(days=1),
        render_model=_render_model(),
        render_model_version=RENDER_MODEL_VERSION,
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)


@pytest.mark.django_db
class TestGuards:
    """The request guards."""

    def test_plain_get_is_rejected(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        assert client.get(_url(region.region_id.lower())).status_code == 400

    def test_unknown_region_is_404(self, client: Client) -> None:
        assert _get(client, _url("xx-9999")).status_code == 404

    def test_malformed_date_is_400(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        response = _get(client, _url(region.region_id.lower(), "not-a-date"))
        assert response.status_code == 400


@pytest.mark.django_db
class TestPeek:
    """What shows at peek: the header."""

    def test_header_names_the_region_and_its_level(self, client: Client) -> None:
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert "data-sheet-peek" in content
        assert "Martigny-Verbier" in content
        assert 'data-testid="region-sheet-rating"' in content
        assert 'data-level="considerable"' in content
        assert "Considerable" in content

    def test_another_day_highlights_the_date_row(self, client: Client) -> None:
        """Hugo, 21 Oct: the peek's date row took over SNOW-1165's strip."""
        region = MicroRegionFactory.create()
        content = _get(
            client, _url(region.region_id.lower(), "2026-02-03")
        ).content.decode()
        assert 'data-testid="another-day-strip"' not in content
        row = _date_row(content)
        assert "data-another-day" in row
        assert "Data from 3 Feb 2026" in _text(row)
        assert "data-back-to-today" in row

    def test_today_shows_no_strip(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="another-day-strip"' not in content

    def test_header_is_the_tile_and_the_name_then_the_date(
        self, client: Client
    ) -> None:
        """Hugo, 21 Oct: no code, level or area squeezed under the name."""
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-meta"' not in content
        assert "search-result-badge" not in content[: content.index("sheet-date-row")]
        row = _date_row(content)
        assert "data-another-day" not in row
        assert f"Data from {timezone.localdate():%-d %b %Y}" in _text(row)
        assert content.index('data-region-sheet-slot="name"') < content.index(
            "sheet-date-row"
        )

    def test_header_carries_the_two_proxies_and_a_close(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-region-sheet-proxy="map-region-pin-control"' in content
        assert 'data-region-sheet-proxy="map-download-control"' in content
        assert 'data-action="dismiss"' in content

    def test_an_unrated_day_has_no_tile(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-rating"' not in content
        assert 'data-level="no_rating"' in content


@pytest.mark.django_db
class TestDetents:
    """FULL only reveals a bulletin, so a day without one stops at half."""

    def test_rated_day_allows_all_three(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-sheet-detents="peek,half,full"' in content

    def test_unrated_day_stops_at_half(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-sheet-detents="peek,half"' in content


@pytest.mark.django_db
class TestHalf:
    """What half adds: where it is, the problems, the resorts."""

    def test_the_body_is_the_bulletin_pages_body(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        _rated_today(region)
        sheet = _get(client, _url(region.region_id.lower())).content.decode()
        page = client.get(region.get_absolute_url()).content.decode()
        assert 'data-testid="region-sheet-bulletin"' in sheet
        for testid in (
            "day-risk-profile-heading",
            "avalanche-problems-heading",
            "metadata-strip",
        ):
            assert f'data-testid="{testid}"' in sheet
            assert f'data-testid="{testid}"' in page
        assert "Wind slab comment text." in sheet

    def test_the_body_leaves_the_pages_chrome_behind(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="bulletin-map-link"' not in content
        assert "Open bulletin for" not in content

    def test_superuser_debug_stays_on_the_page(
        self, client: Client, django_user_model: Any
    ) -> None:
        region = MicroRegionFactory.create()
        _rated_today(region)
        admin = django_user_model.objects.create_superuser("root", "r@example.com", "x")
        client.force_login(admin)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="superuser-debug"' not in content

    def test_no_bulletin_means_no_body_sections(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="avalanche-problems-heading"' not in content

    def test_resorts_link_to_their_pages(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(
            region=region, name="Verbier", kind=Resort.Kind.RESORT
        )
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-resorts"' in content
        assert f'href="{resort.get_absolute_url()}"' in content

    def test_breadcrumb_names_the_country(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-breadcrumb"' in content


@pytest.mark.django_db
class TestNoBulletin:
    """A day with no bulletin says so, in the body's own words."""

    def test_unrated_day_says_no_bulletin(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert (
            "No bulletin available" in content
            or "Avalanche data is not published" in content
        )

    def test_dated_request_shows_that_day(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        day = datetime.date(2026, 2, 3)
        RegionDayRatingFactory.create(region=region, date=day, max_rating="moderate")
        response = _get(client, _url(region.region_id.lower(), day.isoformat()))
        assert response.context["page_date"] == day


@pytest.mark.django_db
class TestMapPage:
    """The map page carries the sheet, its script and the endpoint."""

    def test_map_renders_the_region_sheet_with_detents(self, client: Client) -> None:
        content = client.get(reverse("public:map")).content.decode()
        assert 'id="region-sheet"' in content
        assert 'data-detents="peek,half,full"' in content
        assert 'data-detent-initial="peek"' in content
        assert "js/map_region_sheet.js" in content
        assert 'data-region-sheet-url="/partials/region-sheet/XX-0000/"' in content

    def test_map_carries_the_blank_peek_for_the_page_to_fill(
        self, client: Client
    ) -> None:
        content = client.get(reverse("public:map")).content.decode()
        match = re.search(
            r'<template id="region-sheet-peek-template">(.*?)</template>', content, re.S
        )
        assert match, "the map has no blank peek row"
        row = match.group(1)
        assert "data-sheet-peek" in row
        for slot in ("rating", "name"):
            assert f'data-region-sheet-slot="{slot}"' in row
        assert 'data-region-sheet-proxy="map-region-pin-control"' in row
        assert "data-sheet-date-day" in row


@pytest.mark.django_db
class TestCaching:
    """A body is privately cacheable: a day for a past day, ten minutes otherwise."""

    def _cache_control(self, client: Client, day: str | None) -> str:
        region = MicroRegionFactory.create()
        response = _get(client, _url(region.region_id.lower(), day))
        assert response.status_code == 200
        return str(response["Cache-Control"])

    def test_a_past_day_keeps_for_a_day(self, client: Client) -> None:
        yesterday = (timezone.localdate() - datetime.timedelta(days=1)).isoformat()
        header = self._cache_control(client, yesterday)
        assert "private" in header
        assert "max-age=86400" in header

    def test_today_keeps_for_ten_minutes(self, client: Client) -> None:
        header = self._cache_control(client, None)
        assert "private" in header
        assert "max-age=600" in header

    def test_a_forecast_day_keeps_for_ten_minutes(self, client: Client) -> None:
        tomorrow = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
        assert "max-age=600" in self._cache_control(client, tomorrow)
