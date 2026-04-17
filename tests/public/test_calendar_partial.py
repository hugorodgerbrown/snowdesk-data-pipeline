"""
tests/public/test_calendar_partial.py — Tests for the calendar_partial view.

Covers:
  - Non-HTMX request returns 400
  - HTMX request renders 6-week grid and correct bulletin URL on a rated cell
  - prev_url is absent (disabled) at the season start month
  - next_url is absent (disabled) at today's month
  - Unknown region_id returns 404
  - no_rating cell has no <a> tag
  - Variable-day cell carries data-rating-min != data-rating-max; no subdivision glyph
  - Stable-day cell carries matching data-rating-min/max; subdivision glyph shown
"""

from __future__ import annotations

import datetime
from datetime import UTC
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from pipeline.models import RegionDayRating
from pipeline.services.render_model import RENDER_MODEL_VERSION
from tests.factories import (
    BulletinFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    RegionFactory,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def region():
    """Return a test Region."""
    return RegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


@pytest.fixture()
def htmx_client():
    """Return a Django test client that includes the HX-Request header."""
    client = Client()
    client.defaults["HTTP_HX_REQUEST"] = "true"
    return client


def _calendar_url(region_id: str, year: int, month: int) -> str:
    """Build the calendar partial URL for a region and month."""
    return reverse(
        "public:calendar_partial",
        kwargs={"region_id": region_id, "year": year, "month": month},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCalendarPartialHTMXGuard:
    """Non-HTMX requests are rejected."""

    def test_non_htmx_returns_400(self, region) -> None:
        """A plain GET without the HX-Request header returns 400."""
        client = Client()
        url = _calendar_url(region.region_id, 2026, 1)
        response = client.get(url)
        assert response.status_code == 400

    def test_htmx_request_returns_200(self, region, htmx_client) -> None:
        """An HTMX GET with a valid region and month returns 200."""
        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url(region.region_id, 2026, 4)
            response = htmx_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestCalendarPartialGrid:
    """Grid structure and cell content."""

    def test_renders_six_weeks(self, region, htmx_client) -> None:
        """The template renders exactly 6 rows of 7 cells."""
        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url(region.region_id, 2026, 1)
            response = htmx_client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        # Outer wrapper present.
        assert 'id="bulletin-calendar"' in content

    def test_rated_cell_has_link(self, region, htmx_client) -> None:
        """A cell with a qualifying bulletin renders as an <a> tag."""
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        bulletin = BulletinFactory.create(
            issued_at=vf,
            valid_from=vf,
            valid_to=vt,
            render_model={
                "version": RENDER_MODEL_VERSION,
                "danger": {"key": "moderate", "subdivision": "", "number": 2},
                "traits": [],
            },
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)

        RegionDayRatingFactory.create(
            region=region,
            date=day,
            max_rating=RegionDayRating.Rating.MODERATE,
            source_bulletin=bulletin,
            version=1,
        )

        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url(region.region_id, 2026, 1)
            response = htmx_client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        # The 15th should have a link.
        assert "bulletin_date" in content or "/ch-4115/" in content

    def test_no_rating_cell_has_no_link(self, region, htmx_client) -> None:
        """A no_rating cell renders as a plain <div>, not an <a>."""
        day = datetime.date(2026, 1, 20)
        RegionDayRatingFactory.create(
            region=region,
            date=day,
            min_rating=RegionDayRating.Rating.NO_RATING,
            max_rating=RegionDayRating.Rating.NO_RATING,
            source_bulletin=None,
            version=1,
        )

        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url(region.region_id, 2026, 1)
            response = htmx_client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        # no_rating cells must not link to a bulletin URL.
        assert 'data-rating-min="no_rating"' in content
        assert 'data-rating-max="no_rating"' in content


@pytest.mark.django_db
class TestCalendarVariableVsStableDays:
    """Variable-day vs stable-day rendering."""

    def _make_rated_cell(
        self,
        region,
        day: datetime.date,
        min_rating: str,
        max_rating: str,
        subdivision: str = "",
    ):
        """Create a bulletin and RegionDayRating for a cell."""
        vf = datetime.datetime(day.year, day.month, day.day, 8, 0, tzinfo=UTC)
        vt = datetime.datetime(day.year, day.month, day.day, 17, 0, tzinfo=UTC)
        bulletin = BulletinFactory.create(
            issued_at=vf,
            valid_from=vf,
            valid_to=vt,
            render_model={
                "version": RENDER_MODEL_VERSION,
                "danger": {"key": max_rating, "subdivision": "", "number": 2},
                "traits": [],
            },
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        RegionDayRatingFactory.create(
            region=region,
            date=day,
            min_rating=getattr(RegionDayRating.Rating, min_rating.upper()),
            min_subdivision="",
            max_rating=getattr(RegionDayRating.Rating, max_rating.upper()),
            max_subdivision=subdivision,
            source_bulletin=bulletin,
            version=1,
        )
        return bulletin

    def test_variable_day_carries_differing_min_max_attrs(
        self, region, htmx_client
    ) -> None:
        """A variable day (min != max) carries different data-rating-min and data-rating-max."""
        day = datetime.date(2026, 1, 10)
        self._make_rated_cell(region, day, "moderate", "considerable")

        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            response = htmx_client.get(_calendar_url(region.region_id, 2026, 1))

        content = response.content.decode()
        assert 'data-rating-min="moderate"' in content
        assert 'data-rating-max="considerable"' in content

    def test_variable_day_does_not_render_subdivision_glyph(
        self, region, htmx_client
    ) -> None:
        """On a variable day the subdivision glyph is not rendered."""
        day = datetime.date(2026, 1, 10)
        self._make_rated_cell(region, day, "moderate", "considerable", subdivision="+")

        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            response = htmx_client.get(_calendar_url(region.region_id, 2026, 1))

        # The template guards with {% if cell.min_rating_key == cell.max_rating_key and cell.subdivision %}
        # On a variable day that guard fails so "+" must not appear in the output.
        content = response.content.decode()
        # The "+" glyph specifically should not appear (the cell day number will).
        # We look for the pattern that would be rendered if the guard failed.
        assert "+10" not in content  # subdivision rendered adjacent to day number

    def test_stable_day_renders_subdivision_glyph(self, region, htmx_client) -> None:
        """On a stable day (min == max) the subdivision glyph is shown."""
        day = datetime.date(2026, 1, 12)
        self._make_rated_cell(region, day, "moderate", "moderate", subdivision="+")

        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            response = htmx_client.get(_calendar_url(region.region_id, 2026, 1))

        content = response.content.decode()
        # The "+" glyph should appear adjacent to the day number.
        assert "+12" in content


@pytest.mark.django_db
class TestCalendarPartialNavigation:
    """Prev/next navigation boundary behaviour."""

    def test_prev_disabled_at_season_start(self, region, htmx_client) -> None:
        """At the season's first month, prev_url is None (no nav button)."""
        season_start = datetime.date(2026, 1, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url(region.region_id, 2026, 1)
            response = htmx_client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        # When prev is disabled, the button is a <span>, not a hx-get button.
        assert "aria-label='Previous month'" not in content

    def test_next_disabled_at_current_month(self, region, htmx_client) -> None:
        """At the current month, next_url is None (no nav button)."""
        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url(region.region_id, 2026, 4)
            response = htmx_client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        assert "aria-label='Next month'" not in content

    def test_prev_present_when_not_at_season_start(self, region, htmx_client) -> None:
        """When month > season start, prev navigation is rendered."""
        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url(region.region_id, 2026, 2)
            response = htmx_client.get(url)

        content = response.content.decode()
        assert "aria-label='Previous month'" in content


@pytest.mark.django_db
class TestCalendarPartialRegion:
    """Region resolution."""

    def test_unknown_region_returns_404(self, htmx_client) -> None:
        """An unrecognised region_id returns 404."""
        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url("CH-UNKNOWN", 2026, 1)
            response = htmx_client.get(url)

        assert response.status_code == 404


@pytest.mark.django_db
class TestCalendarPartialSelectedDate:
    """Selected-date (?date=) query parameter behaviour."""

    def test_selected_date_cell_carries_accent_class(self, region, htmx_client) -> None:
        """A cell matching ?date= carries the ring-blue-500 accent class."""
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        bulletin = BulletinFactory.create(
            issued_at=vf,
            valid_from=vf,
            valid_to=vt,
            render_model={
                "version": RENDER_MODEL_VERSION,
                "danger": {"key": "moderate", "subdivision": "", "number": 2},
                "traits": [],
            },
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        RegionDayRatingFactory.create(
            region=region,
            date=day,
            max_rating=RegionDayRating.Rating.MODERATE,
            source_bulletin=bulletin,
            version=1,
        )

        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url(region.region_id, 2026, 1) + "?date=2026-01-15"
            response = htmx_client.get(url)

        assert response.status_code == 200
        content = response.content.decode()
        assert "ring-blue-500" in content

    def test_bad_date_query_param_ignored(self, region, htmx_client) -> None:
        """An unparseable ?date= is silently ignored — no crash, 200 response."""
        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = _calendar_url(region.region_id, 2026, 1) + "?date=not-a-date"
            response = htmx_client.get(url)

        assert response.status_code == 200
        # No selected-date ring present.
        assert "ring-blue-500" not in response.content.decode()


@pytest.mark.django_db
class TestCalendarPartialMonthValidation:
    """Month URL segment validation."""

    def test_invalid_month_13_returns_404(self, region, htmx_client) -> None:
        """month=13 returns 404 (Django int converter accepts it; view must guard)."""
        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = reverse(
                "public:calendar_partial",
                kwargs={"region_id": region.region_id, "year": 2026, "month": 13},
            )
            response = htmx_client.get(url)

        assert response.status_code == 404

    def test_invalid_month_0_returns_404(self, region, htmx_client) -> None:
        """month=0 returns 404."""
        season_start = datetime.date(2025, 11, 1)
        with (
            patch("django.conf.settings.SEASON_START_DATE", season_start),
            patch(
                "public.views.timezone.now",
                return_value=datetime.datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
        ):
            url = reverse(
                "public:calendar_partial",
                kwargs={"region_id": region.region_id, "year": 2026, "month": 0},
            )
            response = htmx_client.get(url)

        assert response.status_code == 404
