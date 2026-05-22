# tests/bulletins/test_mf_archive_upload_admin.py — Tests for the MF archive
# upload view on PipelineRunAdmin.
#
# Covers:
#   - Happy-path POST with a mocked loader → success message + redirect.
#   - POST with failed rows → WARNING level message.
#   - POST with unknown slugs → WARNING level message.
#   - POST with no file → ERROR level message, loader not called.
#   - GET request → redirect to changelist, loader not called.
#   - Non-staff user → redirect to admin login.
"""Tests for the Météo-France archive upload view on PipelineRunAdmin."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.messages import ERROR, SUCCESS, WARNING
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from bulletins.services.mf_archive_loader import LoadResult
from tests.factories import UserFactory

UPLOAD_URL = reverse("admin:bulletins_pipelinerun_upload_mf_archive")
CHANGELIST_URL = reverse("admin:bulletins_pipelinerun_changelist")

# A minimal valid NDJSON line used as the upload body in happy-path tests.
_SAMPLE_NDJSON = (
    b'{"type":"Feature","geometry":null,"properties":{'
    b'"lang":"fr","regions":[{"regionID":"FR-ARAVIS"}],'
    b'"validTime":{"startTime":"2026-01-15T00:00:00+00:00",'
    b'"endTime":"2026-01-15T23:59:59+00:00"},'
    b'"customData":{"MF":{"massif":"ARAVIS","date":"2026-01-15"}},'
    b'"dangerRatings":[],"avalancheProblems":[]}}\n'
)

_CLEAN_RESULT = LoadResult(
    total=1,
    bad_shape=0,
    unknown_slug=0,
    would_upsert=0,
    created=1,
    updated=0,
    failed=0,
    pipeline_run_id=1,
)


@pytest.fixture()
def staff_client() -> Client:
    """Return a Django test client logged in as a superuser."""
    user = UserFactory.create(is_superuser=True)
    client = Client()
    client.force_login(user)
    return client


def _upload_file(
    name: str = "bulletins.ndjson", content: bytes = _SAMPLE_NDJSON
) -> SimpleUploadedFile:
    """Build a SimpleUploadedFile for the 'archive' field.

    Args:
        name: Filename reported to Django.
        content: Raw bytes of the upload body.

    Returns:
        A ``SimpleUploadedFile`` ready to pass in ``data={"archive": ...}``.

    """
    return SimpleUploadedFile(name, content, content_type="application/octet-stream")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadHappyPath:
    """POST with a valid archive calls the loader and shows a success message."""

    def test_calls_loader_with_commit_true(self, staff_client: Client) -> None:
        """load_mf_archive is called with commit=True and triggered_by='admin upload'."""
        with patch(
            "bulletins.admin.load_mf_archive", return_value=_CLEAN_RESULT
        ) as mock_loader:
            staff_client.post(UPLOAD_URL, data={"archive": _upload_file()})

        mock_loader.assert_called_once()
        _args, kwargs = mock_loader.call_args
        assert kwargs["commit"] is True
        assert kwargs["triggered_by"] == "admin upload"

    def test_redirects_to_changelist(self, staff_client: Client) -> None:
        """A successful POST redirects (302) to the PipelineRun changelist."""
        with patch("bulletins.admin.load_mf_archive", return_value=_CLEAN_RESULT):
            response = staff_client.post(UPLOAD_URL, data={"archive": _upload_file()})

        assert response.status_code == 302
        assert response["Location"] == CHANGELIST_URL

    def test_success_message_level(self, staff_client: Client) -> None:
        """A clean result produces a SUCCESS-level admin message."""
        with patch("bulletins.admin.load_mf_archive", return_value=_CLEAN_RESULT):
            response = staff_client.post(
                UPLOAD_URL, data={"archive": _upload_file()}, follow=True
            )

        all_messages = list(response.context["messages"])
        assert len(all_messages) == 1
        assert all_messages[0].level == SUCCESS

    def test_success_message_contains_summary(self, staff_client: Client) -> None:
        """The admin message body includes the loader summary text."""
        with patch("bulletins.admin.load_mf_archive", return_value=_CLEAN_RESULT):
            response = staff_client.post(
                UPLOAD_URL, data={"archive": _upload_file()}, follow=True
            )

        msg = str(list(response.context["messages"])[0])
        assert "1 created" in msg


# ---------------------------------------------------------------------------
# Warning path — failed or unknown_slug rows
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadWarningPath:
    """POST with partial failures shows a WARNING-level message."""

    def test_failed_rows_show_warning(self, staff_client: Client) -> None:
        """LoadResult.failed > 0 produces a WARNING-level message."""
        result = LoadResult(
            total=5,
            bad_shape=0,
            unknown_slug=0,
            would_upsert=0,
            created=4,
            updated=0,
            failed=1,
            pipeline_run_id=2,
        )
        with patch("bulletins.admin.load_mf_archive", return_value=result):
            response = staff_client.post(
                UPLOAD_URL, data={"archive": _upload_file()}, follow=True
            )

        all_messages = list(response.context["messages"])
        assert all_messages[0].level == WARNING

    def test_unknown_slugs_show_warning(self, staff_client: Client) -> None:
        """LoadResult.unknown_slug > 0 produces a WARNING-level message."""
        result = LoadResult(
            total=3,
            bad_shape=0,
            unknown_slug=2,
            would_upsert=0,
            created=1,
            updated=0,
            failed=2,
            pipeline_run_id=3,
        )
        with patch("bulletins.admin.load_mf_archive", return_value=result):
            response = staff_client.post(
                UPLOAD_URL, data={"archive": _upload_file()}, follow=True
            )

        all_messages = list(response.context["messages"])
        assert all_messages[0].level == WARNING


# ---------------------------------------------------------------------------
# Error path — no file provided
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadErrorPath:
    """POST without a file or with an exception shows an ERROR-level message."""

    def test_missing_file_shows_error(self, staff_client: Client) -> None:
        """A POST with no 'archive' field produces an ERROR-level message."""
        with patch("bulletins.admin.load_mf_archive") as mock_loader:
            response = staff_client.post(UPLOAD_URL, data={}, follow=True)

        mock_loader.assert_not_called()
        all_messages = list(response.context["messages"])
        assert len(all_messages) == 1
        assert all_messages[0].level == ERROR

    def test_loader_exception_shows_error(self, staff_client: Client) -> None:
        """An unexpected exception from load_mf_archive surfaces as ERROR."""
        with patch("bulletins.admin.load_mf_archive", side_effect=RuntimeError("boom")):
            response = staff_client.post(
                UPLOAD_URL, data={"archive": _upload_file()}, follow=True
            )

        all_messages = list(response.context["messages"])
        assert all_messages[0].level == ERROR


# ---------------------------------------------------------------------------
# GET request
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadGetRequest:
    """GET requests are redirected to the changelist without calling the loader."""

    def test_get_redirects_to_changelist(self, staff_client: Client) -> None:
        """A GET to the upload URL redirects to the changelist."""
        with patch("bulletins.admin.load_mf_archive") as mock_loader:
            response = staff_client.get(UPLOAD_URL)

        mock_loader.assert_not_called()
        assert response.status_code == 302
        assert response["Location"] == CHANGELIST_URL


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadAuth:
    """Non-staff users are redirected to the admin login page."""

    def test_anonymous_user_redirected_to_login(self) -> None:
        """An anonymous POST is redirected to /admin/login/."""
        client = Client()
        response = client.post(UPLOAD_URL, data={"archive": _upload_file()})
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_non_staff_user_redirected_to_login(self) -> None:
        """A non-staff POST is redirected to /admin/login/."""
        user = UserFactory.create(is_staff=False, is_superuser=False)
        client = Client()
        client.force_login(user)
        response = client.post(UPLOAD_URL, data={"archive": _upload_file()})
        assert response.status_code == 302
        assert "/login/" in response["Location"]
