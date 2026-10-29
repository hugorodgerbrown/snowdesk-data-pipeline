"""
tests/public/test_region_sheet.py — the phone region sheet's body
(apps.public.region_sheet.region_sheet_partial, SNOW-1098).

Covers:
  - The guards: a plain GET is a 400 (require_htmx), an unknown region a
    404, a malformed ``?d=`` a 400.
  - PEEK: the region's name, the danger tile and level, the search row's
    flag-and-code badge and area name (not the date), the two proxy
    controls and the ×, inside the ``data-sheet-peek`` header.
  - The detents: the body narrows none, so all three on every day — a
    day with no bulletin still has "On the ground" and the resorts to
    reach (``data-sheet-detents``, read by static/js/map_sheet.js).
  - SNOW-979: the "Highlight problem terrain" switch above the
    bulletin and its key — only for a region inside the terrain tiles,
    with the tiles configured, on a day with a bulletin. Since SNOW-1194
    the rules come from /api/problem-terrain/ (tests/public/
    test_problem_terrain.py), not the body.
  - SNOW-1291: "On the ground" — signed out, a sign-in link; signed in,
    the button static/js/report.js opens; each row keyed by submission.
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
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.regions.models import Resort
from tests.factories import (
    BulletinFactory,
    FieldObservationFactory,
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


def _section_days(content: str) -> list[str]:
    """Return each dated eyebrow's day tag (``section-day``), as markup."""
    return re.findall(r'<span[^>]*data-testid="section-day"[^>]*>[^<]*</span>', content)


def _text(markup: str) -> str:
    """Return ``markup``'s text, tags dropped and whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", markup).split())


def _rated_today(region: Any) -> None:
    """Give ``region`` today's rating and a bulletin valid all of today."""
    _rated_on(region, timezone.localdate())


def _rated_on(region: Any, day: datetime.date) -> None:
    """Give ``region`` a rating and a bulletin valid all of ``day``."""
    RegionDayRatingFactory.create(region=region, date=day, max_rating="considerable")
    start = datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.UTC)
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

    def test_the_kind_line_names_an_avalanche_region(self, client: Client) -> None:
        """Every map panel names its kind above its title (25 Oct 2026)."""
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        start = content.index('data-testid="sheet-kind"')
        assert "Avalanche region" in content[start : content.index("</p>", start)]

    def test_another_day_dates_the_bulletin_heading(self, client: Client) -> None:
        """Hugo, 29 Oct 2026: no "Data from" row, and no "Back to today" in
        the sheet — the bulletin's first heading names the day, in the info
        colours, and the date control is the way back.
        """
        region = MicroRegionFactory.create()
        _rated_on(region, datetime.date(2026, 2, 3))
        content = _get(
            client, _url(region.region_id.lower(), "2026-02-03")
        ).content.decode()
        assert 'data-testid="another-day-strip"' not in content
        assert "Data from" not in content
        assert "data-back-to-today" not in content
        (day,) = _section_days(content)
        assert "data-another-day" in day
        assert _text(day) == "Tue 3 Feb"

    def test_today_dates_the_bulletin_heading_quietly(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        (day,) = _section_days(content)
        assert "data-another-day" not in day
        assert _text(day) == "Today"

    def test_today_shows_no_strip(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="another-day-strip"' not in content

    def test_header_is_the_tile_and_the_name(self, client: Client) -> None:
        """Hugo, 21 Oct: no code, level or area squeezed under the name."""
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-meta"' not in content
        assert "search-result-badge" not in content
        assert "Data from" not in content

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

    def test_a_region_no_provider_rates_says_not_forecast(self, client: Client) -> None:
        """SNOW-1196: its "–" must not read as a day without a bulletin."""
        cache.clear()  # covered_region_ids() is cached across tests.
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-not-forecast"' in content
        assert "Not forecast" in content

    def test_a_covered_region_on_an_unrated_day_does_not(self, client: Client) -> None:
        cache.clear()
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=timezone.localdate() - datetime.timedelta(days=3),
            max_rating="low",
        )
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-not-forecast"' not in content


@pytest.mark.django_db
class TestDetents:
    """All three detents on every day; FULL fits the content (map_sheet.js)."""

    def test_rated_day_narrows_nothing(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert "data-sheet-detents" not in content

    def test_unrated_day_can_still_reach_full(self, client: Client) -> None:
        """Hugo, 29 Oct: with no bulletin the resorts sat below a half-only
        sheet, out of reach. FULL fits the content, so nothing is lost by
        offering it on a short body.
        """
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert "data-sheet-detents" not in content


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
            "panel-title-level",
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

    def test_each_resort_is_a_row_that_opens_its_panel(self, client: Client) -> None:
        """Hugo, 25 Oct 2026: a region lists its resorts as rows, round tiles."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(
            region=region, name="Verbier", kind=Resort.Kind.RESORT, num_lifts=34
        )
        content = _get(client, _url(region.region_id.lower())).content.decode()
        start = content.index('data-testid="region-sheet-resorts"')
        section = content[start : content.index("</section>", start)]
        assert 'data-panel-open="resort"' in section
        assert f'data-panel-id="{resort.slug}"' in section
        assert f'data-resort-slug="{resort.slug}"' in section
        assert 'data-testid="resort-tile"' in section
        assert "34 lifts" in section

    def test_the_body_carries_its_panel_key(self, client: Client) -> None:
        """The back pill names the panel a row was followed from."""
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert f'data-panel-key="region:{region.region_id}"' in content
        assert 'data-panel-label="Martigny-Verbier"' in content

    def test_breadcrumb_names_the_country(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-breadcrumb"' in content

    def test_breadcrumb_is_in_the_peek_and_the_day_below_it(
        self, client: Client
    ) -> None:
        """SNOW-1234: the peek says where the region is; the bulletin's
        dated heading, below the peek, names the day (29 Oct 2026).
        """
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        peek_at = content.index("data-sheet-peek")
        crumb_at = content.index('data-testid="region-sheet-breadcrumb"')
        day_at = content.index('data-testid="section-day"')
        assert peek_at < crumb_at < day_at
        # The peek's element closes between the breadcrumb and the day.
        assert "</div>" in content[crumb_at:day_at]
        assert region.subregion.name_en in _text(content[crumb_at:day_at])


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
        # No day to fill: the body's dated headings name it (29 Oct 2026).
        assert "data-sheet-date-day" not in row
        # A blank line holds the breadcrumb's place, so the peek keeps its
        # height when the body lands (SNOW-1234).
        crumb = row[row.index('data-testid="region-sheet-breadcrumb"') :]
        assert "&nbsp;" in crumb[: crumb.index("</p>")]
        # One root element: map_region_sheet.js clones only the first.
        assert len(re.findall(r"^<div>", row.strip(), re.M)) == 1


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


_TILES = "https://tiles.example/terrain-class/v1/{z}/{x}/{y}.png"


@pytest.mark.django_db
class TestProblemTerrainSwitch:
    """SNOW-979: the "Highlight problem terrain" switch below the bulletin."""

    def test_rated_swiss_region_carries_the_switch_and_its_key(
        self, client: Client, settings: Any
    ) -> None:
        """The switch and this region's key; the rules come from the feed."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-terrain"' in content
        assert 'id="region-sheet-terrain-toggle"' in content
        # The layers menu's row's name: one state, one name.
        assert "Highlight problem terrain" in content
        # SNOW-1194: the map paints every region from /api/problem-terrain/,
        # so the body no longer carries its own region's rules.
        assert "region-sheet-terrain-rules" not in content
        key = content[content.index("data-region-sheet-terrain-key") :]
        assert "map-legend-swatch--terrain-considerable" in key
        assert "Considerable problem" in _text(key)
        assert "Additional steep terrain (>30°)" in _text(key)
        # No undecided rule, so no undecided swatch.
        assert "map-legend-swatch--terrain-undecided" not in key

    def test_switch_sits_below_the_bulletin(
        self, client: Client, settings: Any
    ) -> None:
        """Hugo, 23 Oct: the bulletin comes first, the switch after it."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert content.index('data-testid="region-sheet-terrain"') > content.index(
            'data-testid="region-sheet-bulletin"'
        )

    def test_key_and_zoom_line_render_hidden(
        self, client: Client, settings: Any
    ) -> None:
        """The script shows them once it has read the switch's state."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert re.search(r"data-region-sheet-terrain-key\s+hidden", content)
        # The zoom hint is the switch's own second line, under its label.
        strip = content[content.index('data-testid="region-sheet-terrain"') :]
        toggle = strip[: strip.index("data-region-sheet-terrain-key")]
        assert re.search(r"data-panel-overlay-toggle-detail\s+hidden", toggle)
        assert "Zoom in on the map to see it" in toggle

    def test_key_and_explainer_start_closed(
        self, client: Client, settings: Any
    ) -> None:
        """Hugo, 23 Oct: read once, so folded behind one question."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        details = re.search(
            r"<details[^>]*data-region-sheet-terrain-key[^>]*>", content
        )
        assert details is not None
        assert " open" not in details.group(0)
        body = content[details.end() :]
        summary = body[: body.index("</summary>")]
        assert "What is problem terrain?" in _text(summary)
        assert "Zoom in" not in summary
        inside = body[body.index("</summary>") : body.index("</details>")]
        assert "map-legend-swatch--terrain-considerable" in inside
        assert "Highlighted terrain is over 30°" in _text(inside)
        assert "How to read it" in inside

    def test_no_switch_while_the_terrain_tiles_are_unconfigured(
        self, client: Client, settings: Any
    ) -> None:
        """The terrain filter's own gate."""
        settings.TERRAIN_CLASS_TILE_URL = ""
        region = MicroRegionFactory.create()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-terrain"' not in content
        assert "region-sheet-terrain-rules" not in content

    def test_no_switch_outside_the_terrain_tiles(
        self, client: Client, settings: Any
    ) -> None:
        """A region outside Switzerland: a switch that paints nothing is a promise."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        region = MicroRegionFactory.create(region_id="AT-07-01")
        region.subregion.major.country = "AT"
        region.subregion.major.save()
        _rated_today(region)
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-terrain"' not in content

    def test_switch_on_a_day_whose_bulletin_describes_no_ground(
        self, client: Client, settings: Any
    ) -> None:
        """No rules is not no switch: steep ground paints as undescribed."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        region = MicroRegionFactory.create()
        _rated_today(region)
        bulletin = region.bulletin_links.get().bulletin
        bulletin.render_model = {**bulletin.render_model, "traits": []}
        bulletin.save()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-terrain"' in content
        key = content[content.index("data-region-sheet-terrain-key") :]
        assert "map-legend-swatch--terrain-considerable" not in key
        assert "Additional steep terrain (>30°)" in _text(key)

    def test_no_switch_on_a_day_without_a_bulletin(
        self, client: Client, settings: Any
    ) -> None:
        """Nothing to describe, nothing to paint."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        region = MicroRegionFactory.create()
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="region-sheet-terrain"' not in content


@pytest.mark.django_db
class TestOnTheGround:
    """SNOW-1291: the sheet's "On the ground" section and its call to report."""

    def test_signed_out_reader_is_asked_to_sign_in(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(region=region, observed_at=timezone.now())
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="on-the-ground"' in content
        assert "Sign in to report" in content
        assert reverse("accounts:sign_in") in content
        assert 'data-action="open-report"' not in content

    def test_signed_in_reader_gets_the_report_button(
        self, client: Client, django_user_model: Any
    ) -> None:
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(region=region, observed_at=timezone.now())
        client.force_login(django_user_model.objects.create_user("u", "u@example.com"))
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-action="open-report"' in content
        assert "Report what you see" in content
        assert "Sign in to report" not in content
        # The sheet IS the map: no link back to it.
        assert "Report on the map" not in content

    def test_empty_today_is_one_row_with_short_calls(
        self, client: Client, django_user_model: Any
    ) -> None:
        """SNOW-1296: no reports is one row, not a sentence and a button."""
        region = MicroRegionFactory.create()
        signed_out = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-testid="on-the-ground-empty"' in signed_out
        assert "No reports in 3 days" in signed_out
        assert re.search(r">\s*Sign in\s*<", signed_out)
        assert "Sign in to report" not in signed_out
        client.force_login(django_user_model.objects.create_user("u", "u@example.com"))
        signed_in = _get(client, _url(region.region_id.lower())).content.decode()
        assert 'data-action="open-report"' in signed_in
        assert "Report what you see" not in signed_in

    def test_a_report_row_carries_its_submission(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        report = FieldObservationFactory.create(
            region=region, observed_at=timezone.now()
        )
        content = _get(client, _url(region.region_id.lower())).content.decode()
        assert f'data-observation-submission="{report.submission}"' in content
