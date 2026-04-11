"""
tests/pipeline/test_admin.py — Tests for the BulletinAdmin backfill view.

Verifies that the backfill button triggers a pipeline run, shows
appropriate success/error messages, and rejects non-POST requests.
"""

from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from pipeline.admin import BulletinAdmin
from pipeline.models import PipelineRun
from tests.factories import PipelineRunFactory


@pytest.fixture()
def admin_client() -> Client:
    """Return a Django test client logged in as a superuser."""
    user = User.objects.create_superuser("admin", "admin@test.com", "password")
    client = Client()
    client.force_login(user)
    return client


BACKFILL_URL = reverse("admin:pipeline_bulletin_backfill")


@pytest.mark.django_db
class TestBackfillView:
    """Tests for BulletinAdmin.backfill_view."""

    def test_post_triggers_pipeline_and_redirects(self, admin_client: Client) -> None:
        """A POST to the backfill URL runs the pipeline and redirects."""
        run = PipelineRunFactory(
            status=PipelineRun.Status.SUCCESS,
            records_created=10,
            records_updated=2,
        )
        with patch("pipeline.admin.run_pipeline", return_value=run) as mock_run:
            response = admin_client.post(BACKFILL_URL)

        mock_run.assert_called_once_with(
            start=BulletinAdmin.BACKFILL_START,
            end=date.today(),
            triggered_by="admin backfill",
            dry_run=False,
            force=False,
        )
        assert response.status_code == 302
        assert response.url == reverse("admin:pipeline_bulletin_changelist")

    def test_success_message_shown(self, admin_client: Client) -> None:
        """A successful backfill shows a success message."""
        run = PipelineRunFactory(
            status=PipelineRun.Status.SUCCESS,
            records_created=5,
            records_updated=1,
        )
        with patch("pipeline.admin.run_pipeline", return_value=run):
            response = admin_client.post(BACKFILL_URL, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "5 created" in str(messages[0])
        assert "1 updated" in str(messages[0])

    def test_failed_run_shows_error(self, admin_client: Client) -> None:
        """A failed pipeline run shows an error message."""
        run = PipelineRunFactory(
            status=PipelineRun.Status.FAILED,
            error_message="API timeout",
        )
        with patch("pipeline.admin.run_pipeline", return_value=run):
            response = admin_client.post(BACKFILL_URL, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "failed" in str(messages[0]).lower()

    def test_exception_shows_error(self, admin_client: Client) -> None:
        """An exception during the pipeline shows an error message."""
        with patch("pipeline.admin.run_pipeline", side_effect=RuntimeError("boom")):
            response = admin_client.post(BACKFILL_URL, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "failed" in str(messages[0]).lower()

    def test_get_request_rejected(self, admin_client: Client) -> None:
        """A GET request to the backfill URL is rejected."""
        response = admin_client.get(BACKFILL_URL)
        assert response.status_code == 302

    def test_unauthenticated_user_redirected(self) -> None:
        """An unauthenticated user is redirected to the login page."""
        client = Client()
        response = client.post(BACKFILL_URL)
        assert response.status_code == 302
        assert "/login/" in response.url
