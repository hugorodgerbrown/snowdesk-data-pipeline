"""
tests/ingest/test_admin.py — Tests for SourceBulletinAdmin (SNOW-1250).

The admin is a read-only window: the list and a row render, and nothing can
be added, changed or deleted.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.ingest.models import SourceBulletin
from tests.factories import SourceBulletinFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client: Client) -> Client:
    """A client signed in as a superuser."""
    client.force_login(User.objects.create_superuser("root", "root@example.org", "x"))
    return client


def test_the_list_and_a_row_render(admin_client: Client) -> None:
    """Both pages answer, and the row shows its CAAML pretty-printed."""
    row = SourceBulletinFactory.create(caaml={"bulletinID": "<b>"})
    listing = admin_client.get(reverse("admin:ingest_sourcebulletin_changelist"))
    assert listing.status_code == 200
    detail = admin_client.get(
        reverse("admin:ingest_sourcebulletin_change", args=[row.pk])
    )
    assert detail.status_code == 200
    assert "&lt;b&gt;" in detail.content.decode()


def test_nothing_can_be_added_changed_or_deleted(rf: RequestFactory) -> None:
    """A row is the record of what a provider said."""
    model_admin = site._registry[SourceBulletin]
    request = rf.get("/")
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
