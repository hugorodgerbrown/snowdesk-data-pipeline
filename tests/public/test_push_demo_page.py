"""
tests/public/test_push_demo_page.py — Tests for the /_push-demo/ staff page.

Covers:
  - Anonymous user is redirected to /admin/login/.
  - Non-staff user is also redirected.
  - Staff user gets 200 with the VAPID public-key meta tag present.
  - Staff user response references push_demo.js.

Mirrors tests/public/test_debug_views.py fixture style.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from subscriptions.models import Subscriber
from tests.factories import SubscriberFactory, UserFactory

_PUSH_DEMO_URL = "/_push-demo/"


@pytest.fixture()
def staff_user(db: Any) -> Subscriber:
    """Return a staff Subscriber."""
    return UserFactory.create()


@pytest.fixture()
def regular_user(db: Any) -> Subscriber:
    """Return a non-staff Subscriber."""
    return SubscriberFactory.create()


@pytest.fixture()
def staff_client(staff_user: Subscriber) -> Client:
    """Return a Django test Client logged in as a staff user."""
    c = Client()
    c.force_login(staff_user)
    return c


@pytest.mark.django_db
class TestPushDemoPage:
    """Tests for the /_push-demo/ view."""

    def test_anonymous_redirected_to_admin_login(self) -> None:
        """A logged-out user is bounced to the admin login page."""
        response = Client().get(_PUSH_DEMO_URL)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_redirected_to_admin_login(
        self, regular_user: Subscriber
    ) -> None:
        """A logged-in non-staff user is also bounced to admin login."""
        c = Client()
        c.force_login(regular_user)
        response = c.get(_PUSH_DEMO_URL)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_staff_sees_page(self, staff_client: Client) -> None:
        """Staff user gets a 200 response."""
        response = staff_client.get(_PUSH_DEMO_URL)
        assert response.status_code == 200

    def test_staff_response_contains_vapid_meta_tag(self, staff_client: Client) -> None:
        """The rendered page contains the vapid-public-key meta tag."""
        response = staff_client.get(_PUSH_DEMO_URL)
        body = response.content.decode()
        assert 'name="vapid-public-key"' in body

    def test_staff_response_references_push_demo_js(self, staff_client: Client) -> None:
        """The rendered page includes a script tag pointing at push_demo.js."""
        response = staff_client.get(_PUSH_DEMO_URL)
        body = response.content.decode()
        assert "push_demo.js" in body

    def test_url_resolves_to_push_demo_name(self) -> None:
        """The named URL 'public:push_demo' resolves to /_push-demo/."""
        assert reverse("public:push_demo") == _PUSH_DEMO_URL
