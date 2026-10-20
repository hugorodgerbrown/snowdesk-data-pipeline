"""
tests/public/test_season_partial.py — Tests for the season_calendar_partial view.

Covers:
  - 400 on non-HTMX request (require_htmx guard).
  - 404 on unknown region_id.
  - Grid fragment rendered with data-testid="season-calendar".
  - No ring in the response — the selected day is ringed client-side
    (SNOW-1158: the bulletin page no longer marks today).
  - data-date="YYYY-MM-DD" on every rendered cell.
  - Fragment cache: second request for the same (region, today) skips DB queries.
  - Cache invalidation: apply_bulletin_day_ratings deletes the key.
  - SNOW-1128: the key carries the language (normalised to its LANGUAGES
    code), invalidation deletes every language's key, and a time-split
    cell's AM/PM label renders through one translatable sentence.
  - SNOW-1158: the fragment is the shared month-shaped calendar, linked —
    rated days are links, two-level days (an afternoon rise, an AM/PM
    split, an ALBINA band split) draw as one "split" cell, peak right.
"""

from __future__ import annotations

import datetime
import re
from datetime import UTC
from typing import Any

import pytest
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.bulletins.services.day_rating import (
    apply_bulletin_day_ratings,
    season_calendar_cache_key,
)
from apps.public.season_calendar import (
    SeasonCell,
    SeasonGrid,
    build_season_months,
    summarise_season,
)
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
)

_SEASON_START = datetime.date(2025, 11, 3)


def _url(region_id: str) -> str:
    """Build the URL for the season_calendar_partial endpoint."""
    return reverse("public:season_partial", kwargs={"region_id": region_id})


# ---------------------------------------------------------------------------
# Guard tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeasonPartialGuards:
    """Tests for request-guard behaviour."""

    def test_non_htmx_get_returns_400(self) -> None:
        """Plain GET without HX-Request header is rejected with 400."""
        client = Client()
        region = MicroRegionFactory.create()
        url = _url(region.region_id.lower())
        response = client.get(url)
        assert response.status_code == 400

    def test_unknown_region_returns_404(self) -> None:
        """Unknown region_id returns 404."""
        client = Client()
        url = _url("xx-9999")
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Response content tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeasonPartialContent:
    """Tests for the content of the season_calendar_partial response."""

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_returns_grid_with_season_calendar_testid(self, client: Client) -> None:
        """HTMX GET returns a fragment with data-testid="season-calendar"."""
        region = MicroRegionFactory.create()
        response = client.get(_url(region.region_id.lower()), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b'data-testid="season-calendar"' in response.content

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_is_the_linked_calendar_with_the_full_key(self, client: Client) -> None:
        """SNOW-1158: the linked variant — no role=img, the counted key."""
        region = MicroRegionFactory.create()
        response = client.get(_url(region.region_id.lower()), HTTP_HX_REQUEST="true")
        content = response.content.decode()
        assert 'data-variant="linked"' in content
        assert 'role="img"' not in content
        assert "Two levels in one day" in content
        assert "Selected day" in content

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_every_cell_carries_data_date_attribute(self, client: Client) -> None:
        """Every rendered cell anchor/div carries a data-date="YYYY-MM-DD" attribute."""
        region = MicroRegionFactory.create()
        response = client.get(_url(region.region_id.lower()), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        content = response.content.decode()
        # At least one data-date attribute must be present.
        assert 'data-date="' in content
        # All data-date values must match YYYY-MM-DD format.
        dates_found = re.findall(r'data-date="(\d{4}-\d{2}-\d{2})"', content)
        assert len(dates_found) > 0
        for date_str in dates_found:
            # Confirm it parses cleanly.
            datetime.date.fromisoformat(date_str)

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_no_day_is_ringed_server_side(self, client: Client) -> None:
        """The ring is never in the partial — the selected day is ringed client-side."""
        region = MicroRegionFactory.create()
        response = client.get(_url(region.region_id.lower()), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b"season-day season-day-ring" not in response.content

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_region_with_rated_days_renders_interactive_cells(
        self, client: Client
    ) -> None:
        """Days with a RegionDayRating + source_bulletin render as <a> links."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        rated_day = today - datetime.timedelta(days=3)
        bulletin = BulletinFactory.create(
            valid_from=datetime.datetime(
                rated_day.year, rated_day.month, rated_day.day, 6, 0, tzinfo=UTC
            ),
            valid_to=datetime.datetime(
                rated_day.year, rated_day.month, rated_day.day, 15, 0, tzinfo=UTC
            ),
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        RegionDayRatingFactory.create(
            region=region,
            date=rated_day,
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.MODERATE,
            source_bulletin=bulletin,
        )
        response = client.get(_url(region.region_id.lower()), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        content = response.content.decode()
        href = reverse(
            "public:bulletin_date",
            args=[region.canonical_region_id, region.name_slug, rated_day.isoformat()],
        )
        cell = content.split(f'href="{href}"')[1].split(">")[0]
        assert f'data-date="{rated_day.isoformat()}"' in cell
        assert 'data-state="moderate"' in cell
        assert 'data-level="moderate"' in cell
        # A day with no bulletin is not a link.
        assert len(re.findall(r"<a\s", content)) == 1


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeasonPartialCache:
    """Tests for fragment-cache behaviour."""

    def setup_method(self) -> None:
        """Clear the cache before each test."""
        cache.clear()

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_second_request_issues_zero_db_queries(
        self, client: Client, django_assert_num_queries: Any
    ) -> None:
        """Second HTMX GET for the same (region, today) hits zero RegionDayRating queries.

        The view checks the template fragment cache directly before calling
        build_season_grid. On a cache hit it returns the cached HTML immediately,
        so no DB queries are issued at all on the second call.
        """
        region = MicroRegionFactory.create()
        url = _url(region.region_id.lower())
        # Prime the cache — first request populates the template fragment cache.
        response1 = client.get(url, HTTP_HX_REQUEST="true")
        assert response1.status_code == 200

        # Second request — must issue zero RegionDayRating queries (cache hit path
        # bypasses build_season_grid entirely).
        with django_assert_num_queries(0):
            response2 = client.get(url, HTTP_HX_REQUEST="true")
        assert response2.status_code == 200
        assert response1.content == response2.content

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_apply_bulletin_day_ratings_invalidates_cache(self, client: Client) -> None:
        """After priming the cache, apply_bulletin_day_ratings deletes the key."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        today_iso = today.isoformat()
        cache_key = season_calendar_cache_key(
            region.canonical_region_id, today_iso, translation.get_language()
        )

        # Prime the fragment cache via the partial endpoint.
        url = _url(region.region_id.lower())
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        # The cache key should exist now (the view's cache.set() call stores it on a miss).
        assert cache.get(cache_key) is not None

        # Build a minimal bulletin linked to the region and call apply.
        bulletin = BulletinFactory.create(
            valid_from=datetime.datetime(
                today.year, today.month, today.day, 6, 0, tzinfo=UTC
            ),
            valid_to=datetime.datetime(
                today.year, today.month, today.day, 15, 0, tzinfo=UTC
            ),
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        apply_bulletin_day_ratings(bulletin)

        # The cache key should have been deleted.
        assert cache.get(cache_key) is None

    def test_cache_key_carries_the_language(self) -> None:
        """SNOW-1128: one key per language; ``en-gb`` is the listed ``en``."""
        with override_settings(LANGUAGES=[("en", "English"), ("de", "Deutsch")]):
            en = season_calendar_cache_key("ch-4115", "2026-01-12", "en")
            en_gb = season_calendar_cache_key("ch-4115", "2026-01-12", "en-gb")
            de = season_calendar_cache_key("ch-4115", "2026-01-12", "de")
        assert en == en_gb
        assert en != de

    @override_settings(
        SEASON_START_DATE=_SEASON_START,
        LANGUAGES=[("en", "English"), ("de", "Deutsch")],
    )
    def test_apply_bulletin_day_ratings_invalidates_every_language(self) -> None:
        """SNOW-1128: ingest deletes the cached body for every language."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        keys = [
            season_calendar_cache_key(
                region.canonical_region_id, today.isoformat(), code
            )
            for code in ("en", "de")
        ]
        for key in keys:
            cache.set(key, b"stale", 90000)
        bulletin = BulletinFactory.create(
            valid_from=datetime.datetime(
                today.year, today.month, today.day, 6, 0, tzinfo=UTC
            ),
            valid_to=datetime.datetime(
                today.year, today.month, today.day, 15, 0, tzinfo=UTC
            ),
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        apply_bulletin_day_ratings(bulletin)
        assert [cache.get(key) for key in keys] == [None, None]


def test_split_day_label_is_one_sentence() -> None:
    """SNOW-1128 / SNOW-1158: a two-level day's title is one translatable sentence."""
    cell = SeasonCell(
        date=datetime.date(2026, 1, 5),
        min_rating_key="low",
        max_rating_key="moderate",
        subdivision="",
        has_bulletin=True,
        am_rating_key="low",
        pm_rating_key="moderate",
    )
    grid = SeasonGrid(columns=[(cell,)], month_labels=[""], season_label="25/26")
    html = render_to_string(
        "public/partials/_season_calendar.html",
        {
            "season_months": build_season_months(grid, datetime.date(2026, 1, 5)),
            "season_summary": summarise_season(grid),
            "linked": False,
        },
    )
    assert 'title="5 Jan 2026 · Low and Moderate"' in html


# ---------------------------------------------------------------------------
# SNOW-292 — ALBINA elevation-band HTML rendering in the calendar partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAlbinaCalendarTileRendering:
    """
    Assert that ALBINA band-split days render as two-level cells in the
    season_calendar_partial response (SNOW-1158 retired the band sub-divs).

    Regression guard: SLF and MF cells must NOT carry ``data-band-mode``.
    """

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_albina_elevation_only_cell_attributes(self, client: Client) -> None:
        """An elevation-only ALBINA day draws as a two-level cell."""
        region = MicroRegionFactory.create(region_id="at-9901")
        target = datetime.date(2025, 11, 4)
        bulletin = BulletinFactory.create()
        bands = [
            {
                "band_id": "above-2200",
                "label": "Above 2200 m",
                "rating_key": "considerable",
                "time_period": "all_day",
            },
            {
                "band_id": "below-2200",
                "label": "Below 2200 m",
                "rating_key": "low",
                "time_period": "all_day",
            },
        ]
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.LOW,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            source_bulletin=bulletin,
            source=Bulletin.Source.ALBINA,
            bands=bands,
        )
        response = client.get(_url(region.region_id.lower()), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        html = response.content.decode()
        # SNOW-1158: a band split is one two-level cell, the peak on the right.
        cell = html.split('data-date="2025-11-04"')[1].split(">")[0]
        assert 'data-state="split"' in cell
        assert 'data-lo="low" data-hi="considerable"' in cell
        assert 'data-level="considerable"' in cell

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_albina_elevation_time_cell_attributes(self, client: Client) -> None:
        """A 2×2 ALBINA day draws as a two-level cell: lowest band, peak band."""
        region = MicroRegionFactory.create(region_id="at-9902")
        target = datetime.date(2025, 11, 4)
        bulletin = BulletinFactory.create()
        bands = [
            {
                "band_id": "above-2500",
                "label": "Above 2500 m",
                "rating_key": "considerable",
                "time_period": "earlier",
            },
            {
                "band_id": "below-2500",
                "label": "Below 2500 m",
                "rating_key": "low",
                "time_period": "earlier",
            },
            {
                "band_id": "above-2800",
                "label": "Above 2800 m",
                "rating_key": "high",
                "time_period": "later",
            },
            {
                "band_id": "below-2800",
                "label": "Below 2800 m",
                "rating_key": "moderate",
                "time_period": "later",
            },
        ]
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.LOW,
            max_rating=RegionDayRating.Rating.HIGH,
            source_bulletin=bulletin,
            source=Bulletin.Source.ALBINA,
            bands=bands,
        )
        response = client.get(_url(region.region_id.lower()), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        html = response.content.decode()
        # SNOW-1158: four bands still draw two halves — the lowest and the peak.
        cell = html.split('data-date="2025-11-04"')[1].split(">")[0]
        assert 'data-state="split"' in cell
        assert 'data-lo="low" data-hi="high"' in cell

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_slf_cell_has_no_band_mode(self, client: Client) -> None:
        """SLF cell does not carry data-band-mode (regression guard)."""
        region = MicroRegionFactory.create(region_id="ch-9901")
        target = datetime.date(2025, 11, 4)
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.MODERATE,
            source_bulletin=bulletin,
            source=Bulletin.Source.SLF,
            bands=None,
        )
        response = client.get(_url(region.region_id.lower()), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        html = response.content.decode()
        assert "data-band-mode=" not in html

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_mf_cell_has_no_band_mode(self, client: Client) -> None:
        """MeteoFrance cell does not carry data-band-mode (regression guard)."""
        region = MicroRegionFactory.create(region_id="fr-9901")
        target = datetime.date(2025, 11, 4)
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.LOW,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            source_bulletin=bulletin,
            source=Bulletin.Source.METEOFRANCE,
            bands=None,
        )
        response = client.get(_url(region.region_id.lower()), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        html = response.content.decode()
        assert "data-band-mode=" not in html

    def test_main_css_draws_every_state_the_template_emits(self) -> None:
        """src/css/main.css has a rule for every data-state / data-lo / data-hi.

        The fills are plain CSS attribute selectors keyed on the rating
        values, not driven by the enum, so a renamed key would silently stop
        matching and the cell would draw empty with no test failure — this
        guard ties the two together.
        """
        from pathlib import Path

        css = Path("src/css/main.css").read_text(encoding="utf-8")
        for key in ("none", "future", "split", *RegionDayRating.Rating.values[1:]):
            assert f'.season-day[data-state="{key}"]' in css, key
        for key in RegionDayRating.Rating.values[1:-1]:
            assert f'.season-day[data-lo="{key}"]' in css, key
        for key in RegionDayRating.Rating.values[2:]:
            assert f'.season-day[data-hi="{key}"]' in css, key


# ---------------------------------------------------------------------------
# SNOW-291 — time-split tile rendering
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeasonPartialTimeSplit:
    """Tests for AM/PM days rendered by the partial (SNOW-291, redrawn by SNOW-1158)."""

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_equal_am_pm_draws_one_level(self, client: Client) -> None:
        """An AM/PM pair at one level is a one-level day, not a split."""
        region = MicroRegionFactory.create()
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create(
            valid_from=datetime.datetime(2025, 11, 4, 6, 0, tzinfo=UTC),
            valid_to=datetime.datetime(2025, 11, 4, 15, 0, tzinfo=UTC),
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2025, 11, 4),
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.MODERATE,
            am_rating=RegionDayRating.Rating.MODERATE,
            pm_rating=RegionDayRating.Rating.MODERATE,
            source_bulletin=bulletin,
        )

        from unittest.mock import patch

        with patch("django.utils.timezone.localdate", return_value=today):
            response = client.get(
                _url(region.region_id.lower()), HTTP_HX_REQUEST="true"
            )

        assert response.status_code == 200
        cell = response.content.decode().split('data-date="2025-11-04"')[1]
        assert cell.split(">")[0].count('data-state="moderate"') == 1

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_time_split_cell_tooltip_names_both_periods(self, client: Client) -> None:
        """An AM/PM split day names both levels, the peak second."""
        region = MicroRegionFactory.create()
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create(
            valid_from=datetime.datetime(2025, 11, 4, 6, 0, tzinfo=UTC),
            valid_to=datetime.datetime(2025, 11, 4, 15, 0, tzinfo=UTC),
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2025, 11, 4),
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            am_rating=RegionDayRating.Rating.MODERATE,
            pm_rating=RegionDayRating.Rating.CONSIDERABLE,
            source_bulletin=bulletin,
        )

        from unittest.mock import patch

        with patch("django.utils.timezone.localdate", return_value=today):
            response = client.get(
                _url(region.region_id.lower()), HTTP_HX_REQUEST="true"
            )

        assert response.status_code == 200
        cell = response.content.decode().split('data-date="2025-11-04"')[1]
        cell = cell.split(">")[0]
        assert 'data-state="split"' in cell
        assert 'data-lo="moderate" data-hi="considerable"' in cell
        assert 'aria-label="4 Nov 2025 · Moderate and Considerable"' in cell

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_uniform_cell_is_not_split(self, client: Client) -> None:
        """A uniform (non-split) day draws its one level."""
        region = MicroRegionFactory.create()
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create(
            valid_from=datetime.datetime(2025, 11, 4, 6, 0, tzinfo=UTC),
            valid_to=datetime.datetime(2025, 11, 4, 15, 0, tzinfo=UTC),
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2025, 11, 4),
            min_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            # No am_rating / pm_rating — uniform day.
            source_bulletin=bulletin,
        )

        from unittest.mock import patch

        with patch("django.utils.timezone.localdate", return_value=today):
            response = client.get(
                _url(region.region_id.lower()), HTTP_HX_REQUEST="true"
            )

        assert response.status_code == 200
        cell = response.content.decode().split('data-date="2025-11-04"')[1]
        assert 'data-state="considerable"' in cell.split(">")[0]
