"""
tests/bulletins/test_weather_snapshot_admin.py — Tests for WeatherSnapshotAdmin.

Covers the "Fetch today's weather" button:
  - Button renders on the changelist page.
  - A POST calls fetch_all_regions and shows a success message.
  - A POST with failures shows a warning message instead.
  - A GET to the fetch URL is rejected (405 redirect).
  - An anonymous user is redirected to the admin login page.
"""

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests.factories import UserFactory

FETCH_URL = reverse("admin:bulletins_weathersnapshot_fetch_today")
CHANGELIST_URL = reverse("admin:bulletins_weathersnapshot_changelist")


@pytest.fixture()
def staff_client() -> Client:
    """Return a Django test client logged in as a staff user."""
    user = UserFactory.create(is_superuser=True)
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestFetchTodayButton:
    """The changelist renders the Fetch today's weather button."""

    def test_fetch_today_button_renders_on_changelist(
        self, staff_client: Client
    ) -> None:
        """Staff GET on the changelist returns 200 and includes the button."""
        response = staff_client.get(CHANGELIST_URL)
        assert response.status_code == 200
        assert b'data-testid="weathersnapshot-fetch-today"' in response.content


@pytest.mark.django_db
class TestFetchTodayPost:
    """POST to the fetch-today URL calls fetch_all_regions and shows messages."""

    def test_post_calls_fetcher_and_shows_success_message(
        self, staff_client: Client
    ) -> None:
        """A successful POST calls fetch_all_regions with today's date and commit=True."""
        counts = {"created": 3, "updated": 1, "skipped": 0, "failed": 0}
        with patch("bulletins.admin.fetch_all_regions", return_value=counts) as mock_fn:
            response = staff_client.post(FETCH_URL, follow=True)

        mock_fn.assert_called_once_with(timezone.localdate(), commit=True)
        assert response.status_code == 200

        all_messages = list(response.context["messages"])
        assert len(all_messages) == 1
        msg = str(all_messages[0])
        assert "3 created" in msg
        assert "1 updated" in msg
        assert "0 failed" in msg

    def test_post_with_failures_shows_warning(self, staff_client: Client) -> None:
        """When failed > 0, a warning-level message is shown instead of success."""
        from django.contrib.messages import WARNING

        counts = {"created": 5, "updated": 0, "skipped": 1, "failed": 2}
        with patch("bulletins.admin.fetch_all_regions", return_value=counts):
            response = staff_client.post(FETCH_URL, follow=True)

        all_messages = list(response.context["messages"])
        assert len(all_messages) == 1
        assert all_messages[0].level == WARNING
        assert "2 failed" in str(all_messages[0])

    def test_post_exception_shows_error_message(self, staff_client: Client) -> None:
        """An exception from fetch_all_regions surfaces as an admin error message."""
        from django.contrib.messages import ERROR

        with patch(
            "bulletins.admin.fetch_all_regions", side_effect=RuntimeError("boom")
        ):
            response = staff_client.post(FETCH_URL, follow=True)

        all_messages = list(response.context["messages"])
        assert len(all_messages) == 1
        assert all_messages[0].level == ERROR

    def test_post_redirects_to_changelist(self, staff_client: Client) -> None:
        """A successful POST redirects to the WeatherSnapshot changelist."""
        counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        with patch("bulletins.admin.fetch_all_regions", return_value=counts):
            response = staff_client.post(FETCH_URL)

        assert response.status_code == 302
        assert response["Location"] == CHANGELIST_URL


@pytest.mark.django_db
class TestFetchTodayGet:
    """GET requests to the fetch-today URL are rejected."""

    def test_get_is_rejected(self, staff_client: Client) -> None:
        """A GET to the fetch URL does not call fetch_all_regions and redirects."""
        with patch("bulletins.admin.fetch_all_regions") as mock_fn:
            response = staff_client.get(FETCH_URL)

        mock_fn.assert_not_called()
        # admin_view wraps GET in a redirect rather than 405, which is acceptable
        assert response.status_code in (302, 405)


@pytest.mark.django_db
class TestFetchTodayAuth:
    """Anonymous users are redirected to the admin login page."""

    def test_requires_staff(self) -> None:
        """An anonymous POST is redirected to /admin/login/."""
        client = Client()
        response = client.post(FETCH_URL)
        assert response.status_code == 302
        assert "/login/" in response["Location"]
