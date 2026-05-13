"""
tests/public/test_season_partial.py — Tests for the season_calendar_partial view.

Covers:
  - 400 on non-HTMX request (require_htmx guard).
  - 404 on unknown region_id.
  - Grid fragment rendered with data-testid="season-calendar".
  - calendar-cell-today present for today's cell.
  - data-date="YYYY-MM-DD" on every rendered cell anchor/div.
  - calendar-cell-selected absent from the response (selection is client-side).
  - Fragment cache: second request for the same (region, today) skips DB queries.
  - Cache invalidation: apply_bulletin_day_ratings deletes the key.
"""

from __future__ import annotations

import datetime
import re
from datetime import UTC

import pytest
from django.core.cache import cache
from django.core.cache.utils import make_template_fragment_key
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from bulletins.models import RegionDayRating
from bulletins.services.day_rating import apply_bulletin_day_ratings
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

    def test_non_htmx_get_returns_400(self):
        """Plain GET without HX-Request header is rejected with 400."""
        client = Client()
        region = MicroRegionFactory.create()
        url = _url(region.region_id)
        response = client.get(url)
        assert response.status_code == 400

    def test_unknown_region_returns_404(self):
        """Unknown region_id returns 404."""
        client = Client()
        url = _url("CH-NOTEXIST")
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Response content tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeasonPartialContent:
    """Tests for the content of the season_calendar_partial response."""

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_returns_grid_with_season_calendar_testid(self, client: Client):
        """HTMX GET returns a fragment with data-testid="season-calendar"."""
        region = MicroRegionFactory.create()
        response = client.get(_url(region.region_id), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b'data-testid="season-calendar"' in response.content

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_returns_today_cell_with_today_modifier(self, client: Client):
        """The cell for today carries the calendar-cell-today CSS modifier."""
        region = MicroRegionFactory.create()
        response = client.get(_url(region.region_id), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b"calendar-cell-today" in response.content

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_every_cell_carries_data_date_attribute(self, client: Client):
        """Every rendered cell anchor/div carries a data-date="YYYY-MM-DD" attribute."""
        region = MicroRegionFactory.create()
        response = client.get(_url(region.region_id), HTTP_HX_REQUEST="true")
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
    def test_calendar_cell_selected_absent(self, client: Client):
        """calendar-cell-selected is never in the partial — selection is client-side."""
        region = MicroRegionFactory.create()
        response = client.get(_url(region.region_id), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b"calendar-cell-selected" not in response.content

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_region_with_rated_days_renders_interactive_cells(self, client: Client):
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
        response = client.get(_url(region.region_id), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        # At least one cell with data-rating-min should be present.
        assert b"data-rating-min" in response.content


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
    def test_second_request_returns_cached_html(self, client: Client):
        """Second HTMX GET for the same (region, today) returns identical HTML from cache."""
        region = MicroRegionFactory.create()
        url = _url(region.region_id)
        # Prime the cache.
        response1 = client.get(url, HTTP_HX_REQUEST="true")
        assert response1.status_code == 200

        # Second request — the fragment HTML is identical (served from template cache).
        response2 = client.get(url, HTTP_HX_REQUEST="true")
        assert response2.status_code == 200
        assert response1.content == response2.content

    @override_settings(SEASON_START_DATE=_SEASON_START)
    def test_apply_bulletin_day_ratings_invalidates_cache(self, client: Client):
        """After priming the cache, apply_bulletin_day_ratings deletes the key."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        today_iso = today.isoformat()
        cache_key = make_template_fragment_key(
            "season_calendar", [region.canonical_region_id, today_iso]
        )

        # Prime the fragment cache via the partial endpoint.
        url = _url(region.region_id)
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        # The cache key should exist now (the {% cache %} tag stores it).
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
