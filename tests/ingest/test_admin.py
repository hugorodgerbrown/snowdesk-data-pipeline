"""
tests/ingest/test_admin.py — Tests for ingest's read-only admins.

SourceBulletinAdmin (SNOW-1250); SourceLocationAdmin and SourceWeatherAdmin
(SNOW-1253); SourcePassAdmin and SourceResortAdmin (SNOW-1271);
SourceDayRatingAdmin (SNOW-1270).

The admin is a read-only window: the list and a row render, and nothing can
be added, changed or deleted.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.ingest.models import (
    SourceBulletin,
    SourceDayRating,
    SourceLocation,
    SourcePass,
    SourceResort,
    SourceWeather,
)
from tests.factories import (
    SourceBulletinFactory,
    SourceDayRatingFactory,
    SourceLocationFactory,
    SourcePassFactory,
    SourceResortFactory,
    SourceWeatherFactory,
)

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


def test_the_weather_service_pages_render(admin_client: Client) -> None:
    """The place and place-day lists and rows answer."""
    row = SourceWeatherFactory.create(location=SourceLocationFactory.create())
    for name, pk in (
        ("sourcelocation", row.location.pk),
        ("sourceweather", row.pk),
    ):
        assert (
            admin_client.get(reverse(f"admin:ingest_{name}_changelist")).status_code
            == 200
        )
        assert (
            admin_client.get(
                reverse(f"admin:ingest_{name}_change", args=[pk])
            ).status_code
            == 200
        )


@pytest.mark.parametrize(
    "model", [SourceLocation, SourceWeather, SourcePass, SourceResort, SourceDayRating]
)
def test_the_weather_service_is_read_only(rf: RequestFactory, model: type) -> None:
    """A place is tombstoned by a sync, a past day stands: no hand edits."""
    model_admin = site._registry[model]
    request = rf.get("/")
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)


def test_the_document_pages_render(admin_client: Client) -> None:
    """The pass, resort and day-rating lists and rows answer."""
    for name, row in (
        ("sourcepass", SourcePassFactory.create()),
        ("sourceresort", SourceResortFactory.create()),
        ("sourcedayrating", SourceDayRatingFactory.create()),
    ):
        assert (
            admin_client.get(reverse(f"admin:ingest_{name}_changelist")).status_code
            == 200
        )
        assert (
            admin_client.get(
                reverse(f"admin:ingest_{name}_change", args=[row.pk])
            ).status_code
            == 200
        )
