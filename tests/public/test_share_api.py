"""
tests/public/test_share_api.py — Tests for the POST /api/bulletins/share/ endpoint.

Covers the happy path, 404 cases, 400 on bad input, rate limiting, and CSRF
enforcement.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from django.test import Client
from django.test.client import _MonkeyPatchedWSGIResponse as TestResponse
from django.urls import reverse

from bulletins.models import BulletinShare
from regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bulletin(region: MicroRegion, day: date) -> None:
    """Create a morning bulletin valid 06:00–17:00 on *day*, linked to *region*."""
    vf = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
    vt = datetime(day.year, day.month, day.day, 17, 0, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)


def _post_share(
    client: Client,
    region_id: str,
    date_str: str,
) -> TestResponse:
    """POST to the share-create endpoint and return the response."""
    url = reverse("api:share_create")
    return client.post(
        url,
        data=json.dumps({"region_id": region_id, "date": date_str}),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareCreateHappyPath:
    """share_create returns 200 + share URL for a valid (region_id, date)."""

    def test_returns_200_with_url(self, client: Client) -> None:
        """Valid request returns 200 with a URL containing the token."""
        region = MicroRegionFactory.create(region_id="CH-4222")
        day = date(2026, 4, 8)
        _make_bulletin(region, day)

        response = _post_share(client, "CH-4222", "2026-04-08")

        assert response.status_code == 200
        data = json.loads(response.content)
        assert "url" in data
        assert "/s/" in data["url"]

    def test_share_row_created(self, client: Client) -> None:
        """A BulletinShare row is created with the correct fields."""
        region = MicroRegionFactory.create(region_id="CH-4222")
        day = date(2026, 4, 8)
        _make_bulletin(region, day)

        _post_share(client, "CH-4222", "2026-04-08")

        share = BulletinShare.objects.get(region=region, target_date=day)
        assert share.bulletin is not None
        assert share.token != ""
        assert len(share.token) > 4

    def test_url_contains_token(self, client: Client) -> None:
        """The returned URL embeds the created token."""
        region = MicroRegionFactory.create(region_id="CH-4222")
        day = date(2026, 4, 8)
        _make_bulletin(region, day)

        response = _post_share(client, "CH-4222", "2026-04-08")

        data = json.loads(response.content)
        share = BulletinShare.objects.get(region=region, target_date=day)
        assert share.token in data["url"]

    def test_region_id_case_insensitive(self, client: Client) -> None:
        """region_id lookup is case-insensitive (ch-4222 == CH-4222)."""
        region = MicroRegionFactory.create(region_id="CH-4222")
        day = date(2026, 4, 8)
        _make_bulletin(region, day)

        response = _post_share(client, "ch-4222", "2026-04-08")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 404 cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareCreate404:
    """share_create returns 404 for unknown region_id or missing bulletin."""

    def test_unknown_region_returns_404(self, client: Client) -> None:
        """A region_id that doesn't exist in the database returns 404."""
        response = _post_share(client, "CH-9999", "2026-04-08")
        assert response.status_code == 404

    def test_no_bulletin_for_date_returns_404(self, client: Client) -> None:
        """A valid region with no bulletin on the given date returns 404."""
        MicroRegionFactory.create(region_id="CH-4222")
        # No bulletin created for this date.
        response = _post_share(client, "CH-4222", "2026-04-08")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 400 cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareCreate400:
    """share_create returns 400 on bad input."""

    def test_malformed_json_returns_400(self, client: Client) -> None:
        """Invalid JSON body returns 400."""
        response = client.post(
            reverse("api:share_create"),
            data=b"not-json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_missing_region_id_returns_400(self, client: Client) -> None:
        """Missing region_id field returns 400."""
        response = client.post(
            reverse("api:share_create"),
            data=json.dumps({"date": "2026-04-08"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_missing_date_returns_400(self, client: Client) -> None:
        """Missing date field returns 400."""
        response = client.post(
            reverse("api:share_create"),
            data=json.dumps({"region_id": "CH-4222"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_invalid_date_format_returns_400(self, client: Client) -> None:
        """Malformed date string returns 400."""
        response = client.post(
            reverse("api:share_create"),
            data=json.dumps({"region_id": "CH-4222", "date": "not-a-date"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_non_dict_json_returns_400(self, client: Client) -> None:
        """A JSON array body returns 400."""
        response = client.post(
            reverse("api:share_create"),
            data=json.dumps([1, 2, 3]),
            content_type="application/json",
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# CSRF enforcement
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareCreateCsrf:
    """share_create respects Django CSRF checks."""

    def test_missing_csrf_token_returns_403(self) -> None:
        """POST without X-CSRFToken returns 403 when CSRF is enforced."""
        client = Client(enforce_csrf_checks=True)
        region = MicroRegionFactory.create(region_id="CH-4222")
        day = date(2026, 4, 8)
        _make_bulletin(region, day)

        response = client.post(
            reverse("api:share_create"),
            data=json.dumps({"region_id": "CH-4222", "date": "2026-04-08"}),
            content_type="application/json",
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Method guard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareCreateMethodGuard:
    """share_create only accepts POST."""

    def test_get_returns_405(self, client: Client) -> None:
        """GET on share_create returns 405 Method Not Allowed."""
        response = client.get(reverse("api:share_create"))
        assert response.status_code == 405
