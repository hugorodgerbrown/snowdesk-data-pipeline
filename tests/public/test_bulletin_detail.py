"""
tests/public/test_bulletin_detail.py — Tests for day-based bulletin navigation.

Covers the bulletin_detail view and its helpers: _select_bulletin_for_date
and _get_nav_dates.  Bulletins follow the SLF pattern:

  * PM (evening) bulletin: valid_from ~15:00 day D, valid_to ~15:00 day D+1
  * AM (morning) bulletin: valid_from ~06:00 day D, valid_to ~15:00 day D

For past days the morning bulletin is preferred; for the current day the
bulletin whose validity window contains *now* is shown.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Generator
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.bulletins.services.day_rating import recompute_region_day
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.bulletins.services.selection import select_bulletin_for_date
from apps.public.views import (
    _get_nav_dates,
    _has_later_bulletin,
    _issues_for_date,
    _resolve_selected_issue,
    _select_default_issue,
)
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    FavouriteFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    ResortFactory,
    UserFactory,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Generator[None, None, None]:
    """Clear the cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def region() -> MicroRegion:
    """Return a test Region."""
    return MicroRegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


def _make_pm_bulletin(region: MicroRegion, day: date, **kwargs: Any) -> Bulletin:
    """Create an evening bulletin valid from 15:00 on *day* to 15:00 next day."""
    vf = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    vt = vf + timedelta(hours=24)
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        **kwargs,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


def _make_am_bulletin(region: MicroRegion, day: date, **kwargs: Any) -> Bulletin:
    """Create a morning bulletin valid from 06:00 to 15:00 on *day*."""
    vf = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
    vt = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        **kwargs,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


def _make_overnight_bulletin(region: MicroRegion, day: date, **kwargs: Any) -> Bulletin:
    """Create a bulletin forecasting *day* whose validity runs past midnight.

    Valid from 11:00 on *day* (so ``target_day_for_valid_from`` gives *day*)
    to 05:00 the next morning — its ``valid_to`` date is the next day.
    """
    vf = datetime(day.year, day.month, day.day, 11, 0, tzinfo=UTC)
    vt = datetime(day.year, day.month, day.day, 5, 0, tzinfo=UTC) + timedelta(days=1)
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        **kwargs,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


def _freeze(dt_str: str) -> Any:  # mock-typing-impractical
    """Return a patch that freezes django.utils.timezone.now to *dt_str*."""
    frozen = datetime.fromisoformat(dt_str)
    return patch("django.utils.timezone.now", return_value=frozen)


# ── _select_bulletin_for_date ────────────────────────────────────────────────


@pytest.mark.django_db
class TestSelectBulletinForDate:
    """Tests for the _select_bulletin_for_date helper."""

    def test_past_date_prefers_am_bulletin(self, region: MicroRegion) -> None:
        """On a past date with both AM and PM bulletins, the AM is chosen."""
        day = date(2026, 3, 15)
        _make_pm_bulletin(region, date(2026, 3, 14))  # PM covers 3/15
        am = _make_am_bulletin(region, day)

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == am.pk

    def test_past_date_falls_back_to_pm_if_no_am(self, region: MicroRegion) -> None:
        """On a past date with only a PM bulletin, that is returned."""
        day = date(2026, 3, 15)
        pm = _make_pm_bulletin(region, date(2026, 3, 14))  # PM covers 3/15

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == pm.pk

    def test_today_returns_currently_valid_am(self, region: MicroRegion) -> None:
        """During today's AM window the AM bulletin is selected."""
        day = date(2026, 3, 15)
        _make_pm_bulletin(region, date(2026, 3, 14))  # PM covers until 15:00
        am = _make_am_bulletin(region, day)  # AM: 06:00 - 15:00

        with _freeze("2026-03-15T10:00:00+00:00"):
            result = select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == am.pk

    def test_today_does_not_depend_on_the_hour(self, region: MicroRegion) -> None:
        """The day's latest issue is picked at any hour, as the map picks it."""
        day = date(2026, 3, 15)
        _make_pm_bulletin(region, date(2026, 3, 14))
        am = _make_am_bulletin(region, day)

        with _freeze("2026-03-15T04:00:00+00:00"):
            result = select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == am.pk

    def test_no_bulletins_returns_none(self, region: MicroRegion) -> None:
        """When no bulletins exist for a date, None is returned."""
        with _freeze("2026-03-20T12:00:00+00:00"):
            result = select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is None


# ── _get_nav_dates ───────────────────────────────────────────────────────────


def _rate(region: MicroRegion, *days: date) -> None:
    """Create a RegionDayRating row for *region* on each of *days*."""
    for day in days:
        RegionDayRatingFactory.create(region=region, date=day)


@pytest.mark.django_db
class TestGetNavDates:
    """Tests for the _get_nav_dates helper.

    The lower bound is the region's earliest RegionDayRating row (SNOW-1344).
    """

    def test_returns_prev_and_next(self, region: MicroRegion) -> None:
        """Prev and next are calendar adjacents regardless of rating gaps."""
        _rate(region, date(2026, 3, 14))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, next_date = _get_nav_dates(region, date(2026, 3, 15))

        assert prev_date == date(2026, 3, 14)
        assert next_date == date(2026, 3, 16)

    def test_no_prev_at_earliest(self, region: MicroRegion) -> None:
        """The earliest rated day has no prev_date."""
        _rate(region, date(2026, 3, 14), date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, _ = _get_nav_dates(region, date(2026, 3, 14))

        assert prev_date is None

    def test_next_is_tomorrow_when_today(self, region: MicroRegion) -> None:
        """On today, next_date is tomorrow (the adjacent calendar day)."""
        _rate(region, date(2026, 3, 14), date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            _, next_date = _get_nav_dates(region, date(2026, 3, 15))

        assert next_date == date(2026, 3, 16)

    def test_does_not_skip_gaps(self, region: MicroRegion) -> None:
        """Navigation steps one calendar day at a time, not to the next rating."""
        _rate(region, date(2026, 3, 10), date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, _ = _get_nav_dates(region, date(2026, 3, 15))

        assert prev_date == date(2026, 3, 14)

    def test_no_next_at_tomorrow(self, region: MicroRegion) -> None:
        """When current_date is tomorrow, next_date is None (upper bound)."""
        _rate(region, date(2026, 3, 14))

        with _freeze("2026-03-15T10:00:00+00:00"):
            _, next_date = _get_nav_dates(region, date(2026, 3, 16))

        assert next_date is None

    def test_no_prev_when_region_has_no_ratings(self, region: MicroRegion) -> None:
        """A region with no rated day has no prev_date."""
        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, next_date = _get_nav_dates(region, date(2026, 3, 15))

        assert prev_date is None
        assert next_date == date(2026, 3, 16)

    def test_other_regions_ratings_do_not_count(self, region: MicroRegion) -> None:
        """Only the region's own rows set its lower bound."""
        other = MicroRegionFactory.create(region_id="CH-4116")
        _rate(other, date(2026, 3, 1))
        _rate(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, _ = _get_nav_dates(region, date(2026, 3, 15))

        assert prev_date is None

    def test_one_query_with_no_bulletin_join(
        self,
        region: MicroRegion,
        django_assert_num_queries: Any,  # pytest-django fixture, untyped
    ) -> None:
        """The bound is one aggregate over RegionDayRating, never the bulletin M2M."""
        _rate(region, date(2026, 3, 14))

        with (
            _freeze("2026-03-20T12:00:00+00:00"),
            django_assert_num_queries(1) as captured,
        ):
            _get_nav_dates(region, date(2026, 3, 15))

        sql = captured.captured_queries[0]["sql"].lower()
        assert "regiondayrating" in sql
        assert "regionbulletin" not in sql
        assert "bulletins_bulletin" not in sql


# ── _has_later_bulletin ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestHasLaterBulletin:
    """Tests for the _has_later_bulletin helper."""

    def test_returns_true_when_later_bulletin_exists(self, region: MicroRegion) -> None:
        """Returns True when a bulletin forecasts a day after page_date."""
        _make_am_bulletin(region, date(2026, 3, 15))  # forecasts 3/15
        _make_am_bulletin(region, date(2026, 3, 16))  # forecasts 3/16

        assert _has_later_bulletin(region, date(2026, 3, 15)) is True

    def test_tonights_issue_for_today_is_not_later(self, region: MicroRegion) -> None:
        """An issue valid past midnight that forecasts today is not later.

        Its ``valid_to`` falls on tomorrow, so the old ``valid_to__date__gt``
        filter counted it and hid the next-update notice (SNOW-1344).
        """
        _make_am_bulletin(region, date(2026, 3, 15))
        tonight = _make_overnight_bulletin(region, date(2026, 3, 15))
        assert tonight.valid_to.date() == date(2026, 3, 16)
        assert tonight.target_date == date(2026, 3, 15)

        assert _has_later_bulletin(region, date(2026, 3, 15)) is False

    def test_evening_issue_for_tomorrow_is_later(self, region: MicroRegion) -> None:
        """Tonight's evening issue forecasting tomorrow is a real later day."""
        _make_am_bulletin(region, date(2026, 3, 15))
        evening = _make_pm_bulletin(region, date(2026, 3, 15))
        assert evening.target_date == date(2026, 3, 16)

        assert _has_later_bulletin(region, date(2026, 3, 15)) is True

    def test_ignores_rows_without_a_target_date(self, region: MicroRegion) -> None:
        """A bulletin with no target_date is not counted, whatever its valid_to."""
        _make_am_bulletin(region, date(2026, 3, 15))
        later = _make_am_bulletin(region, date(2026, 3, 17))
        Bulletin.objects.filter(pk=later.pk).update(target_date=None)

        assert _has_later_bulletin(region, date(2026, 3, 15)) is False

    def test_returns_false_when_no_later_bulletin(self, region: MicroRegion) -> None:
        """Returns False when no bulletin exists after page_date."""
        _make_am_bulletin(region, date(2026, 3, 15))  # valid_to: 15:00 on 3/15

        assert _has_later_bulletin(region, date(2026, 3, 15)) is False

    def test_returns_false_for_empty_region(self, region: MicroRegion) -> None:
        """Returns False when the region has no bulletins at all."""
        assert _has_later_bulletin(region, date(2026, 3, 15)) is False


# ── bulletin_detail view ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBulletinDetailView:
    """Integration tests for the bulletin_detail view."""

    def test_default_shows_today(self, client: Client, region: MicroRegion) -> None:
        """Without a date param the view shows today's bulletin."""
        day = date(2026, 3, 15)
        am = _make_am_bulletin(region, day)

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"].pk == am.pk
        assert response.context["is_today"] is True

    def test_date_segment_selects_day(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A date URL segment selects the requested day."""
        am_14 = _make_am_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-14",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"].pk == am_14.pk
        assert response.context["is_today"] is False

    def test_invalid_date_redirects_to_canonical_today(
        self, client: Client, region: MicroRegion
    ) -> None:
        """An invalid date segment falls back to today and redirects."""
        # ``_parse_target_date`` falls back to today on unparseable input,
        # which makes the inbound path non-canonical (the slug
        # "not-a-date" doesn't match today's ISO string), so the form-3
        # wrapper 302s to the canonical URL with today's date.
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "not-a-date",
                },
            )
            response = client.get(url)

        assert response.status_code == 302
        assert response["Location"] == "/ch-4115/valais/2026-03-15/"

    def test_no_bulletin_shows_empty_state(
        self, client: Client, region: MicroRegion
    ) -> None:
        """When no bulletin exists for the date the callout empty state is rendered."""
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"] is None
        content = response.content.decode()
        assert 'data-testid="callout"' in content
        assert 'data-kind="info"' in content
        assert "No bulletin available" in content

    def test_no_bulletin_has_nav_data_attrs(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The empty-state page carries data-prev-url / data-next-url attributes."""
        # A rated day on 2026-03-14 anchors the lower bound so both prev and
        # next are populated when viewing 2026-03-15 (no bulletin on that day).
        _make_am_bulletin(region, date(2026, 3, 14))
        _rate(region, date(2026, 3, 14))

        with _freeze("2026-03-20T12:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"] is None
        content = response.content.decode()
        assert "data-prev-url=" in content
        assert "data-next-url=" in content

    def test_future_date_beyond_tomorrow_renders_empty_state(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A direct URL five days in the future renders the callout and has no next link."""
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            # today+5 = 2026-03-20, which is beyond tomorrow (2026-03-16)
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-20",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"] is None
        content = response.content.decode()
        assert 'data-testid="callout"' in content
        # Upper bound: tomorrow is 2026-03-16, so 2026-03-20 has no next link
        assert "data-next-url=" not in content

    def test_empty_state_includes_subregion_name(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The subregion subtitle renders on an empty-state bulletin page."""
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"] is None
        assert response.context["subregion_name"] != ""
        content = response.content.decode()
        assert response.context["subregion_name"] in content

    def test_prev_next_dates_in_context(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Prev/next navigation context exposes the adjacent calendar days."""
        _make_am_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))
        _make_am_bulletin(region, date(2026, 3, 16))
        # The lower bound is the earliest rated day (SNOW-1344).
        _rate(region, date(2026, 3, 14))

        with _freeze("2026-03-20T12:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.context["prev_date"] == date(2026, 3, 14)
        assert response.context["next_date"] == date(2026, 3, 16)

    def test_today_label_in_page_title(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Today's bulletin renders the ``Today`` label in the page title."""
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        content = response.content.decode()
        assert "Today" in content

    def test_past_date_shown_in_header(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A past page date appears in the bulletin header."""
        _make_am_bulletin(region, date(2026, 3, 14))

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-14",
                },
            )
            response = client.get(url)

        content = response.content.decode()
        # The bulletin header uses the ``D j M`` format (no year).
        assert "Sat 14 Mar" in content

    def test_next_update_context_populated_today_before_due(
        self, client: Client, region: MicroRegion
    ) -> None:
        """On today, before the next bulletin is due, ``next_update_time`` is set."""
        # Context is still populated so a future chrome element (e.g. a
        # ``next: HH:MM`` tooltip on the disabled `»` chip) can opt in.
        # The current layout does not surface the value in the DOM.
        am = _make_am_bulletin(region, date(2026, 3, 15))
        from apps.bulletins.models import Bulletin

        Bulletin.objects.filter(pk=am.pk).update(
            next_update=datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
        )

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.context["next_update_time"] is not None

    def test_no_next_update_after_due_time(
        self, client: Client, region: MicroRegion
    ) -> None:
        """After the next_update time has passed, the disabled label is absent."""
        am = _make_am_bulletin(region, date(2026, 3, 15))
        from apps.bulletins.models import Bulletin

        Bulletin.objects.filter(pk=am.pk).update(
            next_update=datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
        )

        with _freeze("2026-03-15T16:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.context["next_update_time"] is None

    def test_next_update_shown_after_17_00(
        self, client: Client, region: MicroRegion
    ) -> None:
        """After 17:00, an overnight issue for today keeps the notice up.

        The issue's ``valid_to`` falls on tomorrow; before SNOW-1344 that
        alone made ``_has_later_bulletin`` True and hid the notice.
        """
        _make_am_bulletin(region, date(2026, 3, 15))
        _make_overnight_bulletin(
            region,
            date(2026, 3, 15),
            next_update=datetime(2026, 3, 16, 7, 0, tzinfo=UTC),
        )

        with _freeze("2026-03-15T17:30:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.context["next_update_time"] == datetime(
            2026, 3, 16, 7, 0, tzinfo=UTC
        )

    def test_unknown_region_returns_404(self, client: Client) -> None:
        """A region ID that doesn't match any Region should 404."""
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "xx-9999",
                "slug": "nowhere",
                "date_str": "2026-03-15",
            },
        )
        response = client.get(url)

        assert response.status_code == 404

    def test_stale_render_model_reads_as_the_error_sentinel(
        self, client: Client, region: MicroRegion, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A stale row is not rebuilt per request: it renders the error card.

        SNOW-1342 — the builder is never called on a read path; the warning
        names the versions so an operator knows to run the rebuild.
        """
        am = _make_am_bulletin(region, date(2026, 3, 15), render_model_version=1)
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
        )

        with patch("apps.public.views.RENDER_MODEL_VERSION", 2):
            with patch(
                "apps.ingest.normalise.render_model.build_render_model"
            ) as builder:
                with _freeze("2026-03-15T10:00:00+00:00"):
                    with caplog.at_level("WARNING", logger="apps.public.views"):
                        response = client.get(url)

        builder.assert_not_called()
        assert response.status_code == 200
        assert response.context["bulletin"].pk == am.pk
        panel = response.context.get("panel")
        assert panel is not None
        assert panel["render_model"]["version"] == 0
        assert panel["render_model"]["error_type"] == "StaleRenderModel"
        assert any(
            "stale render_model" in record.message
            and "stored version=1" in record.message
            and "current=2" in record.message
            for record in caplog.records
        )

    def test_stored_build_failure_keeps_its_own_error(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A stored version-0 failure surfaces its own message, not a stale one."""
        am = _make_am_bulletin(region, date(2026, 3, 15))
        am.render_model = {
            "version": 0,
            "error": "validation failed",
            "error_type": "RenderModelBuildError",
        }
        am.render_model_version = 0
        am.save()
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
        )

        with _freeze("2026-03-15T10:00:00+00:00"):
            response = client.get(url)

        assert response.status_code == 200
        rm = response.context["panel"]["render_model"]
        assert rm["error"] == "validation failed"
        assert rm["error_type"] == "RenderModelBuildError"

    @override_settings(DEBUG=False)
    def test_anonymous_page_never_reads_the_caaml(
        self, client: Client, region: MicroRegion
    ) -> None:
        """No query an anonymous bulletin page runs reads the CAAML (SNOW-1342).

        ``DEBUG=False`` as in production: under ``DEBUG`` the page embeds
        the service's CAAML for a developer, by design (SNOW-1337).
        """
        _make_am_bulletin(
            region,
            date(2026, 3, 15),
            held={"bulletinID": "raw-marker"},
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
        )

        with CaptureQueriesContext(connection) as ctx:
            with _freeze("2026-03-15T10:00:00+00:00"):
                response = client.get(url)

        assert response.status_code == 200
        assert response.context.get("caaml_json") is None
        assert not [
            q["sql"]
            for q in ctx.captured_queries
            if "ingest_sourcebulletin" in q["sql"]
        ]

    def test_superuser_page_embeds_the_services_caaml(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A superuser's page carries the CAAML the service holds."""
        _make_am_bulletin(
            region,
            date(2026, 3, 15),
            held={"bulletinID": "raw-marker"},
        )
        client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
        )

        with _freeze("2026-03-15T10:00:00+00:00"):
            response = client.get(url)

        assert "raw-marker" in response.context["caaml_json"]


# ── Issue discovery and selection ────────────────────────────────────────────


@pytest.mark.django_db
class TestIssuesForDate:
    """All three SLF-style issues covering a calendar day are returned."""

    def test_returns_the_issues_that_forecast_the_day(
        self, region: MicroRegion
    ) -> None:
        """Previous evening + morning forecast day D; D's evening forecasts D+1."""
        prev_evening = _make_pm_bulletin(region, date(2026, 3, 14))
        am = _make_am_bulletin(region, date(2026, 3, 15))
        same_evening = _make_pm_bulletin(region, date(2026, 3, 15))

        issues = _issues_for_date(region, date(2026, 3, 15))

        ids = [b.pk for b in issues]
        assert ids == [prev_evening.pk, am.pk], (
            "issues must be returned in chronological (valid_from) order, "
            f"without the evening issue that forecasts tomorrow; got {ids}"
        )
        assert [b.pk for b in _issues_for_date(region, date(2026, 3, 16))] == [
            same_evening.pk
        ]

    def test_an_evening_issue_alone_belongs_to_tomorrow(
        self, region: MicroRegion
    ) -> None:
        """
        The first issue of a season, out at 17:00 on D, shows on D+1 only.

        SNOW-1207: 23 Oct 2026's 15:00 UTC SLF issue appeared in the panel on
        23 Oct while the map, which files it under 24 Oct, stayed grey.
        """
        first = _make_pm_bulletin(region, date(2026, 10, 23))

        assert _issues_for_date(region, date(2026, 10, 23)) == []
        assert [b.pk for b in _issues_for_date(region, date(2026, 10, 24))] == [
            first.pk
        ]

    def test_empty_when_no_bulletins_touch_day(self, region: MicroRegion) -> None:
        """Days with no valid bulletins return an empty list."""
        _make_am_bulletin(region, date(2026, 3, 10))
        assert _issues_for_date(region, date(2026, 3, 15)) == []


@pytest.mark.django_db
class TestDefaultIssueSelection:
    """The default issue is the latest issue that forecasts the day."""

    def test_past_day_prefers_morning_update_over_previous_evening(
        self, region: MicroRegion
    ) -> None:
        """
        At the 10:00 pivot both the morning update AND the previous-day
        evening are valid — the morning update wins because it is the
        latest-issued refresh.
        """
        _make_pm_bulletin(
            region, date(2026, 3, 14)
        )  # prev evening → valid to 3/15 15:00
        am = _make_am_bulletin(region, date(2026, 3, 15))
        _make_pm_bulletin(region, date(2026, 3, 15))  # irrelevant (after 10:00)

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is not None and result.pk == am.pk

    def test_past_day_falls_back_to_previous_evening_when_no_morning(
        self, region: MicroRegion
    ) -> None:
        """Without a morning update, the previous-day evening covers 10:00."""
        prev_evening = _make_pm_bulletin(region, date(2026, 3, 14))
        # No AM today.

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is not None and result.pk == prev_evening.pk

    def test_today_after_17_keeps_todays_issue(self, region: MicroRegion) -> None:
        """
        At 18:00 today's morning issue still shows, though tonight's is out.

        Tonight's issue forecasts tomorrow, so it belongs to tomorrow; before
        SNOW-1207 the panels switched to it while the map kept today's.
        """
        am = _make_am_bulletin(region, date(2026, 3, 15))
        _make_pm_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T18:00:00+00:00"):
            result = select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is not None and result.pk == am.pk

    @pytest.mark.parametrize(
        "make",
        [
            pytest.param(("prev",), id="previous-evening-only"),
            pytest.param(("prev", "am"), id="evening-and-morning"),
            pytest.param(("prev", "am", "pm"), id="all-three"),
            pytest.param(("pm",), id="todays-evening-only"),
        ],
    )
    def test_agrees_with_the_map(self, region: MicroRegion, make: tuple) -> None:
        """The panels' pick is the bulletin the map's day rating was built from."""
        builders = {
            "prev": lambda: _make_pm_bulletin(
                region, date(2026, 3, 14), render_model_version=RENDER_MODEL_VERSION
            ),
            "am": lambda: _make_am_bulletin(
                region, date(2026, 3, 15), render_model_version=RENDER_MODEL_VERSION
            ),
            "pm": lambda: _make_pm_bulletin(
                region, date(2026, 3, 15), render_model_version=RENDER_MODEL_VERSION
            ),
        }
        for name in make:
            builders[name]()
        recompute_region_day(region, date(2026, 3, 15))
        rating = RegionDayRating.objects.get(region=region, date=date(2026, 3, 15))

        with _freeze("2026-03-15T18:00:00+00:00"):
            result = select_bulletin_for_date(region, date(2026, 3, 15))

        assert result == rating.source_bulletin


@pytest.mark.django_db
class TestResolveSelectedIssue:
    """The ``?issue=<uuid>`` override wins over the default when valid."""

    def test_uuid_override_selects_matching_issue(self, region: MicroRegion) -> None:
        """A recognised ``?issue`` UUID returns that specific issue."""
        prev_evening = _make_pm_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))
        issues = _issues_for_date(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _resolve_selected_issue(
                issues, date(2026, 3, 15), str(prev_evening.bulletin_id)
            )

        assert result is not None and result.pk == prev_evening.pk

    def test_unknown_uuid_falls_back_to_default(self, region: MicroRegion) -> None:
        """A bogus ``?issue`` value degrades silently to the default issue."""
        _make_pm_bulletin(region, date(2026, 3, 14))
        am = _make_am_bulletin(region, date(2026, 3, 15))
        issues = _issues_for_date(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _resolve_selected_issue(
                issues, date(2026, 3, 15), "not-a-real-uuid"
            )

        assert result is not None and result.pk == am.pk


@pytest.mark.django_db
class TestBulletinDetailIssueParam:
    """``?issue=<uuid>`` selects which issue renders on multi-issue days."""

    def _url(self, region: MicroRegion, date_str: str) -> str:
        return reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": region.canonical_region_id,
                "slug": region.name_slug,
                "date_str": date_str,
            },
        )

    def test_query_param_switches_rendered_issue(
        self, client: Client, region: MicroRegion
    ) -> None:
        """``?issue=<uuid>`` renders that specific issue (via X-Bulletin-Id)."""
        prev_evening = _make_pm_bulletin(region, date(2026, 3, 14))
        am = _make_am_bulletin(region, date(2026, 3, 15))

        # Default (no ?issue) → morning update.
        with _freeze("2026-03-20T12:00:00+00:00"):
            default_resp = client.get(self._url(region, "2026-03-15"))
        assert default_resp["X-Bulletin-Id"] == str(am.bulletin_id)

        # With ?issue override → previous evening.
        with _freeze("2026-03-20T12:00:00+00:00"):
            override_resp = client.get(
                self._url(region, "2026-03-15"),
                {"issue": str(prev_evening.bulletin_id)},
            )
        assert override_resp["X-Bulletin-Id"] == str(prev_evening.bulletin_id)

    def test_page_date_stays_on_url_even_for_same_day_evening_issue(
        self, client: Client, region: MicroRegion
    ) -> None:
        """
        Selecting the same-day evening issue (valid_to = D+1 17:00) must not
        bump the page header to D+1 — the URL is the source of truth for
        ``page_date``.
        """
        _make_am_bulletin(region, date(2026, 3, 15))
        same_evening = _make_pm_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            response = client.get(
                self._url(region, "2026-03-15"),
                {"issue": str(same_evening.bulletin_id)},
            )

        assert response.status_code == 200
        assert response.context["page_date"] == date(2026, 3, 15)


@pytest.mark.django_db
class TestMapTailLink:
    """SNOW-806: the tail is one link back to the map, focused on region and day.

    The adjoining-regions, resorts-in-region and favourites-here sections and
    the subscribe form are gone — map objects, not part of the document.
    """

    def test_today_page_links_to_the_map_at_this_region(
        self, client: Client, region: MicroRegion
    ) -> None:
        """On today's page the link is the bare map with the region fragment."""
        _make_am_bulletin(region, date(2026, 3, 15))
        with _freeze("2026-03-15T10:00:00+00:00"):
            response = client.get(
                reverse(
                    "public:bulletin", kwargs={"region_id": "ch-4115", "slug": "valais"}
                )
            )

        content = response.content.decode()
        assert 'data-testid="bulletin-map-link"' in content
        assert 'href="/map/#CH-4115"' in content
        assert "See this region on the map" in content

    def test_historic_page_carries_the_date(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A past day's page sends the map to that day."""
        _make_am_bulletin(region, date(2026, 3, 15))
        with _freeze("2026-03-20T10:00:00+00:00"):
            response = client.get(
                reverse(
                    "public:bulletin_date",
                    kwargs={
                        "region_id": "ch-4115",
                        "slug": "valais",
                        "date_str": "2026-03-15",
                    },
                )
            )

        assert 'href="/map/?d=2026-03-15#CH-4115"' in response.content.decode()

    def test_the_four_map_affordances_are_gone(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Neighbours, resorts, the reader's pins and the subscribe form no longer render."""
        neighbour = MicroRegionFactory.create(
            region_id="CH-9991", name="Zoulou", slug="zoulou"
        )
        region.neighbours.add(neighbour)
        ResortFactory.create(region=region, name="Verbier")
        user = UserFactory.create()
        FavouriteFactory.create(user=user, name="My spot", region=region)
        client.force_login(user)
        _make_am_bulletin(region, date(2026, 3, 15))
        with _freeze("2026-03-15T10:00:00+00:00"):
            content = client.get(
                reverse(
                    "public:bulletin", kwargs={"region_id": "ch-4115", "slug": "valais"}
                )
            ).content.decode()

        for testid in (
            "adjoining-regions",
            "resorts-in-region",
            "favourites-in-region",
        ):
            assert f'data-testid="{testid}"' not in content
        assert "subscribe-cta-" not in content
        assert "Zoulou" not in content
        assert "My spot" not in content
        assert (
            "adjoining_regions"
            not in client.get(
                reverse(
                    "public:bulletin", kwargs={"region_id": "ch-4115", "slug": "valais"}
                )
            ).context
        )


@pytest.mark.django_db
class TestSeasonCalendar:
    """Tests for the SNOW-83/SNOW-170 season heatmap on the bulletin page.

    The grid is now deferred via HTMX (SNOW-170): the bulletin page
    renders a shell with an HTMX placeholder; the actual grid markup is
    served by the season_calendar_partial view on first open.
    """

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_context_has_season_calendar(
        self, client: Client, region: MicroRegion
    ) -> None:
        """``season_calendar`` context is a truthy dict with season_label."""
        _make_am_bulletin(region, date(2026, 3, 15))
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        ctx = response.context["season_calendar"]
        assert ctx is not None
        assert ctx["season_label"]  # e.g. "25/26"

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_section_renders_shell_not_grid(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The bulletin page renders the sheet shell but not the grid markup."""
        _make_am_bulletin(region, date(2026, 3, 15))
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        # Shell present.
        assert b'data-testid="season-sheet"' in response.content
        # Grid deferred — not in the initial response.
        assert b'data-testid="season-calendar"' not in response.content

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_today_tile_carries_today_modifier(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Today's tile modifier is served by the partial, not the bulletin page (SNOW-170)."""
        _make_am_bulletin(region, date(2026, 3, 15))
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        # The grid is deferred — the bulletin page only contains the shell.
        # The calendar is served by the partial (see test_season_partial.py).
        assert b'id="season-grid"' in response.content
        assert b'data-testid="season-calendar"' not in response.content

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_historic_url_carries_selected_date_on_grid_placeholder(
        self, client: Client, region: MicroRegion
    ) -> None:
        """On a historic URL the #season-grid placeholder carries data-selected-date (SNOW-170)."""
        _make_am_bulletin(region, date(2026, 3, 5))
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-05",
                },
            )
            response = client.get(url)

        # The selected-date is encoded in the placeholder so the JS htmx:afterSwap
        # handler can ring the selected day client-side.
        assert b'data-selected-date="2026-03-05"' in response.content

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_tomorrow_row_renders_when_present(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A RegionDayRating row for today + 1 surfaces in the season partial (SNOW-170)."""
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=date(2026, 3, 16),
            min_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            source_bulletin=bulletin,
        )

        # The grid is now deferred — hit the partial endpoint directly.
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:season_partial",
                kwargs={"region_id": "ch-4115"},
            )
            response = client.get(url, HTTP_HX_REQUEST="true")

        # The link to tomorrow's bulletin includes the date in the URL.
        # SNOW-99: the calendar partial uses ``region.canonical_region_id``
        # and ``region.name_slug`` (slugified ``Valais`` → ``valais``).
        expected = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-16",
            },
        )
        assert expected.encode() in response.content
        assert b'data-level="considerable"' in response.content


@pytest.mark.django_db
class TestCanonicalUrl:
    """The form-3 canonical URL is rendered as a ``<link rel="canonical">``."""

    def test_canonical_url_in_full_render(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A normal render emits an absolute form-3 canonical URL."""
        _make_am_bulletin(region, date(2026, 3, 15))
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        canonical = response.context["canonical_url"]
        assert canonical.endswith("/ch-4115/valais/2026-03-15/")
        assert b'<link rel="canonical"' in response.content
        assert canonical.encode() in response.content

    def test_canonical_url_in_empty_state(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The empty-state render also emits a canonical URL."""
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"] is None
        canonical = response.context["canonical_url"]
        assert canonical.endswith("/ch-4115/valais/2026-03-15/")
        assert b'<link rel="canonical"' in response.content

    def test_off_canonical_inbound_slug_redirects_to_name_slug(
        self, client: Client
    ) -> None:
        """An off-canonical inbound slug 302s to the name-derived form."""
        # Create a region whose name slug ("valais") differs from the
        # auto-generated ``Region.slug`` (``ch-4115``); a request that
        # uses any non-canonical slug must redirect to the name slug.
        MicroRegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "wrong-slug",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.status_code == 302
        assert response["Location"] == "/ch-4115/valais/2026-03-15/"


@pytest.mark.django_db
class TestMeteoFranceMultiIssueDay:
    """A Météo-France massif-day with two issues resolves to the later one.

    Archive-loaded MF bulletins used to carry a synthetic midnight
    ``valid_from``, so both issues of a covered day sorted equally and the page
    rendered whichever the queryset happened to return first. Extraction now
    takes ``validTime.startTime`` from the bulletin's own "Rédigé le … à 16h"
    line, which is what makes the 10:00-rule work for MF (SNOW-559).

    The MF shape differs from the SLF one the other tests use: an evening issue
    is valid from ~15:00Z on D-1 through to end of day D, and a morning refresh
    from ~08:00Z on D through to the same end of day.
    """

    def _mf_issue(
        self, region: MicroRegion, published: datetime, covered: date
    ) -> Bulletin:
        """Create an MF-shaped bulletin.

        Args:
            region: Region to link the bulletin to.
            published: The issue instant (``valid_from``).
            covered: The day the bulletin forecasts.

        Returns:
            The created Bulletin.

        """
        bulletin = BulletinFactory.create(
            issued_at=published,
            valid_from=published,
            valid_to=datetime(
                covered.year, covered.month, covered.day, 23, 59, 59, tzinfo=UTC
            ),
        )
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time=region.name,
        )
        return bulletin

    def test_both_issues_are_discovered(self, region: MicroRegion) -> None:
        """Both issues overlap the covered day and are returned in order.

        Args:
            region: The region fixture.

        """
        evening = self._mf_issue(
            region, datetime(2026, 2, 12, 15, 0, tzinfo=UTC), date(2026, 2, 13)
        )
        morning = self._mf_issue(
            region, datetime(2026, 2, 13, 8, 0, tzinfo=UTC), date(2026, 2, 13)
        )

        issues = _issues_for_date(region, date(2026, 2, 13))

        assert [b.pk for b in issues] == [evening.pk, morning.pk]

    def test_morning_refresh_is_the_default(self, region: MicroRegion) -> None:
        """The 10:00 pivot picks the morning refresh over the prior evening.

        Both issues span 10:00 on the covered day, so the tie is broken by
        ``valid_from`` — which only orders them because the issue times are real.

        Args:
            region: The region fixture.

        """
        self._mf_issue(
            region, datetime(2026, 2, 12, 15, 0, tzinfo=UTC), date(2026, 2, 13)
        )
        morning = self._mf_issue(
            region, datetime(2026, 2, 13, 8, 0, tzinfo=UTC), date(2026, 2, 13)
        )

        selected = _select_default_issue(
            _issues_for_date(region, date(2026, 2, 13)), date(2026, 2, 13)
        )

        assert selected is not None
        assert selected.pk == morning.pk

    def test_evening_issue_alone_is_selected(self, region: MicroRegion) -> None:
        """A day with only the previous-evening issue still renders it.

        Args:
            region: The region fixture.

        """
        evening = self._mf_issue(
            region, datetime(2026, 2, 12, 15, 0, tzinfo=UTC), date(2026, 2, 13)
        )

        selected = _select_default_issue(
            _issues_for_date(region, date(2026, 2, 13)), date(2026, 2, 13)
        )

        assert selected is not None
        assert selected.pk == evening.pk

    def test_selection_is_deterministic_across_insertion_orders(
        self, region: MicroRegion
    ) -> None:
        """Creating the morning issue first must not change which one wins.

        Non-determinism here was the user-visible half of the collision bug:
        with equal ``valid_from`` values the winner depended on row order.

        Args:
            region: The region fixture.

        """
        morning = self._mf_issue(
            region, datetime(2026, 2, 13, 8, 0, tzinfo=UTC), date(2026, 2, 13)
        )
        self._mf_issue(
            region, datetime(2026, 2, 12, 15, 0, tzinfo=UTC), date(2026, 2, 13)
        )

        selected = _select_default_issue(
            _issues_for_date(region, date(2026, 2, 13)), date(2026, 2, 13)
        )

        assert selected is not None
        assert selected.pk == morning.pk
