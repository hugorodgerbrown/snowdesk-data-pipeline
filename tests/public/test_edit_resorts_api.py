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

from tests.factories import EawsSubRegionFactory, RegionFactory, ResortFactory

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

    def test_response_shape(self) -> None:
        """SNOW-85: response carries ``all_resorts`` and ``sub_regions``."""
        ResortFactory.create(name="A", latitude=46.0, longitude=7.0)
        ResortFactory.create(name="B")
        client = Client()
        resp = client.get(reverse("api:edit_resorts_queue"))
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"all_resorts", "sub_regions"}

    def test_all_resorts_ordered_by_region_then_name(self) -> None:
        """
        Catalogue order is L1 → L4 → name. Sorting by ``region_id``
        groups L1s together because the L1 prefix is a prefix of the
        full region_id, and ``name`` breaks ties within a region.
        """
        ch1 = RegionFactory.create(region_id="CH-1100")
        ch4a = RegionFactory.create(region_id="CH-4115")
        ch4b = RegionFactory.create(region_id="CH-4116")
        # Out-of-alphabetical-order names within and across regions —
        # the server's sort is what we're asserting on.
        ResortFactory.create(name="zzz", region=ch1)
        ResortFactory.create(name="aaa", region=ch4b)
        ResortFactory.create(name="mmm", region=ch4a)
        ResortFactory.create(name="bbb", region=ch4a)
        client = Client()
        resp = client.get(reverse("api:edit_resorts_queue"))
        body = resp.json()
        ordered = [(e["region_id"], e["name"]) for e in body["all_resorts"]]
        assert ordered == [
            ("CH-1100", "zzz"),
            ("CH-4115", "bbb"),
            ("CH-4115", "mmm"),
            ("CH-4116", "aaa"),
        ]

    def test_sub_regions_label_map(self) -> None:
        """
        ``sub_regions`` maps each L2 prefix to a display label.
        Prefers ``name_en`` when SLF publishes one; falls back to
        ``name_native`` so the label is never blank.

        Uses fictional ``ZZ-`` prefixes to avoid collision with the
        real EAWS fixture pre-loaded by migration 0012 (the factory
        has ``django_get_or_create=("prefix",)`` so reusing CH-41
        etc. would return the seeded row and ignore our overrides).
        """
        EawsSubRegionFactory.create(
            prefix="ZZ-11",
            name_en="Western Lower Alps",
            name_native="Bas-Alpes occidentales",
        )
        EawsSubRegionFactory.create(
            prefix="ZZ-41",
            name_en="",  # SLF doesn't publish an English name for this one.
            name_native="Unteres Wallis",
        )
        client = Client()
        resp = client.get(reverse("api:edit_resorts_queue"))
        body = resp.json()
        sub_regions = body["sub_regions"]
        assert sub_regions["ZZ-11"] == "Western Lower Alps"
        assert sub_regions["ZZ-41"] == "Unteres Wallis"

    def test_all_resorts_includes_every_resort(self) -> None:
        """
        Catalogue includes every resort regardless of geocoding state;
        ``has_coords`` reflects reality. SNOW-85: with the manual
        workflow, the operator works through the whole catalogue.
        """
        clean = ResortFactory.create(name="A", latitude=46.0, longitude=7.0)
        unset = ResortFactory.create(name="B")
        flagged = ResortFactory.create(
            name="C",
            latitude=46.0,
            longitude=7.0,
            needs_review=True,
        )
        client = Client()
        resp = client.get(reverse("api:edit_resorts_queue"))
        body = resp.json()
        catalogue = {entry["id"]: entry for entry in body["all_resorts"]}
        assert catalogue[clean.pk]["has_coords"] is True
        assert catalogue[unset.pk]["has_coords"] is False
        assert catalogue[flagged.pk]["needs_review"] is True

    def test_all_resorts_carries_display_fields(self) -> None:
        """
        Catalogue entries carry the full display fields needed by the
        side panel (region_name, canton, latitude, longitude) so the
        panel can render a row's target readout without a follow-up
        fetch. SNOW-85 widened the catalogue: previously selecting
        Aigle (set, in CH-1111) showed blank lat/lon and zoomed to the
        wrong region because the lookup path lost the data.
        """
        region = RegionFactory.create(region_id="CH-1111", name="Lower Chablais")
        resort = ResortFactory.create(
            name="Aigle",
            region=region,
            canton="VD",
            latitude=46.318,
            longitude=6.969,
        )
        client = Client()
        resp = client.get(reverse("api:edit_resorts_queue"))
        body = resp.json()
        entry = next(e for e in body["all_resorts"] if e["id"] == resort.pk)
        assert entry["region_id"] == "CH-1111"
        assert entry["region_name"] == "Lower Chablais"
        assert entry["canton"] == "VD"
        assert entry["latitude"] == 46.318
        assert entry["longitude"] == 6.969
        assert entry["has_coords"] is True
        assert entry["needs_review"] is False


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

    def test_response_does_not_carry_next_in_queue(self) -> None:
        """SNOW-85 dropped auto-advance; ``next_in_queue`` is gone."""
        resort = ResortFactory.create(name="A")
        client = Client()
        resp = _post_coords(client, resort.pk, latitude=46.0, longitude=7.0)
        assert resp.status_code == 200
        assert "next_in_queue" not in resp.json()

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

    def test_save_rebinds_region_when_pin_falls_in_different_polygon(self) -> None:
        """
        Auto-rebind: if the saved point lands inside a different region's
        polygon than the resort's current FK, update the FK. Some imported
        resorts have wrong region tags (e.g. Villars-sur-Ollon was seeded
        as CH-1113 but actually sits in CH-1114); the operator placing a
        pin is the most authoritative signal we'll get.
        """
        # Two adjacent square polygons inside the Swiss bbox; the resort
        # is FK'd to ``wrong_region`` but the saved pin falls inside
        # ``correct_region``.
        wrong_region = RegionFactory.create(
            region_id="CH-1113",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]],
                ],
            },
        )
        correct_region = RegionFactory.create(
            region_id="CH-1114",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[7.5, 46.0], [8.0, 46.0], [8.0, 46.5], [7.5, 46.5], [7.5, 46.0]],
                ],
            },
        )
        resort = ResortFactory.create(name="Villars-sur-Ollon", region=wrong_region)
        client = Client()
        # Pin at lon=7.75, lat=46.25 — inside correct_region's polygon.
        resp = _post_coords(client, resort.pk, latitude=46.25, longitude=7.75)
        assert resp.status_code == 200
        body = resp.json()
        # Response carries the rebound region in both id and name.
        assert body["region_id"] == "CH-1114"
        assert body["region_name"] == correct_region.name
        # DB row was updated.
        resort.refresh_from_db()
        assert resort.region_id == correct_region.pk

    def test_save_keeps_region_when_pin_in_same_polygon(self) -> None:
        """
        No rebind when the saved point is already inside the FK'd
        region's polygon — the existing region must be preserved, and
        ``region`` must NOT be in the update_fields side effects.
        """
        region = RegionFactory.create(
            region_id="CH-1111",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[6.0, 46.0], [7.5, 46.0], [7.5, 47.5], [6.0, 47.5], [6.0, 46.0]],
                ],
            },
        )
        resort = ResortFactory.create(name="Aigle", region=region)
        client = Client()
        resp = _post_coords(client, resort.pk, latitude=46.318, longitude=6.969)
        assert resp.status_code == 200
        assert resp.json()["region_id"] == "CH-1111"
        resort.refresh_from_db()
        assert resort.region_id == region.pk

    def test_save_keeps_region_when_pin_outside_every_polygon(self) -> None:
        """
        Defensive: if the saved point falls in a no-coverage gap (outside
        every region polygon), the FK is left alone rather than nulled.
        """
        # The only region with a boundary doesn't contain the saved pin.
        far_region = RegionFactory.create(
            region_id="CH-9999",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [
                        [10.0, 47.0],
                        [10.4, 47.0],
                        [10.4, 47.4],
                        [10.0, 47.4],
                        [10.0, 47.0],
                    ],
                ],
            },
        )
        original_region = RegionFactory.create(region_id="CH-1100")
        resort = ResortFactory.create(name="Edge", region=original_region)
        client = Client()
        # Pin in the middle of Switzerland, well outside far_region.
        resp = _post_coords(client, resort.pk, latitude=46.5, longitude=7.5)
        assert resp.status_code == 200
        assert resp.json()["region_id"] == "CH-1100"
        resort.refresh_from_db()
        assert resort.region_id == original_region.pk
        assert far_region.pk != resort.region_id  # sanity


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


# ---------------------------------------------------------------------------
# Geometry helpers (SNOW-85 — auto-rebind support)
# ---------------------------------------------------------------------------


class TestPointInPolygon:
    """
    Unit tests for the ray-casting point-in-polygon helper that backs
    the resort-save auto-rebind. Direct tests on the helper rather than
    only via the endpoint, so a regression here is diagnosable without
    untangling Django/HTTP layers.
    """

    SQUARE: dict = {
        "type": "Polygon",
        "coordinates": [
            # Outer ring — clockwise or counter-clockwise both work.
            [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]],
        ],
    }

    SQUARE_WITH_HOLE: dict = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]],
            # Hole in the middle.
            [[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0], [4.0, 4.0]],
        ],
    }

    def test_point_inside_polygon_returns_true(self) -> None:
        """A point clearly inside the outer ring is reported as inside."""
        from public.api import _point_in_polygon

        assert _point_in_polygon(lat=5.0, lon=5.0, polygon=self.SQUARE) is True

    def test_point_outside_polygon_returns_false(self) -> None:
        """A point clearly outside the outer ring is reported as outside."""
        from public.api import _point_in_polygon

        assert _point_in_polygon(lat=20.0, lon=20.0, polygon=self.SQUARE) is False

    def test_point_inside_hole_returns_false(self) -> None:
        """
        A point inside a hole is reported as outside — the standard
        ray-cast over all rings flips parity correctly for holes.
        """
        from public.api import _point_in_polygon

        assert (
            _point_in_polygon(lat=5.0, lon=5.0, polygon=self.SQUARE_WITH_HOLE) is False
        )

    def test_point_outside_hole_but_inside_outer_returns_true(self) -> None:
        """A point inside the outer ring but outside the hole is inside."""
        from public.api import _point_in_polygon

        assert (
            _point_in_polygon(lat=2.0, lon=2.0, polygon=self.SQUARE_WITH_HOLE) is True
        )

    def test_empty_polygon_returns_false(self) -> None:
        """A degenerate polygon with no rings is treated as empty."""
        from public.api import _point_in_polygon

        assert (
            _point_in_polygon(
                lat=0.0, lon=0.0, polygon={"type": "Polygon", "coordinates": []}
            )
            is False
        )


@pytest.mark.django_db
class TestRegionForPoint:
    """
    Tests for the region lookup that ``edit_resort_save_coords`` uses
    to auto-rebind a resort's parent region from its saved location.
    """

    def test_finds_containing_region(self) -> None:
        """Returns the region whose polygon contains the test point."""
        from public.api import _region_for_point

        target = RegionFactory.create(
            region_id="CH-A",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[7.0, 46.0], [8.0, 46.0], [8.0, 47.0], [7.0, 47.0], [7.0, 46.0]],
                ],
            },
        )
        # An adjacent region that does not contain the test point.
        RegionFactory.create(
            region_id="CH-B",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[8.0, 46.0], [9.0, 46.0], [9.0, 47.0], [8.0, 47.0], [8.0, 46.0]],
                ],
            },
        )
        assert _region_for_point(lat=46.5, lon=7.5) == target

    def test_returns_none_outside_every_region(self) -> None:
        """Returns ``None`` when the point falls in no coverage."""
        from public.api import _region_for_point

        RegionFactory.create(
            region_id="CH-X",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[7.0, 46.0], [8.0, 46.0], [8.0, 47.0], [7.0, 47.0], [7.0, 46.0]],
                ],
            },
        )
        assert _region_for_point(lat=46.5, lon=10.0) is None

    def test_skips_regions_without_boundary(self) -> None:
        """Regions with ``boundary=None`` are excluded from the lookup."""
        from public.api import _region_for_point

        RegionFactory.create(region_id="CH-NOBOUND", boundary=None)
        assert _region_for_point(lat=46.5, lon=7.5) is None


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
