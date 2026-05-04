"""
tests/public/test_debug_views.py — Tests for the staff-only debug surfaces.

Two distinct surfaces live in :mod:`public.debug_views` and both are
exercised here:

* ``component_library`` / ``component_library_panel`` (SNOW-103) — the
  design system at ``/_components/``. Gated by ``staff_member_required``
  only, no DEBUG gate. Tests cover the auth/HTMX guards, default-panel
  SSR, every category slug round-tripping through the partial endpoint,
  and that every ``IconToken.path`` resolves via the static finders so
  a typo can't slip through to the page as a broken-image square.

* ``header_combinations`` (SNOW-100) — the bulletin-header matrix at
  ``/debug/header/``. Mounted only when ``settings.DEBUG`` is True and
  additionally gated on ``@_require_debug``. Tests cover the
  DEBUG/non-DEBUG gate, the staff/non-staff gate, the happy path with
  Region fixtures loaded, and the empty-DB fallback path.

The dev server runs with ``config.settings.development`` (DEBUG=True),
which the tox ``test`` env inherits via ``django_debug_mode = "keep"``.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.finders import find as find_static
from django.test import Client, override_settings
from django.urls import reverse

from bulletins.services.weather_display import WEATHER_ICON_BUCKETS
from public.design_tokens import FOUNDATION_CATEGORIES, IconToken
from tests.factories import RegionFactory

User = get_user_model()


# ---------------------------------------------------------------------------
# Component library (SNOW-103) — /_components/
# ---------------------------------------------------------------------------


@pytest.fixture()
def staff_user(db):
    """Return a staff Django user."""
    return User.objects.create_user(
        username="staff",
        password="pass",  # noqa: S106 — test-only credential, not real
        is_staff=True,
    )


@pytest.fixture()
def regular_user(db):
    """Return a non-staff Django user."""
    return User.objects.create_user(
        username="regular",
        password="pass",  # noqa: S106 — test-only credential, not real
        is_staff=False,
    )


@pytest.fixture()
def staff_client(staff_user) -> Client:
    """Return a logged-in staff client."""
    c = Client()
    c.force_login(staff_user)
    return c


@pytest.fixture()
def htmx_staff_client(staff_user) -> Client:
    """Return a logged-in staff client whose requests carry the HX-Request header."""
    c = Client()
    c.force_login(staff_user)
    c.defaults["HTTP_HX_REQUEST"] = "true"
    return c


def _index_url() -> str:
    """Resolve the named full-page URL — guards against silent rename drift."""
    return reverse("public:components_index")


def _panel_url(slug: str) -> str:
    """Resolve the named partial URL for a foundation category."""
    return reverse("public:components_panel", kwargs={"slug": slug})


@pytest.mark.django_db
class TestComponentLibraryIndex:
    """Tests for the full-page /_components/ view."""

    def test_anonymous_user_redirected_to_admin_login(self) -> None:
        """A logged-out user is bounced to the admin login page."""
        response = Client().get(_index_url())
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_user_redirected_to_admin_login(self, regular_user) -> None:
        """A logged-in non-staff user is also bounced to admin login."""
        client = Client()
        client.force_login(regular_user)
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

    def test_staff_user_sees_full_sidebar(self, staff_client: Client) -> None:
        """Sidebar lists every FOUNDATION_CATEGORIES entry."""
        response = staff_client.get(_index_url())
        body = response.content.decode()
        for category in FOUNDATION_CATEGORIES:
            assert category.label in body
            assert _panel_url(category.slug) in body


@pytest.mark.django_db
class TestComponentLibraryPanel:
    """Tests for the HTMX-only /partials/_components/<slug>/ view."""

    @pytest.mark.parametrize(
        "slug", [c.slug for c in FOUNDATION_CATEGORIES], ids=lambda s: s
    )
    def test_every_known_slug_renders(
        self, htmx_staff_client: Client, slug: str
    ) -> None:
        """Every slug in the registry returns 200 via the partial endpoint."""
        response = htmx_staff_client.get(_panel_url(slug))
        assert response.status_code == 200
        assert response.context["active"].slug == slug
        # Inner template via the panel wrapper.
        template_names = [t.name for t in response.templates]
        assert "_components/partials/_panel.html" in template_names

    def test_unknown_slug_returns_404(self, htmx_staff_client: Client) -> None:
        """Slugs that don't appear in the registry 404."""
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
        for category in FOUNDATION_CATEGORIES
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


# ---------------------------------------------------------------------------
# Header-combinations (SNOW-100) — /debug/header/ (DEBUG only)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHeaderCombinationsView:
    """Tests for ``public.debug_views.header_combinations``."""

    @pytest.fixture
    def hc_staff_user(self):
        """A logged-in staff user — required by ``@staff_member_required``."""
        return get_user_model().objects.create_user(
            username="qa",
            password="password",  # noqa: S106 — test fixture, not real creds
            is_staff=True,
        )

    @pytest.fixture
    def hc_staff_client(self, client: Client, hc_staff_user) -> Client:
        """A test client logged in as the staff user."""
        client.force_login(hc_staff_user)
        return client

    def _url(self) -> str:
        """Resolve the named URL — guards against silent rename drift."""
        return reverse("public:debug_header")

    def test_anonymous_user_redirected_to_login(self, client: Client) -> None:
        """``staff_member_required`` redirects anonymous users to admin login."""
        response = client.get(self._url())
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    @override_settings(DEBUG=False)
    def test_blocked_when_debug_false(self, hc_staff_client: Client) -> None:
        """``_require_debug`` returns 404 even for staff in a non-DEBUG env.

        The URL conf was loaded with DEBUG=True (test env), so the route
        still resolves — but the decorator check at request time blocks
        the response. This is the belt-and-braces guard.
        """
        response = hc_staff_client.get(self._url())
        assert response.status_code == 404

    def test_renders_all_icon_buckets_with_regions(
        self, hc_staff_client: Client
    ) -> None:
        """Happy path: every icon bucket appears as a section in the context."""
        # At least one Region must exist for the random-region branch to
        # take the non-fallback path.
        RegionFactory.create()

        response = hc_staff_client.get(self._url())

        assert response.status_code == 200
        assert response.templates[0].name == "debug/bulletin_header_combinations.html"
        sections = response.context["sections"]
        bucket_names = {section["icon_bucket"] for section in sections}
        # Every defined icon bucket maps to at least one WMO code, so
        # every bucket should produce a section. If a future change adds
        # a bucket with no WMO codes the view's ``if not codes: continue``
        # guard would silently drop it — that drift would surface here.
        assert bucket_names == set(WEATHER_ICON_BUCKETS)

        # Each section carries both day + night WeatherDisplay-shaped dicts
        # so the template can render the side-by-side panels.
        for section in sections:
            assert section["day"]["time_of_day"] == "day"
            assert section["night"]["time_of_day"] == "night"
            assert section["day"]["icon_filename"].endswith(".svg")
            assert section["night"]["icon_filename"].endswith(".svg")

        # Themes wire the light/dark wrappers in the template.
        assert response.context["themes"] == ["light", "dark"]
        # Calendar URL is the real ``public:calendar_partial`` path so the
        # HTMX trigger renders authentically inside each panel.
        assert "/partials/calendar/" in response.context["calendar_partial_url"]

    def test_falls_back_to_bex_villars_when_no_regions(
        self, hc_staff_client: Client
    ) -> None:
        """Empty Region table → hardcoded fallback context rather than 500."""
        # No RegionFactory — the table is empty for this test.
        response = hc_staff_client.get(self._url())

        assert response.status_code == 200
        assert response.context["region_name"] == "Bex-Villars"
        assert response.context["subregion_name"] == "Vaud Alps"
        # The calendar URL uses the fallback region_id "CH-2223".
        assert "ch-2223" in response.context["calendar_partial_url"].lower()
