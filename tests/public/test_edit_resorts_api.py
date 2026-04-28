"""
tests/public/test_edit_resorts_api.py — SNOW-74 in-map resort editor.

Covers the three endpoints introduced for the ``?edit=resorts`` mode:

* ``api:resorts_geojson``           — always available; only geocoded rows.
* ``api:edit_resorts_queue``        — DEBUG-only; queue + catalogue payload.
* ``api:edit_resort_save_coords``   — DEBUG-only; persists clicked lat/lon.

The DEBUG guard is asserted with ``@override_settings(DEBUG=False)`` for
both edit-mode endpoints. The ``resorts_geojson`` endpoint is not gated.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from tests.factories import RegionFactory, ResortFactory

# ---------------------------------------------------------------------------
# resorts_geojson
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResortsGeojson:
    """Tests for ``GET /api/resorts.geojson``."""

    def test_returns_only_geocoded_resorts(self) -> None:
        """Resorts missing either coord are excluded from the response."""
        ResortFactory.create(name="Unset")
        ResortFactory.create(name="OnlyLat", latitude=46.0)
        geocoded = ResortFactory.create(
            name="Both",
            latitude=46.5,
            longitude=7.5,
        )

        client = Client()
        resp = client.get(reverse("api:resorts_geojson"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 1
        feature = body["features"][0]
        # GeoJSON ordering: [longitude, latitude].
        assert feature["geometry"]["coordinates"] == [7.5, 46.5]
        assert feature["properties"]["id"] == geocoded.pk
        assert feature["properties"]["name"] == "Both"
        assert feature["properties"]["region_id"] == geocoded.region.region_id
        assert feature["properties"]["needs_review"] is False

    @override_settings(DEBUG=False)
    def test_works_with_debug_off(self) -> None:
        """resorts_geojson is not DEBUG-gated."""
        ResortFactory.create(name="A", latitude=46.0, longitude=7.0)
        client = Client()
        resp = client.get(reverse("api:resorts_geojson"))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# edit_resorts_queue (DEBUG-only)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEditResortsQueue:
    """Tests for ``GET /api/edit/resorts/queue/`` (DEBUG-only)."""

    def test_queue_contains_only_unset_or_review(self) -> None:
        """Queue includes unset rows + review-flagged rows; excludes clean."""
        # Explicit region_ids so the order is deterministic. Queue is
        # sorted by ``region__region_id ASC, name ASC``.
        region_a = RegionFactory.create(region_id="CH-1100")
        region_b = RegionFactory.create(region_id="CH-2200")
        unset = ResortFactory.create(name="bbb", region=region_b)
        flagged = ResortFactory.create(
            name="aaa",
            region=region_a,
            latitude=46.0,
            longitude=7.0,
            needs_review=True,
        )
        ResortFactory.create(  # Clean — should NOT appear in queue.
            name="ccc",
            region=region_a,
            latitude=46.5,
            longitude=7.5,
            needs_review=False,
        )

        client = Client()
        resp = client.get(reverse("api:edit_resorts_queue"))
        assert resp.status_code == 200
        body = resp.json()
        ids = [entry["id"] for entry in body["queue"]]
        # Order: region_id ASC, name ASC — flagged is in CH-1100 (first),
        # unset is in CH-2200 (second). needs_review is no longer a sort
        # key; it remains a visual flag (rendered with ⚠ in the panel).
        assert ids == [flagged.pk, unset.pk]

    def test_all_resorts_includes_clean_geocoded(self) -> None:
        """Catalogue includes every resort; ``has_coords`` reflects reality."""
        clean = ResortFactory.create(name="A", latitude=46.0, longitude=7.0)
        unset = ResortFactory.create(name="B")
        client = Client()
        resp = client.get(reverse("api:edit_resorts_queue"))
        body = resp.json()
        catalogue = {entry["id"]: entry for entry in body["all_resorts"]}
        assert catalogue[clean.pk]["has_coords"] is True
        assert catalogue[unset.pk]["has_coords"] is False


@pytest.mark.django_db
class TestEditResortsQueueDebugGate:
    """The queue endpoint must 404 in production (DEBUG=False)."""

    @override_settings(DEBUG=False)
    def test_returns_404_when_debug_off(self) -> None:
        """Edit-mode endpoints are DEBUG-only at the URL and view layers."""
        client = Client()
        resp = client.get("/api/edit/resorts/queue/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# edit_resort_save_coords (DEBUG-only)
# ---------------------------------------------------------------------------


def _post_coords(client: Client, resort_id: int, **body: object):
    """Helper — POST JSON to the save endpoint."""
    return client.post(
        reverse("api:edit_resort_save_coords", args=[resort_id]),
        data=json.dumps(body),
        content_type="application/json",
    )


@pytest.mark.django_db
class TestEditResortSaveCoords:
    """Tests for ``POST /api/edit/resorts/<id>/coords/`` (DEBUG-only)."""

    def test_happy_path_writes_all_fields(self) -> None:
        """A valid POST sets coords + provenance + clears needs_review."""
        resort = ResortFactory.create(name="A", needs_review=True)
        client = Client()
        resp = _post_coords(client, resort.pk, latitude=46.0961, longitude=7.2275)
        assert resp.status_code == 200, resp.content

        resort.refresh_from_db()
        assert resort.latitude == 46.0961
        assert resort.longitude == 7.2275
        assert resort.geocode_source == "manual"
        assert resort.geocode_confidence == 1.0
        assert resort.geocoded_at is not None
        assert resort.needs_review is False

    def test_response_advances_queue(self) -> None:
        """The response's ``next_in_queue`` points at the next unset row."""
        region = RegionFactory.create()
        first = ResortFactory.create(name="aaa", region=region)
        second = ResortFactory.create(name="bbb", region=region)
        client = Client()
        resp = _post_coords(client, first.pk, latitude=46.0, longitude=7.0)
        assert resp.status_code == 200
        next_entry = resp.json()["next_in_queue"]
        assert next_entry is not None
        assert next_entry["id"] == second.pk

    def test_next_in_queue_null_when_queue_empty(self) -> None:
        """When the queue empties on save, ``next_in_queue`` is null."""
        only = ResortFactory.create(name="aaa")
        client = Client()
        resp = _post_coords(client, only.pk, latitude=46.0, longitude=7.0)
        assert resp.status_code == 200
        assert resp.json()["next_in_queue"] is None

    def test_unknown_resort_returns_404(self) -> None:
        """Posting against a non-existent resort id returns 404."""
        client = Client()
        resp = _post_coords(client, 99999, latitude=46.0, longitude=7.0)
        assert resp.status_code == 404

    def test_invalid_json_returns_400(self) -> None:
        """A non-JSON body returns 400 invalid_json."""
        resort = ResortFactory.create(name="A")
        client = Client()
        resp = client.post(
            reverse("api:edit_resort_save_coords", args=[resort.pk]),
            data="not json",
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_json"

    def test_missing_lat_returns_400_invalid_coords(self) -> None:
        """Missing fields return 400 invalid_coords."""
        resort = ResortFactory.create(name="A")
        client = Client()
        resp = _post_coords(client, resort.pk, longitude=7.0)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_coords"

    def test_lat_out_of_swiss_bbox_returns_400_out_of_bounds(self) -> None:
        """Coordinates north of Switzerland are rejected."""
        resort = ResortFactory.create(name="A")
        client = Client()
        resp = _post_coords(client, resort.pk, latitude=50.0, longitude=7.0)
        assert resp.status_code == 400
        assert resp.json()["error"] == "out_of_bounds"

    def test_lon_out_of_swiss_bbox_returns_400_out_of_bounds(self) -> None:
        """Coordinates east of Switzerland are rejected."""
        resort = ResortFactory.create(name="A")
        client = Client()
        resp = _post_coords(client, resort.pk, latitude=46.0, longitude=12.0)
        assert resp.status_code == 400
        assert resp.json()["error"] == "out_of_bounds"

    def test_boundary_latitude_accepted(self) -> None:
        """The exact south boundary of the Swiss bbox is accepted."""
        resort = ResortFactory.create(name="A")
        client = Client()
        # _SWISS_BBOX = (5.9, 45.8, 10.5, 47.8).
        resp = _post_coords(client, resort.pk, latitude=45.8, longitude=7.0)
        assert resp.status_code == 200

    def test_save_clears_needs_review(self) -> None:
        """Saving a resort flagged for review clears the flag."""
        resort = ResortFactory.create(name="A", needs_review=True)
        client = Client()
        resp = _post_coords(client, resort.pk, latitude=46.0, longitude=7.0)
        assert resp.status_code == 200
        resort.refresh_from_db()
        assert resort.needs_review is False


@pytest.mark.django_db
class TestEditResortSaveCoordsDebugGate:
    """The save endpoint must 404 in production (DEBUG=False)."""

    @override_settings(DEBUG=False)
    def test_returns_404_when_debug_off(self) -> None:
        """Save endpoint refuses with 404 when DEBUG is off."""
        resort = ResortFactory.create(name="A")
        client = Client()
        # Direct-path POST — the URL itself is also gated, so a 404 is correct.
        resp = client.post(
            f"/api/edit/resorts/{resort.pk}/coords/",
            data=json.dumps({"latitude": 46.0, "longitude": 7.0}),
            content_type="application/json",
        )
        assert resp.status_code == 404
        # The DB row must not have been mutated.
        resort.refresh_from_db()
        assert resort.latitude is None
        assert resort.longitude is None


# ---------------------------------------------------------------------------
# Edit-mode rendering of /map/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMapViewEditMode:
    """``map_view`` should boot the panel only when DEBUG and the flag agree."""

    @override_settings(DEBUG=True)
    def test_query_string_with_debug_renders_panel(self) -> None:
        """``?edit=resorts`` + DEBUG=True shows the panel."""
        client = Client()
        resp = client.get(reverse("public:map") + "?edit=resorts")
        assert resp.status_code == 200
        assert b"edit-resorts-panel" in resp.content

    @override_settings(DEBUG=False)
    def test_query_string_without_debug_silent_fallback(self) -> None:
        """``?edit=resorts`` + DEBUG=False renders the normal map."""
        client = Client()
        resp = client.get(reverse("public:map") + "?edit=resorts")
        assert resp.status_code == 200
        assert b"edit-resorts-panel" not in resp.content

    @override_settings(DEBUG=True)
    def test_no_query_string_does_not_render_panel(self) -> None:
        """Without the flag, the panel is absent even in DEBUG."""
        client = Client()
        resp = client.get(reverse("public:map"))
        assert resp.status_code == 200
        assert b"edit-resorts-panel" not in resp.content
