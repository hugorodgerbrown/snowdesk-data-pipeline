"""
tests/public/test_bulletin_detail.py — Tests for day-based bulletin navigation.

Covers the bulletin_detail view and its helpers: _select_bulletin_for_date
and _get_nav_dates.  Bulletins follow the SLF pattern:

  * PM (evening) bulletin: valid_from ~15:00 day D, valid_to ~15:00 day D+1
  * AM (morning) bulletin: valid_from ~06:00 day D, valid_to ~15:00 day D

For past days the morning bulletin is preferred; for the current day the
bulletin whose validity window contains *now* is shown.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from public.views import _get_nav_dates, _select_bulletin_for_date
from tests.factories import BulletinFactory, RegionBulletinFactory, RegionFactory


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def region():
    """Return a test Region."""
    return RegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


def _make_pm_bulletin(region, day, **kwargs):
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


def _make_am_bulletin(region, day, **kwargs):
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


def _freeze(dt_str):
    """Return a patch that freezes django.utils.timezone.now to *dt_str*."""
    frozen = datetime.fromisoformat(dt_str)
    return patch("django.utils.timezone.now", return_value=frozen)


# ── _select_bulletin_for_date ────────────────────────────────────────────────


@pytest.mark.django_db
class TestSelectBulletinForDate:
    """Tests for the _select_bulletin_for_date helper."""

    def test_past_date_prefers_am_bulletin(self, region):
        """On a past date with both AM and PM bulletins, the AM is chosen."""
        day = date(2026, 3, 15)
        _make_pm_bulletin(region, date(2026, 3, 14))  # PM covers 3/15
        am = _make_am_bulletin(region, day)

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == am.pk

    def test_past_date_falls_back_to_pm_if_no_am(self, region):
        """On a past date with only a PM bulletin, that is returned."""
        day = date(2026, 3, 15)
        pm = _make_pm_bulletin(region, date(2026, 3, 14))  # PM covers 3/15

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == pm.pk

    def test_today_returns_currently_valid_am(self, region):
        """During today's AM window the AM bulletin is selected."""
        day = date(2026, 3, 15)
        _make_pm_bulletin(region, date(2026, 3, 14))  # PM covers until 15:00
        am = _make_am_bulletin(region, day)  # AM: 06:00 - 15:00

        with _freeze("2026-03-15T10:00:00+00:00"):
            result = _select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == am.pk

    def test_today_before_am_returns_pm(self, region):
        """Before the AM bulletin starts the PM bulletin is still valid."""
        day = date(2026, 3, 15)
        pm = _make_pm_bulletin(region, date(2026, 3, 14))  # valid until 15:00
        _make_am_bulletin(region, day)  # starts at 06:00

        with _freeze("2026-03-15T04:00:00+00:00"):
            result = _select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == pm.pk

    def test_no_bulletins_returns_none(self, region):
        """When no bulletins exist for a date, None is returned."""
        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is None


# ── _get_nav_dates ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestGetNavDates:
    """Tests for the _get_nav_dates helper."""

    def test_returns_prev_and_next(self, region):
        """When bulletins exist on adjacent dates, both are returned."""
        _make_am_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))
        _make_am_bulletin(region, date(2026, 3, 16))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, next_date = _get_nav_dates(region, date(2026, 3, 15))

        assert prev_date == date(2026, 3, 14)
        assert next_date == date(2026, 3, 16)

    def test_no_prev_at_earliest(self, region):
        """The earliest date has no prev_date."""
        _make_am_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, _ = _get_nav_dates(region, date(2026, 3, 14))

        assert prev_date is None

    def test_no_next_at_today(self, region):
        """The current date has no next_date."""
        _make_am_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            _, next_date = _get_nav_dates(region, date(2026, 3, 15))

        assert next_date is None

    def test_skips_gaps(self, region):
        """Navigation jumps over dates without bulletins."""
        _make_am_bulletin(region, date(2026, 3, 10))
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, _ = _get_nav_dates(region, date(2026, 3, 15))

        assert prev_date == date(2026, 3, 10)


# ── bulletin_detail view ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBulletinDetailView:
    """Integration tests for the bulletin_detail view."""

    def test_default_shows_today(self, client: Client, region):
        """Without a date param the view shows today's bulletin."""
        day = date(2026, 3, 15)
        am = _make_am_bulletin(region, day)

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin",
                kwargs={"region_id": "CH-4115", "slug": "valais"},
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"].pk == am.pk
        assert response.context["is_today"] is True

    def test_date_segment_selects_day(self, client: Client, region):
        """A date URL segment selects the requested day."""
        am_14 = _make_am_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "CH-4115",
                    "slug": "valais",
                    "date_str": "2026-03-14",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"].pk == am_14.pk
        assert response.context["is_today"] is False

    def test_invalid_date_falls_back_to_today(self, client: Client, region):
        """An invalid date segment falls back to today."""
        am = _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "CH-4115",
                    "slug": "valais",
                    "date_str": "not-a-date",
                },
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"].pk == am.pk

    def test_no_bulletin_shows_empty_state(self, client: Client, region):
        """When no bulletin exists for the date the empty state is rendered."""
        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin",
                kwargs={"region_id": "CH-4115", "slug": "valais"},
            )
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"] is None

    def test_nav_links_use_dates(self, client: Client, region):
        """Prev/next navigation passes dates, not bulletin IDs."""
        _make_am_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))
        _make_am_bulletin(region, date(2026, 3, 16))

        with _freeze("2026-03-20T12:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "CH-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
            response = client.get(url)

        assert response.context["prev_date"] == date(2026, 3, 14)
        assert response.context["next_date"] == date(2026, 3, 16)
        content = response.content.decode()
        assert "2026-03-14" in content
        assert "2026-03-16" in content

    def test_current_date_shown_in_nav(self, client: Client, region):
        """The current page date appears in the nav."""
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin",
                kwargs={"region_id": "CH-4115", "slug": "valais"},
            )
            response = client.get(url)

        content = response.content.decode()
        assert "Today" in content

    def test_past_date_shown_in_nav(self, client: Client, region):
        """A past page date appears as a formatted date."""
        _make_am_bulletin(region, date(2026, 3, 14))

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "CH-4115",
                    "slug": "valais",
                    "date_str": "2026-03-14",
                },
            )
            response = client.get(url)

        content = response.content.decode()
        assert "Sat 14 Mar" in content

    def test_next_update_shown_when_today_before_due(self, client: Client, region):
        """On today, before the next bulletin is due, its time is shown."""
        am = _make_am_bulletin(region, date(2026, 3, 15))
        # next_update is 15:00 UTC on the same day
        from pipeline.models import Bulletin

        Bulletin.objects.filter(pk=am.pk).update(
            next_update=datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
        )

        with _freeze("2026-03-15T10:00:00+00:00"):
            url = reverse(
                "public:bulletin",
                kwargs={"region_id": "CH-4115", "slug": "valais"},
            )
            response = client.get(url)

        assert response.context["next_update_time"] is not None
        content = response.content.decode()
        assert "15:00 UTC" in content

    def test_no_next_update_after_due_time(self, client: Client, region):
        """After the next_update time has passed, the disabled label is absent."""
        am = _make_am_bulletin(region, date(2026, 3, 15))
        from pipeline.models import Bulletin

        Bulletin.objects.filter(pk=am.pk).update(
            next_update=datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
        )

        with _freeze("2026-03-15T16:00:00+00:00"):
            url = reverse(
                "public:bulletin",
                kwargs={"region_id": "CH-4115", "slug": "valais"},
            )
            response = client.get(url)

        assert response.context["next_update_time"] is None

    def test_unknown_region_returns_404(self, client: Client):
        """A region ID that doesn't match any Region should 404."""
        url = reverse(
            "public:bulletin",
            kwargs={"region_id": "XX-9999", "slug": "nowhere"},
        )
        response = client.get(url)

        assert response.status_code == 404
