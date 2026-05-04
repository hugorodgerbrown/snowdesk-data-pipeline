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
from django.test import Client, override_settings
from django.urls import reverse
from waffle.testutils import override_flag

from bulletins.models import RegionDayRating
from public.views import (
    _get_nav_dates,
    _issues_for_date,
    _resolve_selected_issue,
    _select_bulletin_for_date,
)
from tests.factories import (
    BulletinFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    RegionFactory,
    WeatherSnapshotFactory,
)


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

    def test_date_segment_selects_day(self, client: Client, region):
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

    def test_invalid_date_redirects_to_canonical_today(self, client: Client, region):
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

    def test_no_bulletin_shows_empty_state(self, client: Client, region):
        """When no bulletin exists for the date the empty state is rendered."""
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

    def test_prev_next_dates_in_context(self, client: Client, region):
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

    def test_today_label_in_page_title(self, client: Client, region):
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

    def test_past_date_shown_in_eyebrow(self, client: Client, region):
        """A past page date appears in the masthead eyebrow."""
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
        # The masthead eyebrow uses the ``D j M Y`` format.
        assert "Sat 14 Mar 2026" in content

    def test_next_update_context_populated_today_before_due(
        self, client: Client, region
    ):
        """On today, before the next bulletin is due, ``next_update_time`` is set."""
        # Context is still populated so a future chrome element (e.g. a
        # ``next: HH:MM`` tooltip on the disabled `»` chip) can opt in.
        # The current layout does not surface the value in the DOM.
        am = _make_am_bulletin(region, date(2026, 3, 15))
        from bulletins.models import Bulletin

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

    def test_no_next_update_after_due_time(self, client: Client, region):
        """After the next_update time has passed, the disabled label is absent."""
        am = _make_am_bulletin(region, date(2026, 3, 15))
        from bulletins.models import Bulletin

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

    def test_unknown_region_returns_404(self, client: Client):
        """A region ID that doesn't match any Region should 404."""
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "XX-9999",
                "slug": "nowhere",
                "date_str": "2026-03-15",
            },
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
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
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
        from bulletins.services.render_model import RenderModelBuildError

        am = _make_am_bulletin(region, date(2026, 3, 15), render_model_version=1)
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
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


@pytest.mark.django_db
class TestIssuesForDate:
    """All three SLF-style issues covering a calendar day are returned."""

    def test_returns_all_three_overlapping_issues(self, region):
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
        _make_pm_bulletin(region, date(2026, 3, 15))  # irrelevant (after 10:00)

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
        same_evening = _make_pm_bulletin(region, date(2026, 3, 15))  # 17:00+

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
class TestBulletinDetailIssueParam:
    """``?issue=<uuid>`` selects which issue renders on multi-issue days."""

    def _url(self, region, date_str):
        return reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": region.canonical_region_id,
                "slug": region.name_slug,
                "date_str": date_str,
            },
        )

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
        self, client: Client, region
    ) -> None:
        """``adjoining_regions`` is sorted by name regardless of insertion order."""
        zoulou = RegionFactory.create(region_id="CH-9991", name="Zoulou", slug="zoulou")
        alpha = RegionFactory.create(region_id="CH-9992", name="Alpha", slug="alpha")
        mike = RegionFactory.create(region_id="CH-9993", name="Mike", slug="mike")
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
        self, client: Client, region
    ) -> None:
        """The Adjoining Regions section emits a link per neighbour."""
        neighbour = RegionFactory.create(
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

    def test_section_hidden_when_no_neighbours(self, client: Client, region) -> None:
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
        self, client: Client, region
    ) -> None:
        """Even when there is no bulletin for the date, neighbours still render."""
        neighbour = RegionFactory.create(
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
class TestSeasonCalendar:
    """Tests for the SNOW-83 season heatmap rendered on the bulletin page."""

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_context_has_season_calendar(self, client: Client, region) -> None:
        """``season_calendar`` is in the response context on a normal page."""
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

        assert response.context["season_calendar"] is not None
        assert response.context["season_calendar"].columns

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_section_renders_with_testid(self, client: Client, region) -> None:
        """The rendered HTML contains the season-calendar test id."""
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

        assert b'data-testid="season-calendar"' in response.content

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_today_tile_carries_today_ring(self, client: Client, region) -> None:
        """Today's tile has the ring-text-1 today highlight class."""
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

        # The today highlight class is unique to today's tile.
        assert b"ring-2 ring-offset-2 ring-text-1" in response.content

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_historic_url_marks_page_date_tile_selected(
        self, client: Client, region
    ) -> None:
        """On a historic URL the page_date tile carries the ring-ring-selected class."""
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

        # Both rings should be present: today (today's column) and selected
        # (the page_date column).
        assert b"ring-text-1" in response.content
        assert b"ring-ring-selected" in response.content

    @override_settings(SEASON_START_DATE=date(2026, 3, 1))
    def test_tomorrow_row_renders_when_present(self, client: Client, region) -> None:
        """A RegionDayRating row for today + 1 surfaces with non-no_rating attrs."""
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=date(2026, 3, 16),
            min_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            source_bulletin=bulletin,
        )
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

    The ``weather_header`` waffle flag is forced on for every test in
    this class via the autouse fixture below so each test exercises the
    rendered partial. The off-state is covered by
    :class:`TestWeatherHeaderFlagGate`.
    """

    @pytest.fixture(autouse=True)
    def _enable_weather_header_flag(self):
        """Force ``weather_header=on`` for every test in this class.

        ``override_flag`` cannot be used as a pytest class decorator
        (Django's decorator only supports ``unittest.TestCase``
        subclasses), so apply it via an autouse fixture as the rest of
        the codebase does (see ``tests/public/test_edit_resorts_api.py``).
        """
        with override_flag("weather_header", active=True):
            yield

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

    def test_no_snapshot_yields_none_in_context(self, client: Client, region) -> None:
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
        self, client: Client, region
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
        self, client: Client, region
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

    def test_cloudy_emits_no_day_night_suffix(self, client: Client, region) -> None:
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
        self, client: Client, region
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
        self, client: Client, region
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
        self, client: Client, region
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
        self, client: Client, region
    ) -> None:
        """A snapshot for a different region must not surface on this page."""
        other = RegionFactory.create(region_id="CH-9999", name="Other", slug="other")
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


@pytest.mark.django_db
class TestWeatherHeaderFlagGate:
    """The weather header partial is hidden when ``weather_header`` is off."""

    def _bulletin_url(self) -> str:
        """Return the today-bulletin URL (form-3 canonical post-SNOW-99)."""
        return reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
        )

    @override_flag("weather_header", active=False)
    def test_partial_hidden_when_flag_inactive(self, client: Client, region) -> None:
        """With the flag off, the unified header is not rendered.

        SNOW-100 made the flag the EITHER/OR switch between the unified
        ``bulletin_header.html`` (flag on) and the legacy
        ``bulletin_masthead.html`` (flag off). Assert the legacy masthead
        renders and the unified header does not. ``weather_display`` is
        still computed in the view regardless of the flag — it's the
        template that picks which partial to include.
        """
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

        assert response.status_code == 200
        assert b'data-testid="bulletin-header"' not in response.content
        assert b'data-testid="bulletin-masthead"' in response.content
        # The view still computes weather_display — only the partial is gated.
        assert response.context["weather_display"] is not None

    @override_flag("weather_header", active=True)
    def test_partial_visible_when_flag_active(self, client: Client, region) -> None:
        """With the flag on, the unified header partial renders."""
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

        assert response.status_code == 200
        assert b'data-testid="bulletin-header"' in response.content
        # And the legacy masthead is not rendered alongside it.
        assert b'data-testid="bulletin-masthead"' not in response.content


# ── Masthead weather icon (SNOW-100) ─────────────────────────────────────────


@pytest.mark.django_db
class TestMastheadWeatherIcon:
    """The small weather icon in the masthead H1 row (SNOW-100).

    The masthead icon is independent of the ``weather_header`` waffle flag
    (that flag gates only the band partial below the masthead).  These tests
    therefore do NOT activate the flag, so they exercise the icon in isolation.
    """

    def _bulletin_url(self) -> str:
        """Return the canonical form-3 bulletin URL used by every test below."""
        return reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": "2026-03-15",
            },
        )

    def test_masthead_renders_weather_icon(self, client: Client, region) -> None:
        """When a snapshot exists, the masthead icon is present with the right src."""
        _make_am_bulletin(region, date(2026, 3, 15))
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=date(2026, 3, 15),
            weather_code=0,  # clear sky → clear-day.svg
            sunrise=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            sunset=datetime(2026, 3, 15, 18, 0, tzinfo=UTC),
        )

        with _freeze("2026-03-15T10:00:00+00:00"):
            response = client.get(self._bulletin_url())

        assert response.status_code == 200
        assert b'data-testid="bulletin-masthead-weather-icon"' in response.content
        assert b"icons/weather/clear-day.svg" in response.content

    def test_masthead_omits_weather_icon_when_no_snapshot(
        self, client: Client, region
    ) -> None:
        """When no WeatherSnapshot exists, the masthead icon is absent."""
        _make_am_bulletin(region, date(2026, 3, 15))
        # No WeatherSnapshotFactory call — snapshot deliberately absent.

        with _freeze("2026-03-15T10:00:00+00:00"):
            response = client.get(self._bulletin_url())

        assert response.status_code == 200
        assert b'data-testid="bulletin-masthead-weather-icon"' not in response.content


@pytest.mark.django_db
class TestCanonicalUrl:
    """The form-3 canonical URL is rendered as a ``<link rel="canonical">``."""

    def test_canonical_url_in_full_render(self, client: Client, region) -> None:
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

    def test_canonical_url_in_empty_state(self, client: Client, region) -> None:
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
        RegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")

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
