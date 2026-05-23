"""
tests/public/test_panel_template.py — Tests for bulletin-page render-model output.

Covers the rendered HTML output of the bulletin detail page for the
error state (``render_model.version == 0``), trait header heading
semantics, per-problem ``danger_level_css`` enrichment, and
view-level integration canaries for the adapter-based render model.
Integration tests render through the Django test client so the full
template-tag and context pipeline is exercised; the enrichment tests
are pure unit tests against ``_enrich_render_model_problem``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from bulletins.models import Bulletin
from regions.models import MicroRegion
from subscriptions.models import Subscriber
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    UserFactory,
)

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


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


@pytest.fixture()
def region() -> MicroRegion:
    """Return a test Region."""
    return MicroRegionFactory.create(
        region_id="CH-7777", name="Test Valley", slug="ch-7777"
    )


@pytest.fixture()
def staff_user(db: Any) -> Subscriber:
    """Return a staff Subscriber."""
    return UserFactory.create()


@pytest.fixture()
def anon_client() -> Client:
    """Return an unauthenticated test client."""
    return Client()


@pytest.fixture()
def staff_client(staff_user: Subscriber) -> Client:
    """Return a test client logged in as the staff user."""
    c = Client()
    c.force_login(staff_user)
    return c


def _bulletin_url(
    day: date, region_id: str = "ch-7777", slug: str = "test-valley"
) -> str:
    """Build the canonical form-3 URL for the test region.

    Defaults match the canonical components for the test fixture region
    (lowercase ``region_id`` + name-derived slug) so the GET hits the
    renderer directly rather than redirecting through the form-3
    canonical-redirect layer (SNOW-99).
    """
    return reverse(
        "public:bulletin_date",
        kwargs={
            "region_id": region_id,
            "slug": slug,
            "date_str": day.isoformat(),
        },
    )


# ── Error-state card (version == 0) ─────────────────────────────────────────


@pytest.mark.django_db
class TestErrorStateCard:
    """Tests for the version==0 error card in the bulletin panel template."""

    def _make_error_bulletin(self, region: MicroRegion, day: date) -> Bulletin:
        """Create a bulletin whose render_model carries a stored error.

        render_model_version is set to match RENDER_MODEL_VERSION (2) so the
        view uses the stored render_model directly rather than rebuilding it.
        The stored render_model has version==0 which triggers the error card.
        """
        from bulletins.services.render_model import RENDER_MODEL_VERSION

        return _make_am_bulletin(
            region,
            day,
            render_model={
                "version": 0,
                "error": "Synthetic test error — do not display",
                "error_type": "RenderModelBuildError",
            },
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={
                "properties": {
                    "dangerRatings": [{"mainValue": "moderate"}],
                }
            },
        )

    def test_staff_sees_error_text_and_admin_link(
        self, staff_client: Client, region: MicroRegion
    ) -> None:
        """Staff users see the raw error string and an admin change link."""
        day = date(2026, 5, 1)
        bulletin = self._make_error_bulletin(region, day)
        response = staff_client.get(_bulletin_url(day))

        assert response.status_code == 200
        content = response.content.decode()

        # The error band is rendered by includes/_callout.html with kind="warning".
        # Pin both attributes so a future rename of the partial or its testid
        # surfaces here rather than as a silent visual regression.
        assert 'data-testid="callout"' in content
        assert 'data-kind="warning"' in content

        # Admin link to the specific bulletin change page.
        expected_admin = f"/admin/bulletins/bulletin/{bulletin.pk}/change/"
        assert expected_admin in content

        # Raw error text visible.
        assert "Synthetic test error" in content

    def test_anon_does_not_see_error_text(
        self, anon_client: Client, region: MicroRegion
    ) -> None:
        """Non-staff users see a generic message, not the raw error."""
        day = date(2026, 5, 2)
        self._make_error_bulletin(region, day)
        response = anon_client.get(_bulletin_url(day))

        assert response.status_code == 200
        content = response.content.decode()

        # Raw error string must NOT be in the page.
        assert "Synthetic test error" not in content

        # Generic message must appear.
        assert "We are sorry for the inconvenience" in content

    def test_anon_does_not_see_admin_link(
        self, anon_client: Client, region: MicroRegion
    ) -> None:
        """The error-card 'Inspect in admin' link is absent for non-staff visitors.

        The bulletin footer also renders an 'Open in admin' shortcut
        when ``settings.DEBUG`` is truthy, which points to the same
        admin URL — that one is a dev convenience and is gated on
        DEBUG rather than user.is_staff. The test scope here is the
        error-card link in ``_bulletin_panel.html``.
        """
        day = date(2026, 5, 3)
        self._make_error_bulletin(region, day)
        response = anon_client.get(_bulletin_url(day))

        assert response.status_code == 200
        content = response.content.decode()

        # The panel error card's link reads "Inspect in admin →"; the
        # DEBUG-only footer link reads "Open in admin". Asserting on
        # the former keeps this test's contract narrow.
        assert "Inspect in admin" not in content


# ── Trait header semantics ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestProblemCardRendering:
    """
    Tests that problem cards render correctly.

    The page hierarchy is h1 (region) → h2 (Avalanche Problems section
    heading) → h2 (Snowpack & Weather section) — no per-card h2 any more
    since the card header is plain text.
    """

    def test_problem_label_rendered_in_card_header(
        self, anon_client: Client, region: MicroRegion
    ) -> None:
        """Problem type label ('Wind slab') appears in the card header area."""
        day = date(2026, 5, 10)
        _make_am_bulletin(
            region,
            day,
            # render_model_version=0 triggers an on-the-fly rebuild from raw_data
            # so the test exercises the full render path including problem cards.
            render_model_version=0,
            raw_data={
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "dangerRatings": [{"mainValue": "low"}],
                    "avalancheProblems": [
                        {
                            "problemType": "wind_slab",
                            "dangerRatingValue": "low",
                            "validTimePeriod": "all_day",
                            "aspects": ["N"],
                            "elevation": {"lowerBound": "2000"},
                            "comment": "<p>Wind slab hazard.</p>",
                        }
                    ],
                    "customData": {
                        "CH": {
                            "aggregation": [
                                {"category": "dry", "problemTypes": ["wind_slab"]}
                            ]
                        }
                    },
                },
            },
        )
        response = anon_client.get(_bulletin_url(day))
        assert response.status_code == 200
        content = response.content.decode()

        assert 'data-testid="rating-block"' in content
        assert "Wind slab" in content

    def test_two_problems_produce_two_blocks(
        self, anon_client: Client, region: MicroRegion
    ) -> None:
        """A bulletin with dry + wet problems produces two separate rating blocks."""
        day = date(2026, 5, 11)
        _make_am_bulletin(
            region,
            day,
            # render_model_version=0 triggers an on-the-fly rebuild from raw_data.
            render_model_version=0,
            raw_data={
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "dangerRatings": [{"mainValue": "moderate"}],
                    "avalancheProblems": [
                        {
                            "problemType": "wind_slab",
                            "dangerRatingValue": "moderate",
                            "validTimePeriod": "all_day",
                            "aspects": [],
                            "elevation": None,
                            "comment": "<p>Dry hazard.</p>",
                        },
                        {
                            "problemType": "wet_snow",
                            "dangerRatingValue": "low",
                            "validTimePeriod": "later",
                            "aspects": [],
                            "elevation": None,
                            "comment": "<p>Wet hazard.</p>",
                        },
                    ],
                    "customData": {
                        "CH": {
                            "aggregation": [
                                {"category": "dry", "problemTypes": ["wind_slab"]},
                                {"category": "wet", "problemTypes": ["wet_snow"]},
                            ]
                        }
                    },
                },
            },
        )
        response = anon_client.get(_bulletin_url(day))
        assert response.status_code == 200
        content = response.content.decode()

        assert content.count('data-testid="rating-block"') == 2
        assert "Wind slab" in content
        assert "Wet snow" in content


# ── Per-problem danger_level_css enrichment ───────────────────────────────────


class TestEnrichRenderModelProblemDangerLevelCss:
    """Unit tests for the danger_level_css field added by _enrich_render_model_problem."""

    def _enrich(self, danger_rating_value: Any) -> dict[str, Any]:
        """
        Call _enrich_render_model_problem with a minimal problem dict.

        Returns the enriched dict so tests can inspect danger_level_css.
        """
        from public.views import _enrich_render_model_problem

        rm_problem = {
            "problem_type": "new_snow",
            "danger_rating_value": danger_rating_value,
            "time_period": "all_day",
            "elevation": None,
            "aspects": [],
            "core_zone_text": "",
            "comment_html": "",
        }
        return _enrich_render_model_problem(rm_problem, {}, [rm_problem], 0)

    def test_known_level_is_passed_through(self) -> None:
        """A recognised danger_rating_value is returned as danger_level_css."""
        for level in ("low", "moderate", "considerable", "high", "very_high"):
            result = self._enrich(level)
            assert result["danger_level_css"] == level, f"expected {level!r}"

    def test_none_produces_empty_string(self) -> None:
        """danger_rating_value=None produces an empty danger_level_css."""
        result = self._enrich(None)
        assert result["danger_level_css"] == ""

    def test_unknown_value_produces_empty_string(self) -> None:
        """An unrecognised danger_rating_value falls back to empty string."""
        result = self._enrich("extreme")
        assert result["danger_level_css"] == ""


# ── View-level integration canaries ──────────────────────────────────────────


def _wrap_feature(properties: dict[str, Any]) -> dict[str, Any]:
    """Wrap a CAAML properties dict in a GeoJSON Feature envelope."""
    return {"type": "Feature", "geometry": None, "properties": properties}


@pytest.mark.django_db
class TestSlfSubdivisionPlusCanary:
    """
    AM/PM drift canary for SLF bulletins with a 3+ (considerable-plus) rating.

    A dangerRating whose ``customData.CH.subdivision`` is ``"plus"`` must
    propagate through the adapter → render model → panel context so that
    ``morning_subdivision`` and ``afternoon_subdivision`` are both ``"+"``
    when the rating covers ``all_day``.  Any regression that accidentally
    drops the subdivision on the ``danger.ratings`` projection path will
    surface here.
    """

    def test_considerable_plus_subdivision_propagates_to_panel_context(
        self,
    ) -> None:
        """morning_subdivision and afternoon_subdivision are '+' for 3+ rating."""
        from public.views import _build_panel_context

        fixture = json.loads(
            (_FIXTURE_DIR / "sample_subdivision_3plus_day.json").read_text()
        )
        bulletin = BulletinFactory.create(
            raw_data=fixture,
            issued_at=datetime(2026, 2, 5, 15, 0, tzinfo=UTC),
            valid_from=datetime(2026, 2, 5, 15, 0, tzinfo=UTC),
            valid_to=datetime(2026, 2, 6, 15, 0, tzinfo=UTC),
        )
        ctx = _build_panel_context(bulletin)

        assert ctx["morning_key"] == "considerable"
        assert ctx["morning_subdivision"] == "+"
        assert ctx["afternoon_key"] == "considerable"
        assert ctx["afternoon_subdivision"] == "+"


@pytest.mark.django_db
class TestAlbinaPanelContextCanary:
    """
    Integration canary: ALBINA bulletin produces a well-formed panel context.

    The adapter unit tests cover projection in isolation; this test exercises
    the full path from raw ALBINA bulletin JSON through
    ``build_render_model`` → ``_build_panel_context`` and asserts that
    ``problem_cards`` and ``day_character`` are non-empty.  A regression that
    decouples the ALBINA adapter from the view layer will surface here.
    """

    def test_albina_panel_context_has_problem_cards_and_day_character(
        self,
    ) -> None:
        """ALBINA panel context populates problem_cards and day_character."""
        from public.views import _build_panel_context

        props = json.loads((_FIXTURE_DIR / "albina_sample_bulletin.json").read_text())
        bulletin = BulletinFactory.create(
            raw_data=_wrap_feature(props),
            issued_at=datetime(2025, 11, 28, 18, 6, 1, tzinfo=UTC),
            valid_from=datetime(2025, 11, 28, 16, 0, tzinfo=UTC),
            valid_to=datetime(2025, 11, 29, 16, 0, tzinfo=UTC),
        )
        ctx = _build_panel_context(bulletin)

        assert ctx["problem_cards"], (
            "Expected at least one problem card for ALBINA bulletin"
        )
        day_char = ctx["day_character"]
        # ALBINA bulletins carry a tendency lead that overrides the computed
        # label — day_character.explainer carries the forecaster-authored text.
        assert day_char.explainer, "Expected non-empty day_character explainer"
