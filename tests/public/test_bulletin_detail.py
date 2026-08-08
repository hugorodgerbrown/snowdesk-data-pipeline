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
from django.test import Client, override_settings
from django.urls import reverse

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.public.views import (
    _get_nav_dates,
    _has_later_bulletin,
    _issues_for_date,
    _resolve_selected_issue,
    _select_bulletin_for_date,
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
    WeatherSnapshotFactory,
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
            result = _select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == am.pk

    def test_past_date_falls_back_to_pm_if_no_am(self, region: MicroRegion) -> None:
        """On a past date with only a PM bulletin, that is returned."""
        day = date(2026, 3, 15)
        pm = _make_pm_bulletin(region, date(2026, 3, 14))  # PM covers 3/15

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == pm.pk

    def test_today_returns_currently_valid_am(self, region: MicroRegion) -> None:
        """During today's AM window the AM bulletin is selected."""
        day = date(2026, 3, 15)
        _make_pm_bulletin(region, date(2026, 3, 14))  # PM covers until 15:00
        am = _make_am_bulletin(region, day)  # AM: 06:00 - 15:00

        with _freeze("2026-03-15T10:00:00+00:00"):
            result = _select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == am.pk

    def test_today_before_am_returns_pm(self, region: MicroRegion) -> None:
        """Before the AM bulletin starts the PM bulletin is still valid."""
        day = date(2026, 3, 15)
        pm = _make_pm_bulletin(region, date(2026, 3, 14))  # valid until 15:00
        _make_am_bulletin(region, day)  # starts at 06:00

        with _freeze("2026-03-15T04:00:00+00:00"):
            result = _select_bulletin_for_date(region, day)

        assert result is not None
        assert result.pk == pm.pk

    def test_no_bulletins_returns_none(self, region: MicroRegion) -> None:
        """When no bulletins exist for a date, None is returned."""
        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is None


# ── _get_nav_dates ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestGetNavDates:
    """Tests for the _get_nav_dates helper."""

    def test_returns_prev_and_next(self, region: MicroRegion) -> None:
        """Prev and next are calendar adjacents regardless of bulletin gaps."""
        # Anchor the lower bound by creating a bulletin whose valid_to day
        # is 2026-03-14, so that current_date 2026-03-15 > oldest_date and
        # prev_date is populated.
        _make_am_bulletin(region, date(2026, 3, 14))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, next_date = _get_nav_dates(region, date(2026, 3, 15))

        assert prev_date == date(2026, 3, 14)
        assert next_date == date(2026, 3, 16)

    def test_no_prev_at_earliest(self, region: MicroRegion) -> None:
        """The oldest bulletin's date has no prev_date."""
        _make_am_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, _ = _get_nav_dates(region, date(2026, 3, 14))

        assert prev_date is None

    def test_next_is_tomorrow_when_today(self, region: MicroRegion) -> None:
        """On today, next_date is tomorrow (the adjacent calendar day)."""
        _make_am_bulletin(region, date(2026, 3, 14))
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T10:00:00+00:00"):
            _, next_date = _get_nav_dates(region, date(2026, 3, 15))

        assert next_date == date(2026, 3, 16)

    def test_does_not_skip_gaps(self, region: MicroRegion) -> None:
        """Navigation steps one calendar day at a time, not to the next bulletin."""
        _make_am_bulletin(region, date(2026, 3, 10))
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, _ = _get_nav_dates(region, date(2026, 3, 15))

        # prev is current-1, not the older bulletin date
        assert prev_date == date(2026, 3, 14)

    def test_no_next_at_tomorrow(self, region: MicroRegion) -> None:
        """When current_date is tomorrow, next_date is None (upper bound)."""
        _make_am_bulletin(region, date(2026, 3, 14))

        with _freeze("2026-03-15T10:00:00+00:00"):
            # Tomorrow relative to the frozen today (2026-03-15) is 2026-03-16.
            _, next_date = _get_nav_dates(region, date(2026, 3, 16))

        assert next_date is None

    def test_no_prev_when_region_has_no_bulletins(self, region: MicroRegion) -> None:
        """When a region has no bulletins, prev_date is None."""
        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, _ = _get_nav_dates(region, date(2026, 3, 15))

        assert prev_date is None

    def test_no_prev_at_oldest_bulletin_date(self, region: MicroRegion) -> None:
        """When current_date equals the oldest bulletin date, prev_date is None."""
        # Only one bulletin; its valid_to day is 2026-03-14 (AM bulletin).
        _make_am_bulletin(region, date(2026, 3, 14))

        with _freeze("2026-03-20T12:00:00+00:00"):
            prev_date, _ = _get_nav_dates(region, date(2026, 3, 14))

        assert prev_date is None


# ── _has_later_bulletin ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestHasLaterBulletin:
    """Tests for the _has_later_bulletin helper."""

    def test_returns_true_when_later_bulletin_exists(self, region: MicroRegion) -> None:
        """Returns True when a bulletin exists with valid_to after page_date."""
        _make_am_bulletin(region, date(2026, 3, 15))  # valid_to: 15:00 on 3/15
        _make_am_bulletin(region, date(2026, 3, 16))  # valid_to: 15:00 on 3/16

        assert _has_later_bulletin(region, date(2026, 3, 15)) is True

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
        # A bulletin on 2026-03-14 anchors the lower bound so both prev and
        # next are populated when viewing 2026-03-15 (no bulletin on that day).
        _make_am_bulletin(region, date(2026, 3, 14))

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

    def test_stale_render_model_triggers_warning_and_rebuilds(
        self, client: Client, region: MicroRegion, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A bulletin at a lower render_model_version triggers a warning and rebuilds."""
        # Create a bulletin whose stored render_model_version is 1.
        am = _make_am_bulletin(region, date(2026, 3, 15), render_model_version=1)
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
        )

        # Patch RENDER_MODEL_VERSION in the view module to 2 so version 1 appears stale.
        with patch("apps.public.views.RENDER_MODEL_VERSION", 2):
            with _freeze("2026-03-15T10:00:00+00:00"):
                with caplog.at_level("WARNING", logger="apps.public.views"):
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
        self, client: Client, region: MicroRegion, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When stale rebuild raises RenderModelBuildError, page returns 200 with error card."""
        from apps.bulletins.services.render_model import RenderModelBuildError

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
                "apps.public.views.build_render_model",
                side_effect=RenderModelBuildError("validation failed"),
            ):
                with _freeze("2026-03-15T10:00:00+00:00"):
                    with caplog.at_level("ERROR", logger="apps.public.views"):
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


@pytest.mark.django_db
class TestIssuesForDate:
    """All three SLF-style issues covering a calendar day are returned."""

    def test_returns_all_three_overlapping_issues(self, region: MicroRegion) -> None:
        """Previous evening + morning + same-day evening all overlap day D."""
        prev_evening = _make_pm_bulletin(region, date(2026, 3, 14))
        am = _make_am_bulletin(region, date(2026, 3, 15))
        same_evening = _make_pm_bulletin(region, date(2026, 3, 15))

        issues = _issues_for_date(region, date(2026, 3, 15))

        ids = [b.pk for b in issues]
        assert ids == [prev_evening.pk, am.pk, same_evening.pk], (
            "issues must be returned in chronological (valid_from) order "
            f"for the tab strip; got {ids}"
        )

    def test_empty_when_no_bulletins_touch_day(self, region: MicroRegion) -> None:
        """Days with no valid bulletins return an empty list."""
        _make_am_bulletin(region, date(2026, 3, 10))
        assert _issues_for_date(region, date(2026, 3, 15)) == []


@pytest.mark.django_db
class TestDefaultIssueSelection:
    """The default issue honours the 10:00-rule for past days and *now* for today."""

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
            result = _select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is not None and result.pk == am.pk

    def test_past_day_falls_back_to_previous_evening_when_no_morning(
        self, region: MicroRegion
    ) -> None:
        """Without a morning update, the previous-day evening covers 10:00."""
        prev_evening = _make_pm_bulletin(region, date(2026, 3, 14))
        # No AM today.

        with _freeze("2026-03-20T12:00:00+00:00"):
            result = _select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is not None and result.pk == prev_evening.pk

    def test_today_prefers_window_containing_now(self, region: MicroRegion) -> None:
        """For today, the pivot is *now* — not the synthetic 10:00 value."""
        _make_am_bulletin(region, date(2026, 3, 15))  # AM: 06:00–15:00
        same_evening = _make_pm_bulletin(region, date(2026, 3, 15))  # 17:00+

        # 18:00 is inside the same-day evening window and outside AM's.
        with _freeze("2026-03-15T18:00:00+00:00"):
            result = _select_bulletin_for_date(region, date(2026, 3, 15))

        assert result is not None and result.pk == same_evening.pk


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
class TestAdjoiningRegions:
    """Tests for the adjoining-regions context entry and rendered section."""

    def test_context_lists_neighbours_in_alphabetical_order(
        self, client: Client, region: MicroRegion
    ) -> None:
        """``adjoining_regions`` is sorted by name regardless of insertion order."""
        zoulou = MicroRegionFactory.create(
            region_id="CH-9991", name="Zoulou", slug="zoulou"
        )
        alpha = MicroRegionFactory.create(
            region_id="CH-9992", name="Alpha", slug="alpha"
        )
        mike = MicroRegionFactory.create(region_id="CH-9993", name="Mike", slug="mike")
        # Insert in non-alphabetical order to prove the view sorts.
        region.neighbours.set([zoulou, mike, alpha])

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

        names = [r.name for r in response.context["adjoining_regions"]]
        assert names == ["Alpha", "Mike", "Zoulou"]

    def test_section_renders_with_links_to_each_neighbour(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The Adjoining Regions section emits a link per neighbour."""
        neighbour = MicroRegionFactory.create(
            region_id="CH-9994", name="Bordering", slug="bordering"
        )
        region.neighbours.add(neighbour)

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
        # The adjoining-region link points at the canonical form-3 URL
        # using the same page_date as the rendered page (SNOW-99) so the
        # neighbour link preserves the date the user is browsing.
        expected_url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-9994",
                "slug": "bordering",
                "date_str": "2026-03-15",
            },
        )
        assert 'data-testid="adjoining-regions"' in content
        assert "Bordering" in content
        assert expected_url in content

    def test_section_hidden_when_no_neighbours(
        self, client: Client, region: MicroRegion
    ) -> None:
        """No neighbours seeded → no adjoining-regions section in the HTML."""
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

        assert response.context["adjoining_regions"] == []
        assert b'data-testid="adjoining-regions"' not in response.content

    def test_empty_state_includes_adjoining_regions(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Even when there is no bulletin for the date, neighbours still render."""
        neighbour = MicroRegionFactory.create(
            region_id="CH-9995", name="Border", slug="border"
        )
        region.neighbours.add(neighbour)

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

        assert response.context["bulletin"] is None
        assert list(response.context["adjoining_regions"]) == [neighbour]
        assert b'data-testid="adjoining-regions"' in response.content


@pytest.mark.django_db
class TestResortsInRegion:
    """Tests for the "Resorts in this region" context entry and section (SNOW-504)."""

    def test_context_lists_resorts_in_the_region(
        self, client: Client, region: MicroRegion
    ) -> None:
        """``resorts_in_region`` lists the region's resorts, alphabetically."""
        ResortFactory.create(name="Zermatt", region=region)
        ResortFactory.create(name="Arosa", region=region)

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

        names = [r.name for r in response.context["resorts_in_region"]]
        assert names == ["Arosa", "Zermatt"]

    def test_section_renders_with_links_to_each_resort(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The section emits a link to each resort's own page."""
        resort = ResortFactory.create(name="Verbier", region=region)

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
        assert 'data-testid="resorts-in-region"' in content
        assert "Verbier" in content
        assert resort.get_absolute_url() in content

    def test_section_hidden_when_no_resorts(
        self, client: Client, region: MicroRegion
    ) -> None:
        """No resorts seeded in the region → no section in the HTML."""
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

        assert response.context["resorts_in_region"] == []
        assert b'data-testid="resorts-in-region"' not in response.content

    def test_empty_state_includes_resorts_in_region(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Even when there is no bulletin for the date, resorts still render."""
        resort = ResortFactory.create(name="Verbier", region=region)

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

        assert response.context["bulletin"] is None
        assert list(response.context["resorts_in_region"]) == [resort]
        assert b'data-testid="resorts-in-region"' in response.content


@pytest.mark.django_db
class TestFavouritesInRegion:
    """Tests for the "Your favourites here" context entry and section (SNOW-507)."""

    def test_section_shows_for_signed_in_user_with_favourite_in_region(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A signed-in user's favourite in this region renders the section + link."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="My spot", region=region)

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
        assert 'data-testid="favourites-in-region"' in content
        assert "My spot" in content
        assert reverse("favourites:detail", args=[favourite.uuid]) in content

    def test_section_hidden_for_anonymous(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The section never renders for an anonymous request."""
        FavouriteFactory.create(region=region)

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

        assert response.context["favourites_in_region"] == []
        assert b'data-testid="favourites-in-region"' not in response.content

    def test_section_hidden_when_users_favourites_are_all_in_other_regions(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A favourite in a different region does not surface here."""
        user = UserFactory.create()
        client.force_login(user)
        other_region = MicroRegionFactory.create()
        FavouriteFactory.create(user=user, region=other_region)

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

        assert response.context["favourites_in_region"] == []
        assert b'data-testid="favourites-in-region"' not in response.content


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
        # calendar-cell-today is served by the partial (see test_season_partial.py).
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
        # handler can apply calendar-cell-selected client-side.
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
        assert b'data-rating-max="considerable"' in response.content


# ── Weather header (SNOW-98) ───────────────────────────────────────────────


@pytest.mark.django_db
class TestWeatherHeader:
    """Tests for the WeatherSnapshot → context plumbing on bulletin_detail.

    Verifies that weather context is correctly computed and passed to the
    ``bulletin_header.html`` partial across all meaningful snapshot states.
    """

    def _bulletin_url(self) -> str:
        """Return the today-bulletin URL used by every test below.

        SNOW-99 rewires form 2 (``/<region>/<slug>/``) as a redirect, so
        these tests now hit the canonical form-3 URL directly with the
        date the freeze fixture pegs ``today`` to.
        """
        return reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
        )

    def test_no_snapshot_yields_none_in_context(
        self, client: Client, region: MicroRegion
    ) -> None:
        """When no WeatherSnapshot exists, ``weather_display`` is None.

        The unified header partial (SNOW-100) still renders the panel chrome
        in the no-data path so the rest of the page chrome stays consistent —
        ``data-weather-bucket="none"`` falls back to a neutral dark token, the
        hero icon is omitted, and the metadata strip drops the weather lines.
        Assert that shape rather than the partial vanishing.
        """
        _make_am_bulletin(region, date(2026, 3, 15))
        with _freeze("2026-03-15T10:00:00+00:00"):
            response = client.get(self._bulletin_url())

        assert response.status_code == 200
        assert response.context["weather_display"] is None
        # Panel renders, but in the degraded ``bucket=none`` mode without a
        # hero icon — the visual cue that no snapshot was available.
        assert b'data-testid="bulletin-header"' in response.content
        assert b'data-weather-bucket="none"' in response.content
        assert b'data-testid="bulletin-header-hero-icon"' not in response.content

    def test_daytime_snapshot_emits_day_attributes(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A clear-sky daytime snapshot maps to bucket=clear, time=day."""
        _make_am_bulletin(region, date(2026, 3, 15))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 15),
            weather_code=0,  # clear sky
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
        )

        with _freeze("2026-03-15T12:00:00+00:00"):
            response = client.get(self._bulletin_url())

        assert response.status_code == 200
        display = response.context["weather_display"]
        assert display is not None
        assert display["bucket"] == "clear"
        assert display["time_of_day"] == "day"
        # The partial renders the data-attributes the design CSS targets.
        assert b'data-weather-bucket="clear"' in response.content
        assert b'data-time-of-day="day"' in response.content
        # Icon affordance (SNOW-100): icon file and condition label in HTML.
        assert display["icon_bucket"] == "clear"
        assert display["condition_label"] == "Clear"
        assert display["icon_filename"] == "clear-day.svg"
        assert b"icons/weather/clear-day.svg" in response.content
        assert b">Clear<" in response.content

    def test_nighttime_snapshot_emits_night_attributes(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A snowing snapshot read after sunset maps to bucket=snow, time=night."""
        _make_am_bulletin(region, date(2026, 3, 15))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 15),
            weather_code=71,  # light snowfall — maps to light_snow icon bucket
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
        )

        with _freeze("2026-03-15T22:00:00+00:00"):
            response = client.get(self._bulletin_url())

        assert response.status_code == 200
        display = response.context["weather_display"]
        assert display is not None
        assert display["bucket"] == "snow"
        assert display["time_of_day"] == "night"
        assert b'data-weather-bucket="snow"' in response.content
        assert b'data-time-of-day="night"' in response.content
        # Icon affordance (SNOW-100).
        assert display["icon_bucket"] == "light_snow"
        assert display["condition_label"] == "Light snow"
        assert display["icon_filename"] == "light_snow-night.svg"
        assert b"icons/weather/light_snow-night.svg" in response.content
        assert b">Light snow<" in response.content

    def test_cloudy_emits_no_day_night_suffix(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Overcast (WMO 3) uses cloudy.svg with no day/night suffix (SNOW-100).

        This guards the special-case logic: 'cloudy' is the only icon bucket
        whose SVG does not vary by time of day.
        """
        _make_am_bulletin(region, date(2026, 3, 15))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 15),
            weather_code=3,  # overcast → cloudy bucket
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
        )

        with _freeze("2026-03-15T00:30:00+00:00"):
            response = client.get(self._bulletin_url())

        assert response.status_code == 200
        display = response.context["weather_display"]
        assert display is not None
        assert display["icon_bucket"] == "cloudy"
        assert display["icon_filename"] == "cloudy.svg"
        assert b"icons/weather/cloudy.svg" in response.content
        assert b"cloudy-day.svg" not in response.content
        assert b"cloudy-night.svg" not in response.content

    def test_historical_date_with_daytime_clock_renders_as_day(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Browsing a past date at 11:09 wall-clock still renders as day.

        Regression guard: an earlier implementation compared full instants,
        which always landed past every historical sunset and forced every
        past page into the night theme.
        """
        _make_am_bulletin(region, date(2026, 3, 14))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 14),
            weather_code=0,
            sunrise=datetime(2026, 3, 14, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 14, 18, 0, tzinfo=UTC),
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-14",
            },
        )
        with _freeze("2026-05-01T11:09:00+00:00"):
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["weather_display"]["time_of_day"] == "day"

    def test_historical_date_with_evening_clock_renders_as_night(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Browsing a past date at 23:09 wall-clock renders as night."""
        _make_am_bulletin(region, date(2026, 3, 14))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 14),
            weather_code=0,
            sunrise=datetime(2026, 3, 14, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 14, 18, 0, tzinfo=UTC),
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-14",
            },
        )
        with _freeze("2026-05-01T23:09:00+00:00"):
            response = client.get(url)

        assert response.status_code == 200
        assert response.context["weather_display"]["time_of_day"] == "night"

    def test_empty_state_still_includes_weather_display(
        self, client: Client, region: MicroRegion
    ) -> None:
        """No bulletin but a snapshot exists → header still renders."""
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 15),
            weather_code=3,  # overcast
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
        )

        with _freeze("2026-03-15T12:00:00+00:00"):
            response = client.get(self._bulletin_url())

        assert response.status_code == 200
        assert response.context["bulletin"] is None
        display = response.context["weather_display"]
        assert display is not None
        assert display["bucket"] == "cloudy"

    def test_snapshot_for_other_region_does_not_leak(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A snapshot for a different region must not surface on this page."""
        other = MicroRegionFactory.create(
            region_id="CH-9999", name="Other", slug="other"
        )
        WeatherSnapshotFactory.create(
            region=other,
            valid_for_date=date(2026, 3, 15),
            weather_code=0,
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
        )
        _make_am_bulletin(region, date(2026, 3, 15))

        with _freeze("2026-03-15T12:00:00+00:00"):
            response = client.get(self._bulletin_url())

        assert response.status_code == 200
        assert response.context["weather_display"] is None


# ── Weather panel — daily temperature/snowfall (SNOW-571) ──────────────────


@pytest.mark.django_db
class TestWeatherPanelDailyExtras:
    """The masthead's meta strip renders temp/snowfall from the snapshot."""

    def _bulletin_url(self) -> str:
        """Return the form-3 URL for the test region on 2026-03-15."""
        return reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
        )

    def test_renders_temp_and_snowfall_when_populated(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A fully-populated snapshot renders both the temp and snowfall groups."""
        _make_am_bulletin(region, date(2026, 3, 15))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 15),
            weather_code=0,
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
            temperature_2m_max=4.2,
            temperature_2m_min=-3.1,
            snowfall_sum=12.0,
        )

        with _freeze("2026-03-15T12:00:00+00:00"):
            response = client.get(self._bulletin_url())

        content = response.content.decode()
        assert "4&deg;" in content
        assert "-3&deg;" in content
        assert "12 cm" in content

    def test_renders_explicit_zero_snowfall(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A 0 cm snowfall total still renders — 'no new snow' is a statement."""
        _make_am_bulletin(region, date(2026, 3, 15))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 15),
            weather_code=0,
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
            snowfall_sum=0.0,
        )

        with _freeze("2026-03-15T12:00:00+00:00"):
            response = client.get(self._bulletin_url())

        assert "0 cm" in response.content.decode()

    def test_omits_temp_and_snowfall_when_absent(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A sparse snapshot (fields unset) omits both groups individually."""
        _make_am_bulletin(region, date(2026, 3, 15))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 15),
            weather_code=0,
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
        )

        with _freeze("2026-03-15T12:00:00+00:00"):
            response = client.get(self._bulletin_url())

        content = response.content.decode()
        assert 'sr-only">Temperature<' not in content
        assert 'sr-only">Snowfall<' not in content

    def test_temp_renders_without_snowfall_when_snowfall_is_null(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Temperature renders on its own when snowfall_sum is NULL — omit individually."""
        _make_am_bulletin(region, date(2026, 3, 15))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 15),
            weather_code=0,
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
            temperature_2m_max=4.2,
            temperature_2m_min=-3.1,
        )

        with _freeze("2026-03-15T12:00:00+00:00"):
            response = client.get(self._bulletin_url())

        content = response.content.decode()
        assert "4&deg;" in content
        assert 'sr-only">Snowfall<' not in content


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
