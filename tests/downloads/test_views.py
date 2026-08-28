"""
tests/downloads/test_views.py — Tests for apps.downloads.views.

Covers:
  area_sync   — anonymous → 403; non-HTMX → 400; a malformed area id → 400;
                a custom area with a missing/invalid/out-of-range bbox →
                400; a region area with no region_id → 400; a valid region
                and a valid custom area → 200 and a stored row; a REPEAT
                sync of the same area_id updates rather than duplicates
                (the mutation-queue replay guarantee); a region's bbox is
                never stored and a custom area's region_id never is;
                at DOWNLOAD_AREAS_MAX_PER_USER → 409 for a NEW area but
                still 200 for one already held; rate-limited → 429.
  area_rename — owner renames; a region → 400 (its name is its own);
                another user's area_id → 404 (no existence oracle); an
                unknown id → 404; over-long name → 400; anonymous → 403.
  area_forget — owner forgets, and ONLY the account row goes; another
                user's area_id → 404 and their row survives; a repeat call
                → 404; anonymous → 403.
  areas_json  — anonymous → 403 (not an empty list — "signed out" and "no
                areas" are different facts); owner scoping; the serialised
                shape, including that no byte count or band is exposed;
                served without an HX-Request header.

Scoped to the Django test client throughout — per CLAUDE.md's layer rules
there is nothing browser-shaped here, and a 403 needs no browser.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client

from apps.downloads.models import DownloadArea
from tests.factories import DownloadAreaFactory, UserFactory

SYNC_URL = "/downloads/partials/sync/"
AREAS_URL = "/downloads/areas.json"

HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}

# A valid custom-area payload, as static/js/downloads_sync.js posts one.
CUSTOM_PAYLOAD: dict[str, Any] = {
    "area_id": "custom-11111111-2222-3333-4444-555555555555",
    "bbox": json.dumps([7.0, 45.9, 7.3, 46.1]),
    "basemap_key": "outdoor",
    "name": "Verbier bowl",
}

REGION_PAYLOAD: dict[str, Any] = {
    "area_id": "region-ch-4115",
    "region_id": "ch-4115",
    "basemap_key": "outdoor",
}


def _rename_url(area_id: str) -> str:
    """Return the rename URL for an area id."""
    return f"/downloads/partials/{area_id}/rename/"


def _forget_url(area_id: str) -> str:
    """Return the forget URL for an area id."""
    return f"/downloads/partials/{area_id}/forget/"


@pytest.fixture
def signed_in(client: Client) -> Any:
    """Return a user signed in on the shared test client."""
    user = UserFactory.create()
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestAreaSyncGuards:
    """Auth, HTMX and validation guards on area_sync."""

    def test_anonymous_is_refused(self, client: Client) -> None:
        """An anonymous sync gets 403, and stores nothing."""
        response = client.post(SYNC_URL, REGION_PAYLOAD, **HTMX_HEADERS)

        assert response.status_code == 403
        assert not DownloadArea.objects.exists()

    def test_non_htmx_is_refused(self, client: Client, signed_in: Any) -> None:
        """A plain POST with no HX-Request header gets 400."""
        response = client.post(SYNC_URL, REGION_PAYLOAD)

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "area_id",
        [
            "",
            "nonsense",
            "region-",
            "custom-",
            # A path separator would let a caller address a different URL
            # shape; the anchored pattern rejects it.
            "region-../../etc",
            "region-" + "x" * 200,
        ],
    )
    def test_malformed_area_id_is_refused(
        self, client: Client, signed_in: Any, area_id: str
    ) -> None:
        """Only the two client-minted id shapes are accepted."""
        response = client.post(
            SYNC_URL, {**REGION_PAYLOAD, "area_id": area_id}, **HTMX_HEADERS
        )

        assert response.status_code == 400
        assert not DownloadArea.objects.exists()

    @pytest.mark.parametrize(
        "bbox",
        [
            "",
            "not json",
            json.dumps([1, 2, 3]),
            json.dumps(["a", "b", "c", "d"]),
            # Inverted: west must be less than east.
            json.dumps([7.3, 45.9, 7.0, 46.1]),
            # Zero-width: a box with no area downloads no tiles.
            json.dumps([7.0, 45.9, 7.0, 46.1]),
            # Out of range.
            json.dumps([-200.0, 45.9, 7.3, 46.1]),
            json.dumps([7.0, -95.0, 7.3, 46.1]),
        ],
    )
    def test_custom_area_needs_a_valid_bbox(
        self, client: Client, signed_in: Any, bbox: str
    ) -> None:
        """A custom area with no usable box is refused, not stored empty."""
        response = client.post(
            SYNC_URL, {**CUSTOM_PAYLOAD, "bbox": bbox}, **HTMX_HEADERS
        )

        assert response.status_code == 400
        assert not DownloadArea.objects.exists()

    def test_region_area_needs_a_region_id(
        self, client: Client, signed_in: Any
    ) -> None:
        """A region row with nothing naming its region is refused."""
        response = client.post(
            SYNC_URL, {**REGION_PAYLOAD, "region_id": ""}, **HTMX_HEADERS
        )

        assert response.status_code == 400

    def test_rate_limited_branch_returns_429(self) -> None:
        """When request.limited is True, the view returns 429.

        Mirrors ``tests/routes/test_views.py::TestRouteCreateRateLimit`` —
        django-ratelimit ORs a pre-set ``request.limited=True`` with its own
        (unmet) check, so pre-setting it short-circuits into the 429 branch.
        Driving the real limiter with thirty live requests would instead
        make the assertion depend on where the window boundary happens to
        fall, which is a flake, not a test.
        """
        user = UserFactory.create()

        from django.contrib.sessions.backends.db import SessionStore  # noqa: PLC0415
        from django.http import HttpResponse as _HR  # noqa: PLC0415
        from django.test import RequestFactory  # noqa: PLC0415
        from django_htmx.middleware import HtmxMiddleware  # noqa: PLC0415

        rf = RequestFactory()
        request = rf.post(SYNC_URL, REGION_PAYLOAD, HTTP_HX_REQUEST="true")
        request.limited = True  # type: ignore[attr-defined]
        request.user = user
        request.session = SessionStore()

        HtmxMiddleware(lambda r: _HR())(request)

        from apps.downloads.views import area_sync  # noqa: PLC0415

        assert area_sync(request).status_code == 429


@pytest.mark.django_db
class TestAreaSyncWrites:
    """The happy paths and the idempotency guarantee."""

    def test_region_area_is_stored(self, client: Client, signed_in: Any) -> None:
        """A region sync stores the region id and no bbox."""
        response = client.post(SYNC_URL, REGION_PAYLOAD, **HTMX_HEADERS)

        assert response.status_code == 200
        area = DownloadArea.objects.get(area_id="region-ch-4115")
        assert area.kind == DownloadArea.KIND.REGION
        assert area.region_id == "ch-4115"
        # A region's tiles come from its real boundary server-side, so a
        # box would be a second, coarser answer to a settled question.
        assert area.bbox is None

    def test_custom_area_is_stored(self, client: Client, signed_in: Any) -> None:
        """A custom sync stores the bbox and no region id."""
        response = client.post(SYNC_URL, CUSTOM_PAYLOAD, **HTMX_HEADERS)

        assert response.status_code == 200
        area = DownloadArea.objects.get(area_id=CUSTOM_PAYLOAD["area_id"])
        assert area.kind == DownloadArea.KIND.CUSTOM
        assert area.bbox == [7.0, 45.9, 7.3, 46.1]
        assert area.region_id == ""
        assert area.name == "Verbier bowl"

    def test_kind_is_derived_from_the_id_not_the_payload(
        self, client: Client, signed_in: Any
    ) -> None:
        """A posted kind cannot contradict the id's own prefix."""
        response = client.post(
            SYNC_URL, {**CUSTOM_PAYLOAD, "kind": "region"}, **HTMX_HEADERS
        )

        assert response.status_code == 200
        area = DownloadArea.objects.get(area_id=CUSTOM_PAYLOAD["area_id"])
        assert area.kind == DownloadArea.KIND.CUSTOM

    def test_repeat_sync_updates_rather_than_duplicates(
        self, client: Client, signed_in: Any
    ) -> None:
        """A mutation-queue replay must not create a second row."""
        client.post(SYNC_URL, CUSTOM_PAYLOAD, **HTMX_HEADERS)
        client.post(SYNC_URL, {**CUSTOM_PAYLOAD, "name": "Renamed"}, **HTMX_HEADERS)

        areas = DownloadArea.objects.filter(area_id=CUSTOM_PAYLOAD["area_id"])
        assert areas.count() == 1
        assert areas.get().name == "Renamed"

    def test_cap_refuses_a_new_area(
        self, client: Client, signed_in: Any, settings: Any
    ) -> None:
        """At the per-user cap, a new area is refused with 409."""
        settings.DOWNLOAD_AREAS_MAX_PER_USER = 1
        DownloadAreaFactory.create(
            user=signed_in, area_id="region-ch-1", region_id="ch-1"
        )

        response = client.post(SYNC_URL, REGION_PAYLOAD, **HTMX_HEADERS)

        assert response.status_code == 409

    def test_cap_still_allows_updating_an_area_already_held(
        self, client: Client, signed_in: Any, settings: Any
    ) -> None:
        """Re-downloading something you already have must never be blocked."""
        settings.DOWNLOAD_AREAS_MAX_PER_USER = 1
        DownloadAreaFactory.create(
            user=signed_in, area_id="region-ch-4115", region_id="ch-4115"
        )

        response = client.post(SYNC_URL, REGION_PAYLOAD, **HTMX_HEADERS)

        assert response.status_code == 200


@pytest.mark.django_db
class TestAreaRename:
    """area_rename coverage."""

    def test_owner_renames_a_custom_area(self, client: Client, signed_in: Any) -> None:
        """The owner's rename is stored."""
        area = DownloadAreaFactory.create(
            user=signed_in,
            area_id="custom-abc",
            kind=DownloadArea.KIND.CUSTOM,
            region_id="",
            bbox=[7.0, 45.9, 7.3, 46.1],
        )

        response = client.post(
            _rename_url(area.area_id), {"name": "Home valley"}, **HTMX_HEADERS
        )

        assert response.status_code == 200
        area.refresh_from_db()
        assert area.name == "Home valley"

    def test_a_region_cannot_be_renamed(self, client: Client, signed_in: Any) -> None:
        """A region's name is the region's own, so a rename is refused."""
        area = DownloadAreaFactory.create(user=signed_in)

        response = client.post(
            _rename_url(area.area_id), {"name": "Nope"}, **HTMX_HEADERS
        )

        assert response.status_code == 400
        area.refresh_from_db()
        assert area.name == ""

    def test_another_users_area_is_not_found(
        self, client: Client, signed_in: Any
    ) -> None:
        """404, not 403 — no existence oracle for somebody else's areas."""
        theirs = DownloadAreaFactory.create(
            area_id="custom-abc",
            kind=DownloadArea.KIND.CUSTOM,
            region_id="",
            bbox=[7.0, 45.9, 7.3, 46.1],
        )

        response = client.post(
            _rename_url(theirs.area_id), {"name": "Mine now"}, **HTMX_HEADERS
        )

        assert response.status_code == 404
        theirs.refresh_from_db()
        assert theirs.name == ""

    def test_over_long_name_is_refused(self, client: Client, signed_in: Any) -> None:
        """A name past the column's max_length is rejected, not truncated."""
        area = DownloadAreaFactory.create(
            user=signed_in,
            area_id="custom-abc",
            kind=DownloadArea.KIND.CUSTOM,
            region_id="",
            bbox=[7.0, 45.9, 7.3, 46.1],
        )

        response = client.post(
            _rename_url(area.area_id), {"name": "x" * 101}, **HTMX_HEADERS
        )

        assert response.status_code == 400

    def test_anonymous_is_refused(self, client: Client) -> None:
        """An anonymous rename gets 403."""
        area = DownloadAreaFactory.create(
            area_id="custom-abc",
            kind=DownloadArea.KIND.CUSTOM,
            region_id="",
            bbox=[7.0, 45.9, 7.3, 46.1],
        )

        response = client.post(
            _rename_url(area.area_id), {"name": "Nope"}, **HTMX_HEADERS
        )

        assert response.status_code == 403


@pytest.mark.django_db
class TestAreaForget:
    """area_forget coverage."""

    def test_owner_forgets_their_area(self, client: Client, signed_in: Any) -> None:
        """The account row goes."""
        area = DownloadAreaFactory.create(user=signed_in)

        response = client.post(_forget_url(area.area_id), **HTMX_HEADERS)

        assert response.status_code == 204
        assert not DownloadArea.objects.filter(pk=area.pk).exists()

    def test_forgetting_leaves_other_areas_alone(
        self, client: Client, signed_in: Any
    ) -> None:
        """Only the named area is dropped."""
        keep = DownloadAreaFactory.create(user=signed_in)
        drop = DownloadAreaFactory.create(user=signed_in)

        client.post(_forget_url(drop.area_id), **HTMX_HEADERS)

        assert list(DownloadArea.objects.for_user(signed_in)) == [keep]

    def test_another_users_area_survives(self, client: Client, signed_in: Any) -> None:
        """404, and their row is untouched."""
        theirs = DownloadAreaFactory.create()

        response = client.post(_forget_url(theirs.area_id), **HTMX_HEADERS)

        assert response.status_code == 404
        assert DownloadArea.objects.filter(pk=theirs.pk).exists()

    def test_repeat_forget_is_not_found(self, client: Client, signed_in: Any) -> None:
        """A second call has nothing to delete."""
        area = DownloadAreaFactory.create(user=signed_in)
        client.post(_forget_url(area.area_id), **HTMX_HEADERS)

        response = client.post(_forget_url(area.area_id), **HTMX_HEADERS)

        assert response.status_code == 404

    def test_anonymous_is_refused(self, client: Client) -> None:
        """An anonymous forget gets 403, and the row survives."""
        area = DownloadAreaFactory.create()

        response = client.post(_forget_url(area.area_id), **HTMX_HEADERS)

        assert response.status_code == 403
        assert DownloadArea.objects.filter(pk=area.pk).exists()


@pytest.mark.django_db
class TestAreasJson:
    """areas_json coverage."""

    def test_anonymous_is_refused(self, client: Client) -> None:
        """403, not an empty list — the two are different facts."""
        response = client.get(AREAS_URL)

        assert response.status_code == 403

    def test_served_without_an_htmx_header(
        self, client: Client, signed_in: Any
    ) -> None:
        """It is read by a fetch(), not an HTMX swap, so no header is needed."""
        response = client.get(AREAS_URL)

        assert response.status_code == 200

    def test_returns_only_the_owners_areas(
        self, client: Client, signed_in: Any
    ) -> None:
        """Another user's areas never appear."""
        mine = DownloadAreaFactory.create(user=signed_in)
        DownloadAreaFactory.create()

        payload = client.get(AREAS_URL).json()

        assert [area["area_id"] for area in payload["areas"]] == [mine.area_id]

    def test_serialised_shape(self, client: Client, signed_in: Any) -> None:
        """Every field the client reads is present, and nothing device-local is."""
        DownloadAreaFactory.create(
            user=signed_in,
            area_id="custom-abc",
            kind=DownloadArea.KIND.CUSTOM,
            region_id="",
            bbox=[7.0, 45.9, 7.3, 46.1],
            name="Verbier bowl",
        )

        area = client.get(AREAS_URL).json()["areas"][0]

        assert area["area_id"] == "custom-abc"
        assert area["kind"] == "custom"
        assert area["bbox"] == [7.0, 45.9, 7.3, 46.1]
        assert area["name"] == "Verbier bowl"
        assert area["basemap_key"] == "outdoor"
        assert "created_at" in area
        # Never synced — see apps/downloads/models.py for why each is
        # absent. A client that found one here would be reading another
        # device's measurement as its own.
        assert "bytes" not in area
        assert "band" not in area
        assert "template" not in area
