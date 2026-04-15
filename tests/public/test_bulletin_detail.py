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

from public.views import (
    _build_issue_tabs,
    _get_nav_dates,
    _issues_for_date,
    _resolve_selected_issue,
    _select_bulletin_for_date,
)
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

    def test_stale_render_model_triggers_warning_and_rebuilds(
        self, client: Client, region, caplog
    ):
        """A bulletin at a lower render_model_version triggers a warning and rebuilds."""
        # Create a bulletin whose stored render_model_version is 1.
        am = _make_am_bulletin(region, date(2026, 3, 15), render_model_version=1)
        url = reverse(
            "public:bulletin",
            kwargs={"region_id": "CH-4115", "slug": "valais"},
        )

        # Patch RENDER_MODEL_VERSION in the view module to 2 so version 1 appears stale.
        with patch("public.views.RENDER_MODEL_VERSION", 2):
            with _freeze("2026-03-15T10:00:00+00:00"):
                with caplog.at_level("WARNING", logger="public.views"):
                    response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"].pk == am.pk
        assert any(
            "stale render_model" in record.message
            and "stored version=1" in record.message
            and "current=2" in record.message
            for record in caplog.records
        )

    def test_stale_render_model_rebuild_failure_returns_200_with_error_state(
        self, client: Client, region, caplog
    ):
        """When stale rebuild raises RenderModelBuildError, page returns 200 with error card."""
        from pipeline.services.render_model import RenderModelBuildError

        am = _make_am_bulletin(region, date(2026, 3, 15), render_model_version=1)
        url = reverse(
            "public:bulletin",
            kwargs={"region_id": "CH-4115", "slug": "valais"},
        )

        with patch("public.views.RENDER_MODEL_VERSION", 2):
            with patch(
                "public.views.build_render_model",
                side_effect=RenderModelBuildError("validation failed"),
            ):
                with _freeze("2026-03-15T10:00:00+00:00"):
                    with caplog.at_level("ERROR", logger="public.views"):
                        response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"].pk == am.pk
        # The panel render model should have version=0 (error state).
        panel = response.context.get("panel")
        assert panel is not None
        assert panel["render_model"]["version"] == 0
        # An ERROR log entry should have been emitted.
        assert any(
            "render model rebuild failed" in record.message.lower()
            for record in caplog.records
            if record.levelname == "ERROR"
        )


# ── Issue discovery and selection ────────────────────────────────────────────


def _make_evening_bulletin(region, day, **kwargs):
    """Create a D-evening issue valid ``D 17:00 → D+1 17:00``."""
    vf = datetime(day.year, day.month, day.day, 17, 0, tzinfo=UTC)
    vt = vf + timedelta(hours=24)
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        **kwargs,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin, region=region, region_name_at_time=region.name
    )
    return bulletin


@pytest.mark.django_db
class TestIssuesForDate:
    """All three SLF-style issues covering a calendar day are returned."""

    def test_returns_all_three_overlapping_issues(self, region):
        """Previous evening + morning + same-day evening all overlap day D."""
        prev_evening = _make_pm_bulletin(region, date(2026, 3, 14))
        am = _make_am_bulletin(region, date(2026, 3, 15))
        same_evening = _make_evening_bulletin(region, date(2026, 3, 15))

        issues = _issues_for_date(region, date(2026, 3, 15))

        ids = [b.pk for b in issues]
        assert ids == [prev_evening.pk, am.pk, same_evening.pk], (
            "issues must be returned in chronological (valid_from) order "
            f"for the tab strip; got {ids}"
        )

    def test_empty_when_no_bulletins_touch_day(self, region):
        """Days with no valid bulletins return an empty list."""
        _make_am_bulletin(region, date(2026, 3, 10))
        assert _issues_for_date(region, date(2026, 3, 15)) == []


@pytest.mark.django_db
class TestDefaultIssueSelection:
    """The default issue honours the 10:00-rule for past days and *now* for today."""

    def test_past_day_prefers_morning_update_over_previous_evening(self, region):
        """
        At the 10:00 pivot both the morning update AND the previous-day
        evening are valid — the morning update wins because it is the
        latest-issued refresh.
        """
        _make_pm_bulletin(
            region, date(2026, 3, 14)
        )  # prev evening → valid to 3/15 15:00
        am = _make_am_bulletin(region, date(2026, 3, 15))
        _make_evening_bulletin(region, date(2026, 3, 15))  # irrelevant (after 10:00)

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is not None and result.pk == am.pk

    def test_past_day_falls_back_to_previous_evening_when_no_morning(self, region):
        """Without a morning update, the previous-day evening covers 10:00."""
        prev_evening = _make_pm_bulletin(region, date(2026, 3, 14))
        # No AM today.

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is not None and result.pk == prev_evening.pk

    def test_today_prefers_window_containing_now(self, region):
        """For today, the pivot is *now* — not the synthetic 10:00 value."""
        _make_am_bulletin(region, date(2026, 3, 15))  # AM: 06:00–15:00
        same_evening = _make_evening_bulletin(region, date(2026, 3, 15))  # 17:00+

        # 18:00 is inside the same-day evening window and outside AM's.
        with _freeze("2026-03-15T18:00:00+00:00"):
            result = _select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is not None and result.pk == same_evening.pk


@pytest.mark.django_db
class TestResolveSelectedIssue:
    """The ``?issue=<uuid>`` override wins over the default when valid."""

    def test_uuid_override_selects_matching_issue(self, region):
        """A recognised ``?issue`` UUID returns that specific issue."""
        prev_evening = _make_pm_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))
        issues = _issues_for_date(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _resolve_selected_issue(
                issues, date(2026, 3, 15), str(prev_evening.bulletin_id)
            )

        assert result is not None and result.pk == prev_evening.pk

    def test_unknown_uuid_falls_back_to_default(self, region):
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
class TestIssueTabLabels:
    """Issue tabs carry chronological labels and the correct ``is_active`` flag."""

    def test_tab_labels_and_active_flag(self, region):
        """Each tab's role + label reflect the issue's valid_from."""
        _make_pm_bulletin(region, date(2026, 3, 14))  # 3/14 15:00
        am = _make_am_bulletin(region, date(2026, 3, 15))  # 3/15 06:00
        _make_evening_bulletin(region, date(2026, 3, 15))  # 3/15 17:00
        issues = _issues_for_date(region, date(2026, 3, 15))

        tabs = _build_issue_tabs(issues, am, date(2026, 3, 15))

        assert [t["role"] for t in tabs] == [
            "previous evening",
            "morning",
            "evening",
        ]
        # Morning update is the selected tab.
        assert [t["is_active"] for t in tabs] == [False, True, False]
        # Short labels include day-of-month and HH:MM.
        assert tabs[0]["short_label"] == "14 Mar 15:00"
        assert tabs[1]["short_label"] == "15 Mar 06:00"
        assert tabs[2]["short_label"] == "15 Mar 17:00"


@pytest.mark.django_db
class TestBulletinDetailIssueTabs:
    """The bulletin page renders an issue-tab strip when >1 issue touches the day."""

    def _url(self, region, date_str):
        return reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": region.region_id,
                "slug": region.slug,
                "date_str": date_str,
            },
        )

    def test_tabs_rendered_when_multiple_issues(self, client: Client, region):
        """With three issues touching a day, three tabs render."""
        _make_pm_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))
        _make_evening_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            response = client.get(self._url(region, "2026-03-15"))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="issue-tabs"' in content
        assert content.count('data-testid="issue-tab"') == 3

    def test_tabs_hidden_when_only_one_issue(self, client: Client, region):
        """A single issue day renders no tab strip (nothing to switch between)."""
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            response = client.get(self._url(region, "2026-03-15"))

        content = response.content.decode()
        assert 'data-testid="issue-tabs"' not in content

    def test_query_param_switches_rendered_issue(self, client: Client, region):
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
        self, client: Client, region
    ):
        """
        Selecting the same-day evening issue (valid_to = D+1 17:00) must not
        bump the page header to D+1 — the URL is the source of truth for
        ``page_date``.
        """
        _make_am_bulletin(region, date(2026, 3, 15))
        same_evening = _make_evening_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            response = client.get(
                self._url(region, "2026-03-15"),
                {"issue": str(same_evening.bulletin_id)},
            )

        assert response.status_code == 200
        assert response.context["page_date"] == date(2026, 3, 15)
