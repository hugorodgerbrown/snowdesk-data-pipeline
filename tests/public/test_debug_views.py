"""
tests/public/test_debug_views.py — Tests for the staff-only design-debug pages.

These views are mounted under ``/debug/`` in :mod:`public.urls` only when
``settings.DEBUG`` is True, and each is gated on
``@_require_debug`` + ``@staff_member_required``. The tests exercise:

* The DEBUG/non-DEBUG gate (decorator returns 404 in production).
* The staff/non-staff gate (Django redirects anonymous users to login).
* The happy path with Region fixtures loaded — every icon bucket is
  represented in the rendered context, the no-snapshot fallback panel
  renders, and the calendar trigger HTMX URL is wired.
* The empty-DB fallback path — when no Region rows exist, the view falls
  back to the hardcoded Bex-Villars context rather than raising.

The dev server runs with ``config.settings.development`` (DEBUG=True),
which the tox ``test`` env inherits via ``django_debug_mode = "keep"``.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import reverse

from bulletins.services.weather_display import WEATHER_ICON_BUCKETS
from tests.factories import RegionFactory


@pytest.mark.django_db
class TestHeaderCombinationsView:
    """Tests for ``public.debug_views.header_combinations``."""

    @pytest.fixture
    def staff_user(self):
        """A logged-in staff user — required by ``@staff_member_required``."""
        return get_user_model().objects.create_user(
            username="qa",
            password="password",  # noqa: S106 — test fixture, not real creds
            is_staff=True,
        )

    @pytest.fixture
    def staff_client(self, client: Client, staff_user) -> Client:
        """A test client logged in as the staff user."""
        client.force_login(staff_user)
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
    def test_blocked_when_debug_false(self, staff_client: Client) -> None:
        """``_require_debug`` returns 404 even for staff in a non-DEBUG env.

        The URL conf was loaded with DEBUG=True (test env), so the route
        still resolves — but the decorator check at request time blocks
        the response. This is the belt-and-braces guard.
        """
        response = staff_client.get(self._url())
        assert response.status_code == 404

    def test_renders_all_icon_buckets_with_regions(self, staff_client: Client) -> None:
        """Happy path: every icon bucket appears as a section in the context."""
        # At least one Region must exist for the random-region branch to
        # take the non-fallback path.
        RegionFactory.create()

        response = staff_client.get(self._url())

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
        self, staff_client: Client
    ) -> None:
        """Empty Region table → hardcoded fallback context rather than 500."""
        # No RegionFactory — the table is empty for this test.
        response = staff_client.get(self._url())

        assert response.status_code == 200
        assert response.context["region_name"] == "Bex-Villars"
        assert response.context["subregion_name"] == "Vaud Alps"
        # The calendar URL uses the fallback region_id "CH-2223".
        assert "ch-2223" in response.context["calendar_partial_url"].lower()
