"""
tests/public/test_panel_template.py — Tests for the bulletin panel template.

Covers the rendered HTML output of public/_bulletin_panel.html for the
error state (version == 0), the empty-traits state, and the trait header
heading semantics.  These are integration tests that render the template
through the Django test client so the full template-tag and context
pipeline is exercised.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from tests.factories import BulletinFactory, RegionBulletinFactory, RegionFactory

User = get_user_model()


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


@pytest.fixture()
def region():
    """Return a test Region."""
    return RegionFactory.create(region_id="CH-7777", name="Test Valley", slug="ch-7777")


@pytest.fixture()
def staff_user(db):
    """Return a staff Django user."""
    return User.objects.create_user(
        username="staff",
        password="pass",  # noqa: S106  — test-only credential, not real
        is_staff=True,
    )


@pytest.fixture()
def anon_client():
    """Return an unauthenticated test client."""
    return Client()


@pytest.fixture()
def staff_client(staff_user):
    """Return a test client logged in as the staff user."""
    c = Client()
    c.force_login(staff_user)
    return c


def _bulletin_url(day: date, region_id: str = "CH-7777", slug: str = "ch-7777") -> str:
    """Build the bulletin_date URL for a given day."""
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

    def _make_error_bulletin(self, region, day: date):
        """Create a bulletin whose render_model carries a stored error.

        render_model_version is set to match RENDER_MODEL_VERSION (2) so the
        view uses the stored render_model directly rather than rebuilding it.
        The stored render_model has version==0 which triggers the error card.
        """
        from pipeline.services.render_model import RENDER_MODEL_VERSION

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

    def test_staff_sees_error_text_and_admin_link(self, staff_client: Client, region):
        """Staff users see the raw error string and an admin change link."""
        day = date(2026, 5, 1)
        bulletin = self._make_error_bulletin(region, day)
        response = staff_client.get(_bulletin_url(day))

        assert response.status_code == 200
        content = response.content.decode()

        # Admin link to the specific bulletin change page.
        expected_admin = f"/admin/pipeline/bulletin/{bulletin.pk}/change/"
        assert expected_admin in content

        # Raw error text visible.
        assert "Synthetic test error" in content

    def test_anon_does_not_see_error_text(self, anon_client: Client, region):
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

    def test_anon_does_not_see_admin_link(self, anon_client: Client, region):
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
class TestTraitHeaderSemantics:
    """
    Tests that trait titles render inside <h2> elements.

    The page hierarchy is ``h1`` (region) → ``h2`` (trait title) → ``h2``
    (Snowpack & Weather section) so Lighthouse's ``heading-order`` audit
    passes. Traits must NOT render as <h3> (which would skip a level
    given the h1 parent).
    """

    def test_trait_title_rendered_in_h2(self, anon_client: Client, region):
        """Each trait title appears inside an <h2> tag, not a <p> or <div>."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

        day = date(2026, 5, 10)
        _make_am_bulletin(
            region,
            day,
            render_model={
                "version": RENDER_MODEL_VERSION,
                "traits": [
                    {
                        "title": "Dry avalanches, whole day",
                        "category": "dry",
                        "time_period": "all_day",
                        "problems": [],
                        "geography": {"source": "no_problems"},
                        "prose": "",
                    }
                ],
                "danger": {},
            },
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={
                "properties": {
                    "dangerRatings": [{"mainValue": "low"}],
                }
            },
        )
        response = anon_client.get(_bulletin_url(day))
        assert response.status_code == 200
        content = response.content.decode()

        # Title should appear inside an h2 element (the trait heading level).
        assert "<h2" in content
        assert "Dry avalanches, whole day" in content

    def test_two_trait_rating_blocks_present(self, anon_client: Client, region):
        """A variable-day bulletin produces one rating block per trait."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

        day = date(2026, 5, 11)
        _make_am_bulletin(
            region,
            day,
            render_model={
                "version": RENDER_MODEL_VERSION,
                "traits": [
                    {
                        "title": "Dry avalanches, whole day",
                        "category": "dry",
                        "time_period": "all_day",
                        "problems": [],
                        "geography": {"source": "no_problems"},
                        "prose": "",
                    },
                    {
                        "title": "Wet-snow avalanches, later",
                        "category": "wet",
                        "time_period": "later",
                        "problems": [],
                        "geography": {"source": "no_problems"},
                        "prose": "",
                    },
                ],
                "danger": {},
            },
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={
                "properties": {
                    "dangerRatings": [{"mainValue": "moderate"}],
                }
            },
        )
        response = anon_client.get(_bulletin_url(day))
        assert response.status_code == 200
        content = response.content.decode()

        # Count rating-block wrappers rather than ``<h2`` occurrences: the page
        # now carries other ``<h2>`` elements outside the rating blocks (section
        # headings for "Avalanche Problems", "Snowpack & Weather", …) and the
        # test's concern is specifically "one rating block per trait", not
        # "exactly N headings on the page".
        assert content.count('data-testid="rating-block"') == 2
        assert "Dry avalanches, whole day" in content
        assert "Wet-snow avalanches, later" in content


# ── Per-problem danger_level_css enrichment ───────────────────────────────────


class TestEnrichRenderModelProblemDangerLevelCss:
    """Unit tests for the danger_level_css field added by _enrich_render_model_problem."""

    def _enrich(self, danger_rating_value):
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

    def test_known_level_is_passed_through(self):
        """A recognised danger_rating_value is returned as danger_level_css."""
        for level in ("low", "moderate", "considerable", "high", "very_high"):
            result = self._enrich(level)
            assert result["danger_level_css"] == level, f"expected {level!r}"

    def test_none_produces_empty_string(self):
        """danger_rating_value=None produces an empty danger_level_css."""
        result = self._enrich(None)
        assert result["danger_level_css"] == ""

    def test_unknown_value_produces_empty_string(self):
        """An unrecognised danger_rating_value falls back to empty string."""
        result = self._enrich("extreme")
        assert result["danger_level_css"] == ""


@pytest.mark.django_db
class TestProblemLevelDataAttribute:
    """Integration test: data-level attribute rendered in the panel HTML."""

    def test_problem_level_data_attribute_in_html(self, anon_client: Client, region):
        """A problem with danger_rating_value renders data-level on its wrapper div."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

        day = date(2026, 5, 20)
        _make_am_bulletin(
            region,
            day,
            render_model={
                "version": RENDER_MODEL_VERSION,
                "traits": [
                    {
                        "title": "Wet avalanches",
                        "category": "wet",
                        "time_period": "all_day",
                        "problems": [
                            {
                                "problem_type": "wet_snow",
                                "danger_rating_value": "considerable",
                                "time_period": "all_day",
                                "elevation": None,
                                "aspects": [],
                                "core_zone_text": "",
                                "comment_html": "",
                            }
                        ],
                        "geography": {"source": "structured"},
                        "prose": "",
                    }
                ],
                "danger": {},
            },
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={
                "properties": {
                    "dangerRatings": [{"mainValue": "considerable"}],
                }
            },
        )
        response = anon_client.get(_bulletin_url(day))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-level="considerable"' in content

    def test_none_danger_renders_empty_data_level(self, anon_client: Client, region):
        """A problem with danger_rating_value=None renders data-level="" (neutral)."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

        day = date(2026, 5, 21)
        _make_am_bulletin(
            region,
            day,
            render_model={
                "version": RENDER_MODEL_VERSION,
                "traits": [
                    {
                        "title": "Dry avalanches",
                        "category": "dry",
                        "time_period": "all_day",
                        "problems": [
                            {
                                "problem_type": "new_snow",
                                "danger_rating_value": None,
                                "time_period": "all_day",
                                "elevation": None,
                                "aspects": [],
                                "core_zone_text": "",
                                "comment_html": "",
                            }
                        ],
                        "geography": {"source": "structured"},
                        "prose": "",
                    }
                ],
                "danger": {},
            },
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={
                "properties": {
                    "dangerRatings": [{"mainValue": "moderate"}],
                }
            },
        )
        response = anon_client.get(_bulletin_url(day))
        assert response.status_code == 200
        content = response.content.decode()
        # The problem wrapper should carry data-level="" (empty → neutral grey border).
        assert 'data-level=""' in content


# ── XSS injection prevention ─────────────────────────────────────────────────
#
# _bulletin_panel.html is rendered by the random_bulletins view
# (/<region_id>/history/), not by bulletin_date.  Tests below use that URL.


_XSS_INNER = "alert('xss_snow20')"
_SCRIPT_PAYLOAD = f"<script>{_XSS_INNER}</script>"
_SAFE_HTML = "<p>safe content</p>"


def _history_url(region_id: str = "CH-7777") -> str:
    """Build the random_bulletins (history) URL for a region."""
    return reverse("public:random_bulletins", kwargs={"region_id": region_id})


@pytest.mark.django_db
class TestXssInjectionPrevention:
    """
    Regression tests for SNOW-20: all four SLF prose render sites in
    _bulletin_panel.html must strip <script> tags via |snowdesk_html.
    """

    def _make_panel_bulletin(self, region, day, render_model, raw_data=None):
        """Create a bulletin linked to region, using the given render_model."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

        bulletin = BulletinFactory.create(
            issued_at=datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC),
            valid_from=datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC),
            valid_to=datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC),
            render_model={**render_model, "version": RENDER_MODEL_VERSION},
            render_model_version=RENDER_MODEL_VERSION,
            raw_data=raw_data
            or {"properties": {"dangerRatings": [{"mainValue": "low"}]}},
        )
        RegionBulletinFactory.create(
            bulletin=bulletin, region=region, region_name_at_time=region.name
        )
        return bulletin

    def test_trait_prose_strips_script(self, anon_client: Client, region):
        """trait.prose with a <script> payload must not appear in the rendered panel."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

        self._make_panel_bulletin(
            region,
            date(2026, 7, 1),
            render_model={
                "version": RENDER_MODEL_VERSION,
                "traits": [
                    {
                        "title": "Dry avalanches",
                        "category": "dry",
                        "time_period": "all_day",
                        "problems": [],
                        "geography": {"source": "prose_only"},
                        "prose": _SCRIPT_PAYLOAD + _SAFE_HTML,
                    }
                ],
                "danger": {},
            },
        )
        response = anon_client.get(_history_url())
        assert response.status_code == 200
        content = response.content.decode()
        assert _SCRIPT_PAYLOAD not in content
        assert "safe content" in content

    def test_problem_comment_html_strips_script(self, anon_client: Client, region):
        """p.comment_html with a <script> payload must not appear in the rendered panel."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

        self._make_panel_bulletin(
            region,
            date(2026, 7, 2),
            render_model={
                "version": RENDER_MODEL_VERSION,
                "traits": [
                    {
                        "title": "Wet avalanches",
                        "category": "wet",
                        "time_period": "all_day",
                        "problems": [
                            {
                                "problem_type": "wet_snow",
                                "danger_rating_value": "low",
                                "time_period": "all_day",
                                "elevation": None,
                                "aspects": [],
                                "core_zone_text": "",
                                "comment_html": _SCRIPT_PAYLOAD + _SAFE_HTML,
                            }
                        ],
                        "geography": {"source": "structured"},
                        "prose": "",
                    }
                ],
                "danger": {},
            },
        )
        response = anon_client.get(_history_url())
        assert response.status_code == 200
        content = response.content.decode()
        assert _SCRIPT_PAYLOAD not in content
        assert "safe content" in content

    def test_snowpack_structure_strips_script(self, anon_client: Client, region):
        """panel.snowpack_structure with a <script> payload must be stripped."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

        self._make_panel_bulletin(
            region,
            date(2026, 7, 3),
            render_model={
                "version": RENDER_MODEL_VERSION,
                "traits": [],
                "danger": {},
            },
            raw_data={
                "properties": {
                    "dangerRatings": [{"mainValue": "low"}],
                    "snowpackStructure": {"comment": _SCRIPT_PAYLOAD + _SAFE_HTML},
                }
            },
        )
        response = anon_client.get(_history_url())
        assert response.status_code == 200
        content = response.content.decode()
        assert _SCRIPT_PAYLOAD not in content
        assert "safe content" in content

    def test_allowed_tags_survive_sanitisation(self, anon_client: Client, region):
        """Benign allowed tags (<strong>) still render inside trait.prose."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

        self._make_panel_bulletin(
            region,
            date(2026, 7, 4),
            render_model={
                "version": RENDER_MODEL_VERSION,
                "traits": [
                    {
                        "title": "Dry avalanches",
                        "category": "dry",
                        "time_period": "all_day",
                        "problems": [],
                        "geography": {"source": "prose_only"},
                        "prose": "<p><strong>snow20_marker</strong> note.</p>",
                    }
                ],
                "danger": {},
            },
        )
        response = anon_client.get(_history_url())
        assert response.status_code == 200
        content = response.content.decode()
        assert "<strong>snow20_marker</strong>" in content
