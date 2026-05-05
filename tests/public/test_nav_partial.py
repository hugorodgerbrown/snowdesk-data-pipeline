"""
tests/public/test_nav_partial.py — Tests for the nav partial admin dropdown.

Covers:
  - Staff users (is_staff=True) see the admin menu and all three links
    (component library, edit map, Django admin).
  - Non-staff users (is_staff=False) do not see the admin menu element.
  - Anonymous users (AnonymousUser) do not see the admin menu element.

The template is rendered in isolation via render_to_string + RequestFactory
so no database views or URL routing are needed (the three user-type cases
require only the ``db`` fixture for user creation, and the anonymous case
needs no DB at all).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from tests.factories import UserFactory


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a Django RequestFactory."""
    return RequestFactory()


@pytest.fixture()
def staff_user(db):
    """Return a staff Django user."""
    return UserFactory.create(is_staff=True)


@pytest.fixture()
def regular_user(db):
    """Return a non-staff Django user."""
    return UserFactory.create(is_staff=False)


@pytest.mark.django_db
class TestNavAdminMenu:
    """Tests for the staff-only Admin dropdown rendered inside nav.html."""

    def test_staff_sees_admin_links(self, rf: RequestFactory, staff_user) -> None:
        """Staff users see the admin menu and all three destination links."""
        request = rf.get("/")
        request.user = staff_user
        html = render_to_string("includes/nav.html", {}, request=request)
        assert 'id="admin-menu"' in html
        assert reverse("public:components_index") in html
        assert "/map/?edit=resorts" in html
        assert "/admin/" in html

    def test_non_staff_sees_no_admin_menu(
        self, rf: RequestFactory, regular_user
    ) -> None:
        """Non-staff authenticated users do not see the admin menu."""
        request = rf.get("/")
        request.user = regular_user
        html = render_to_string("includes/nav.html", {}, request=request)
        assert 'id="admin-menu"' not in html

    def test_anonymous_sees_no_admin_menu(self, rf: RequestFactory) -> None:
        """Anonymous users do not see the admin menu."""
        request = rf.get("/")
        request.user = AnonymousUser()
        html = render_to_string("includes/nav.html", {}, request=request)
        assert 'id="admin-menu"' not in html
