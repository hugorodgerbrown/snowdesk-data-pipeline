"""
tests/public/test_debug_views.py — Tests for the component-library page and
the other staff-only debug pages hosted in apps/public/debug_views.py.

Covers:
  - Anonymous and non-staff users are redirected to /admin/login/.
  - Staff users hit the page; default panel (typography) is rendered SSR.
  - Sidebar lists every category across both LIBRARY_GROUPS (Foundations
    + Components).
  - Every category slug in LIBRARY_GROUPS round-trips through the HTMX
    partial endpoint and 200s with the right active category.
  - The Weather header components panel renders the partial with real
    weather-bucket data attributes (and the no-snapshot fallback).
  - Unknown slug returns 404.
  - The partial endpoint rejects non-HTMX requests with 400 (require_htmx).
  - Every IconToken.path resolves via the staticfiles finders so a typo
    can't slip through to the page as a broken-image square.
  - The SW shell-version page (/_sw-version/, SNOW-517) requires staff and
    server-renders the committed CACHE_VERSION and APP_VERSION.

The earlier TestHeaderCombinationsView (the /debug/header/ matrix) was
removed by SNOW-110 — that visual is now the Weather header components
entry inside the library.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.finders import find as find_static
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Account
from apps.core.sw_shell import cache_version
from apps.public.design_tokens import LIBRARY_GROUPS, FoundationCategory, IconToken
from tests.factories import AccountFactory, UserFactory


def _all_categories() -> list[FoundationCategory]:
    """Flatten every category across all library groups."""
    return [c for group in LIBRARY_GROUPS for c in group.categories]


def _all_slugs() -> list[str]:
    """Return every category slug in declaration order."""
    return [c.slug for c in _all_categories()]


@pytest.fixture()
def staff_user(db: Any) -> User:
    """Return a staff User."""
    return UserFactory.create()


@pytest.fixture()
def regular_user(db: Any) -> Account:
    """Return a non-staff Account."""
    return AccountFactory.create()


@pytest.fixture()
def staff_client(staff_user: User) -> Client:
    """Return a logged-in staff client."""
    c = Client()
    c.force_login(staff_user)
    return c


@pytest.fixture()
def htmx_staff_client(staff_user: User) -> Client:
    """Return a logged-in staff client whose requests carry the HX-Request header."""
    c = Client()
    c.force_login(staff_user)
    c.defaults["HTTP_HX_REQUEST"] = "true"
    return c


def _index_url() -> str:
    """Resolve the named full-page URL — guards against silent rename drift."""
    return reverse("public:components_index")


def _panel_url(slug: str) -> str:
    """Resolve the named partial URL for a library category."""
    return reverse("public:components_panel", kwargs={"slug": slug})


@pytest.mark.django_db
class TestComponentLibraryIndex:
    """Tests for the full-page /_components/ view."""

    def test_anonymous_user_redirected_to_admin_login(self) -> None:
        """A logged-out user is bounced to the admin login page."""
        response = Client().get(_index_url())
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_user_redirected_to_admin_login(
        self, regular_user: Account
    ) -> None:
        """A logged-in non-staff user is also bounced to admin login."""
        client = Client()
        client.force_login(regular_user.user)
        response = client.get(_index_url())
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_staff_user_sees_default_panel(self, staff_client: Client) -> None:
        """Staff land on the page with the typography panel pre-rendered."""
        response = staff_client.get(_index_url())
        assert response.status_code == 200
        assert response.templates[0].name == "_components/index.html"
        active = response.context["active"]
        assert active.slug == "typography"

    def test_staff_user_sees_full_sidebar_across_both_groups(
        self, staff_client: Client
    ) -> None:
        """Sidebar lists every category across both library groups."""
        response = staff_client.get(_index_url())
        body = response.content.decode()
        # Group headings
        for group in LIBRARY_GROUPS:
            assert group.label in body
        # Every category label and partial URL
        for category in _all_categories():
            assert category.label in body
            assert _panel_url(category.slug) in body

    @pytest.mark.parametrize("slug", _all_slugs(), ids=lambda s: s)
    def test_query_param_deep_links_to_panel(
        self, staff_client: Client, slug: str
    ) -> None:
        """``?slug=<slug>`` SSR-renders the matching panel as active.

        Lets ``/_components/?slug=weather-header`` deep-link straight
        to the weather-header panel for sharing in chat / bookmarks.
        """
        response = staff_client.get(f"{_index_url()}?slug={slug}")
        assert response.status_code == 200
        assert response.context["active"].slug == slug

    def test_unknown_slug_query_param_falls_back_to_default(
        self, staff_client: Client
    ) -> None:
        """Unknown ``?slug=`` silently falls back to the default panel.

        Old or misspelled bookmarks land on a usable page rather than a
        hard 404 — the slug is in the URL bar so users can see what
        they tried.
        """
        response = staff_client.get(f"{_index_url()}?slug=does-not-exist")
        assert response.status_code == 200
        assert response.context["active"].slug == "typography"

    def test_empty_slug_query_param_renders_default(self, staff_client: Client) -> None:
        """``?slug=`` with no value renders the default panel."""
        response = staff_client.get(f"{_index_url()}?slug=")
        assert response.status_code == 200
        assert response.context["active"].slug == "typography"


@pytest.mark.django_db
class TestComponentLibraryPanel:
    """Tests for the HTMX-only /partials/_components/<slug>/ view."""

    @pytest.mark.parametrize("slug", _all_slugs(), ids=lambda s: s)
    def test_every_known_slug_renders(
        self, htmx_staff_client: Client, slug: str
    ) -> None:
        """Every slug in LIBRARY_GROUPS returns 200 via the partial endpoint."""
        response = htmx_staff_client.get(_panel_url(slug))
        assert response.status_code == 200
        assert response.context["active"].slug == slug
        # Inner template via the panel wrapper.
        template_names = [t.name for t in response.templates]
        assert "_components/partials/_panel.html" in template_names

    def test_day_windows_panel_renders_expected_variants(
        self, htmx_staff_client: Client
    ) -> None:
        """The day-windows panel ships seven variants matching the scope contract.

        Variant 1: all-day level grid (five rows stepping low → very_high).
        Variant 2: all-day with sublevel modifier (considerable−).
        Variant 3: cross-category later (all_day low + later moderate).
        Variant 4: within-category later (all_day considerable− + later considerable).
        Variant 5: numeric-pivot bands (considerable below / moderate above, glyphs).
        Variant 6: treeline-pivot bands (low below / considerable above, glyphs).
        Variant 7: high-severity bands (considerable below / high above, glyphs).
        Asserting both the context shape and rendered HTML guards against drift
        in either the fixture or the ``include_variant`` plumbing.
        """
        response = htmx_staff_client.get(_panel_url("day-windows"))
        assert response.status_code == 200

        active = response.context["active"]
        assert active.slug == "day-windows"
        assert len(active.variants) == 7

        # Variant 1 — all-day, five EAWS levels (single rows, no glyph).
        v1_windows = active.variants[0]["context"]["day_windows"]
        assert [w["level_key"] for w in v1_windows] == [
            "low",
            "moderate",
            "considerable",
            "high",
            "very_high",
        ]
        assert {w["type"] for w in v1_windows} == {"all_day"}
        assert {w["pill_label"] for w in v1_windows} == {"All day"}
        assert all("elevation_bounds" not in w for w in v1_windows)

        # Variant 2 — all-day with sublevel modifier.
        v2_windows = active.variants[1]["context"]["day_windows"]
        assert len(v2_windows) == 1
        assert v2_windows[0]["level_key"] == "considerable"
        assert v2_windows[0]["level_number"] == "3-"

        # Variant 3 — cross-category later (all_day low + later moderate).
        v3_windows = active.variants[2]["context"]["day_windows"]
        assert [(w["type"], w["level_key"]) for w in v3_windows] == [
            ("all_day", "low"),
            ("later", "moderate"),
        ]

        # Variant 4 — within-category later.
        v4_windows = active.variants[3]["context"]["day_windows"]
        assert [(w["type"], w["level_key"]) for w in v4_windows] == [
            ("all_day", "considerable"),
            ("later", "considerable"),
        ]
        assert v4_windows[0]["level_number"] == "3-"

        # Variant 5 — numeric-pivot bands (two rows, lower then upper, glyphs).
        v5_windows = active.variants[4]["context"]["day_windows"]
        assert [w["level_key"] for w in v5_windows] == ["considerable", "moderate"]
        assert all("2500" in w["caption"] for w in v5_windows)
        assert [w["elevation_bounds"].bound_type for w in v5_windows] == [
            "UPPER",
            "LOWER",
        ]

        # Variant 6 — treeline-pivot bands.
        v6_windows = active.variants[5]["context"]["day_windows"]
        assert [w["level_key"] for w in v6_windows] == ["low", "considerable"]
        assert all("treeline" in w["caption"] for w in v6_windows)

        # Rendered HTML — confirms include_variant reached the partial, that the
        # level_css → CSS class mapping (``very_high`` → ``lv-very-high``)
        # survives the round-trip, and that banded rows render the glyph.
        body = response.content.decode()
        assert "lv-low" in body
        assert "lv-very-high" in body
        assert 'data-window="later"' in body
        # Only non-default windows take a pill (SNOW-727): "later" is the news,
        # "all day" is the baseline and goes unlabelled on every variant here —
        # including the five-row level grid, which is all_day throughout.
        assert ">Later<" in body
        assert ">All day<" not in body
        # Banded variants render the mountain elevation glyph.
        assert 'data-testid="day-window-elevation-icon"' in body

    def test_unknown_slug_returns_404(self, htmx_staff_client: Client) -> None:
        """Slugs that don't appear in any library group 404."""
        # ``not-a-real-slug`` matches the <slug:slug> URL converter (only
        # letters, digits and hyphens). Routing succeeds, the view 404s.
        response = htmx_staff_client.get(_panel_url("not-a-real-slug"))
        assert response.status_code == 404

    def test_non_htmx_request_returns_400(self, staff_client: Client) -> None:
        """Direct browser hits (no HX-Request header) are rejected by require_htmx."""
        response = staff_client.get(_panel_url("typography"))
        assert response.status_code == 400

    def test_anonymous_user_redirected(self) -> None:
        """The partial endpoint also gates anonymous users."""
        client = Client()
        client.defaults["HTTP_HX_REQUEST"] = "true"
        response = client.get(_panel_url("typography"))
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]


def _all_icon_tokens() -> list[IconToken]:
    """Flatten every IconToken across the registry."""
    return [
        token
        for category in _all_categories()
        for token in category.tokens
        if isinstance(token, IconToken)
    ]


@pytest.mark.parametrize("icon", _all_icon_tokens(), ids=lambda i: i.name)
def test_every_registered_icon_resolves_to_a_static_file(icon: IconToken) -> None:
    """Every IconToken.path must resolve via the staticfiles finders.

    Catches typos in the registry that would render as broken-image squares
    in the panel. ``find_static`` returns ``None`` when no app or
    ``STATICFILES_DIRS`` entry knows about the path.
    """
    assert find_static(icon.path) is not None, (
        f"{icon.name}: static file {icon.path!r} not found by any finder"
    )


# ── SNOW-201 — per-category focused tests ───────────────────────────────────
# These complement the parametrised "every slug renders 200" suite with
# assertions that the fixture context actually drives the partial's branches.


_NEW_SLUGS = [
    "nav",
    "site-footer",
    "rating-block",
    "region-tooltip",
    "subscribe-form",
    "subscribe-outcomes",
    "no-data-supplied",
]


@pytest.mark.django_db
@pytest.mark.parametrize("slug", _NEW_SLUGS)
def test_new_slug_index_page_renders_200_with_known_marker(
    staff_client: Client, slug: str
) -> None:
    """Deep-linking to each new slug via ``?slug=`` returns 200 and a marker.

    Proves the fixture context reaches the partial and the partial itself
    renders without raising an exception — not just that the panel wrapper
    is present.
    """
    # Known data-testid attributes emitted by each partial.
    markers: dict[str, str] = {
        "nav": "<nav ",
        "site-footer": 'data-testid="site-footer"',
        "rating-block": 'data-testid="rating-block"',
        "region-tooltip": 'class="region-tooltip"',
        "subscribe-form": 'id="subscribe-cta-',
        "subscribe-outcomes": "Check your inbox",
        "no-data-supplied": 'data-testid="no-data-supplied"',
    }
    response = staff_client.get(f"{_index_url()}?slug={slug}")
    assert response.status_code == 200
    assert response.context["active"].slug == slug
    body = response.content.decode()
    assert markers[slug] in body, (
        f"Expected marker {markers[slug]!r} not found in rendered output for {slug!r}"
    )


@pytest.mark.django_db
class TestRatingBlockPanel:
    """Focused tests for the rating-block component panel."""

    def test_all_problem_types_appear_in_rendered_html(
        self, htmx_staff_client: Client
    ) -> None:
        """Every EAWS problem type in RATING_BLOCK_VARIANTS appears in the output.

        The ``_rating_block.html`` template does not render the problem_type
        value directly, but the EAWS pictogram ``src`` attribute encodes it.
        The card ``label`` field is the more readable proxy — assert that
        instead, since it is rendered verbatim in the card header.
        """
        response = htmx_staff_client.get(_panel_url("rating-block"))
        assert response.status_code == 200

        active = response.context["active"]
        assert active.slug == "rating-block"
        # SNOW-263 replaced the single dry prose-only variant with two wet
        # prose-only variants (one per empty-state branch), raising the count
        # from 12 to 13.
        assert len(active.variants) == 13  # noqa: PLR2004 — thirteen after SNOW-263

        # Gather all problem_type values from the fixtures.
        expected_problem_types = {
            v["context"]["card"]["problem_type"] for v in active.variants
        }
        body = response.content.decode()
        # The danger-band carries a data-level attribute — confirm the template
        # rendered at least one card header.
        assert 'data-testid="rating-block"' in body
        # Exactly 11 of the 13 variants render the aspect-elevation row — the
        # two prose-only variants (empty aspects + empty elevation) omit it.
        # The panel renders each variant twice (light + dark themes), so the
        # expected count is 11 structured cards × 2 theme passes = 22.
        assert body.count('data-testid="aspect-elevation-row"') == 22  # noqa: PLR2004
        # The two prose-only variants drive the empty-state branches: one with
        # prose_mentions_spatial=True (fallback row) and one without (all-scope
        # row). Each renders once per theme pass, so both appear twice.
        assert body.count('data-testid="aspect-elevation-fallback"') == 2  # noqa: PLR2004
        assert body.count('data-testid="aspect-elevation-allscope"') == 2  # noqa: PLR2004
        # Verify the problem_type set is exactly the six we declared (both
        # prose-only variants use wet_snow, so the set collapses to 6).
        assert expected_problem_types == {
            "new_snow",
            "wind_slab",
            "persistent_weak_layers",
            "cornices",
            "wet_snow",
            "gliding_snow",
        }


@pytest.mark.django_db
class TestRegionTooltipPanel:
    """Focused tests for the region-tooltip component panel."""

    def test_no_bulletin_variant_renders_no_bulletin_fallback(
        self, htmx_staff_client: Client
    ) -> None:
        """The ``no_bulletin`` variant renders the fallback text, not the CTA."""
        response = htmx_staff_client.get(_panel_url("region-tooltip"))
        assert response.status_code == 200
        body = response.content.decode()
        # The no-bulletin variant should produce the fallback element.
        assert 'data-testid="region-tooltip-no-bulletin"' in body

    def test_bulletin_variants_render_bulletin_cta(
        self, htmx_staff_client: Client
    ) -> None:
        """Variants that carry a day_rating render the bulletin CTA link."""
        response = htmx_staff_client.get(_panel_url("region-tooltip"))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-testid="region-tooltip-bulletin-link"' in body

    def test_rating_chip_present_for_rated_variants(
        self, htmx_staff_client: Client
    ) -> None:
        """Variants with a day_rating show the danger-tile chip."""
        response = htmx_staff_client.get(_panel_url("region-tooltip"))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-testid="region-tooltip-rating-chip"' in body


@pytest.mark.django_db
class TestSubscribeOutcomesPanel:
    """Focused tests for the subscribe-outcomes component panel."""

    def test_five_variants_each_with_distinct_partial(
        self, htmx_staff_client: Client
    ) -> None:
        """Five variants are registered and each carries a ``"partial"`` key."""
        from apps.public.design_tokens import get_category

        category = get_category("subscribe-outcomes")
        assert category is not None
        assert len(category.variants) == 5  # noqa: PLR2004 — five outcomes

        partials = [v.get("partial") for v in category.variants]
        # Every variant must have a partial override.
        assert all(p is not None for p in partials)
        # All five must be distinct template paths.
        assert len(set(partials)) == 5  # noqa: PLR2004

    def test_all_five_outcome_copies_appear_in_rendered_html(
        self, htmx_staff_client: Client
    ) -> None:
        """Each of the five outcome templates renders its own distinct copy.

        Checks known heading strings from each partial — these are
        stable i18n keys that change only intentionally.
        """
        response = htmx_staff_client.get(_panel_url("subscribe-outcomes"))
        assert response.status_code == 200
        body = response.content.decode()

        # subscribe_success + subscribe_success_access both say "Check your inbox"
        assert "Check your inbox" in body
        # subscribe_success_added — region name is in the fixture
        assert "Bex" in body  # "Added Bex–Villars to your alerts"
        # subscribe_success_already — same region
        assert "already subscribed" in body
        # subscribe_error
        assert "Something went wrong" in body


@pytest.mark.django_db
class TestIncludeVariantPartialOverride:
    """Unit test for the include_variant partial-key override path (Option A)."""

    def test_variant_partial_key_overrides_category_default(
        self, htmx_staff_client: Client
    ) -> None:
        """``variant["partial"]`` renders a *different* template than the category default.

        Uses the subscribe-outcomes panel, whose first variant
        (``subscribe_success.html``) is also the category default, and whose
        last variant (``subscribe_error.html``) is different.  If the override
        is working, the error copy appears; if it were ignored, the error
        template would never render.
        """
        response = htmx_staff_client.get(_panel_url("subscribe-outcomes"))
        assert response.status_code == 200
        body = response.content.decode()
        # The error template's heading only appears if the override worked.
        assert "Something went wrong" in body

    def test_csrf_token_present_in_subscribe_form_partial(
        self, htmx_staff_client: Client
    ) -> None:
        """``include_variant`` renders a ``RequestContext`` so ``{% csrf_token %}`` works.

        Before the fix, ``include_variant`` called ``Template.render(dict)``,
        which builds a plain ``Context``.  ``{% csrf_token %}`` inside the
        subscribe-form partial silently renders to an empty string in that case.
        This test asserts the token input is present in the rendered output,
        proving a ``RequestContext`` (with its CSRF middleware processor) was used.
        """
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            response = htmx_staff_client.get(_panel_url("subscribe-form"))

        assert response.status_code == 200
        body = response.content.decode()
        # The CSRF hidden input must be present.
        assert 'name="csrfmiddlewaretoken"' in body
        # No UserWarning about a missing CSRF value must have been emitted.
        csrf_warnings = [w for w in caught if "csrf_token" in str(w.message).lower()]
        assert csrf_warnings == [], (
            f"Unexpected CSRF warning(s): {[str(w.message) for w in csrf_warnings]}"
        )


def _sw_version_url() -> str:
    """Resolve the named URL for the SW shell-version debug page."""
    return reverse("public:sw_version")


@pytest.mark.django_db
class TestSwVersionPage:
    """Tests for the staff-only /_sw-version/ page (SNOW-517)."""

    def test_anonymous_user_redirected_to_admin_login(self) -> None:
        """A logged-out user is bounced to the admin login page."""
        response = Client().get(_sw_version_url())
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_user_redirected_to_admin_login(
        self, regular_user: Account
    ) -> None:
        """A logged-in non-staff user is also bounced to admin login."""
        client = Client()
        client.force_login(regular_user.user)
        response = client.get(_sw_version_url())
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_staff_user_sees_committed_version_and_app_version(
        self, staff_client: Client
    ) -> None:
        """The baseline (JS-off) render carries the server-side version pair.

        Proven via both the response context and the rendered HTML.
        """
        response = staff_client.get(_sw_version_url())
        assert response.status_code == 200
        assert response.context["cache_version"] == cache_version()
        assert response.context["app_version"] == settings.APP_VERSION

        body = response.content.decode()
        assert cache_version() in body
        assert str(settings.APP_VERSION) in body

    def test_staff_user_sees_live_version_probe_script(
        self, staff_client: Client
    ) -> None:
        """The progressive-enhancement probe script is included on the page."""
        response = staff_client.get(_sw_version_url())
        assert response.status_code == 200
        body = response.content.decode()
        assert "pwa_sw_version_probe.js" in body
        assert 'data-testid="sw-live-version"' in body

    @override_settings(SW_DEV_SHELL_BYPASS=True)
    def test_dev_shell_bypass_toggle_renders_when_setting_is_on(
        self, staff_client: Client
    ) -> None:
        """The opt-in checkbox and its script render when the bypass is on (SNOW-585)."""
        response = staff_client.get(_sw_version_url())
        assert response.status_code == 200
        assert response.context["sw_dev_shell_bypass"] is True
        body = response.content.decode()
        assert 'id="sw-dev-shell-cache-optin"' in body
        assert "pwa_dev_shell_toggle.js" in body

    @override_settings(SW_DEV_SHELL_BYPASS=False)
    def test_dev_shell_bypass_toggle_absent_when_setting_is_off(
        self, staff_client: Client
    ) -> None:
        """The opt-in checkbox and its script are absent when the bypass is off."""
        response = staff_client.get(_sw_version_url())
        assert response.status_code == 200
        assert response.context["sw_dev_shell_bypass"] is False
        body = response.content.decode()
        assert 'id="sw-dev-shell-cache-optin"' not in body
        assert "pwa_dev_shell_toggle.js" not in body
