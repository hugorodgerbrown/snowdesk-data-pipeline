"""
tests/public/test_debug_views.py — Tests for the component-library views.

Covers:
  - Anonymous and non-staff users are redirected to /admin/login/.
  - Staff users hit the page; default panel (typography) is rendered SSR.
  - Every category slug in FOUNDATION_CATEGORIES round-trips through the
    HTMX partial endpoint and 200s with the right active category.
  - Unknown slug returns 404.
  - The partial endpoint rejects non-HTMX requests with 400 (require_htmx).
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.finders import find as find_static
from django.test import Client
from django.urls import reverse

from public.design_tokens import FOUNDATION_CATEGORIES, IconToken

User = get_user_model()


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
