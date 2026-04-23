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
        run = PipelineRunFactory.create(
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
        assert response["Location"] == reverse("admin:pipeline_bulletin_changelist")

    def test_success_message_shown(self, admin_client: Client) -> None:
        """A successful backfill shows a success message."""
        run = PipelineRunFactory.create(
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
        run = PipelineRunFactory.create(
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
        assert "/login/" in response["Location"]


# ── XSS injection prevention (SNOW-22) ───────────────────────────────────────

_SCRIPT_TAG = "<script>alert('xss_snow22')</script>"


@pytest.mark.django_db
class TestAdminXssEscape:
    """
    Regression tests for SNOW-22: BulletinAdmin HTML table methods must
    escape SLF-provided fields rather than passing them raw through mark_safe.
    """

    def _admin(self) -> BulletinAdmin:
        from django.contrib.admin import site

        from pipeline.models import Bulletin as BulletinModel

        return BulletinAdmin(BulletinModel, site)

    def _bulletin(self, raw_properties: dict):
        from tests.factories import BulletinFactory

        return BulletinFactory.create(raw_data={"properties": raw_properties})

    def test_danger_ratings_escapes_main_value(self) -> None:
        """danger_ratings escapes a <script> injected into mainValue."""
        bulletin = self._bulletin(
            {
                "dangerRatings": [
                    {"mainValue": _SCRIPT_TAG, "validTimePeriod": "all_day"}
                ]
            }
        )
        html = self._admin().danger_ratings(bulletin)
        assert _SCRIPT_TAG not in html
        assert "&lt;script&gt;" in html or "alert" not in html

    def test_avalanche_problems_escapes_comment(self) -> None:
        """avalanche_problems escapes a <script> injected into problem comment."""
        bulletin = self._bulletin(
            {
                "dangerRatings": [],
                "avalancheProblems": [
                    {
                        "problemType": "new_snow",
                        "dangerRatingValue": "low",
                        "validTimePeriod": "all_day",
                        "comment": _SCRIPT_TAG,
                        "aspects": [],
                    }
                ],
            }
        )
        html = self._admin().avalanche_problems(bulletin)
        assert _SCRIPT_TAG not in html

    def test_aggregation_escapes_title(self) -> None:
        """aggregation escapes a <script> injected into the aggregation title."""
        bulletin = self._bulletin(
            {
                "dangerRatings": [],
                "customData": {
                    "CH": {
                        "aggregation": [
                            {
                                "category": "dry",
                                "validTimePeriod": "all_day",
                                "title": _SCRIPT_TAG,
                                "problemTypes": ["new_snow"],
                            }
                        ]
                    }
                },
            }
        )
        html = self._admin().aggregation(bulletin)
        assert _SCRIPT_TAG not in html
