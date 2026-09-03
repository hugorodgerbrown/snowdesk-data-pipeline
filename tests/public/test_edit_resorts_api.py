"""
tests/public/test_edit_resorts_api.py — SNOW-74 in-map resort editor.

Covers the three endpoints introduced for the ``?edit=resorts`` mode:

* ``api:resorts_geojson``           — always available; only geocoded rows.
* ``api:edit_resorts_queue``        — superuser-only;
  queue + catalogue payload.
* ``api:edit_resort_save``          — superuser-only;
  persists the placed lat/lon and the hand-curated detail fields.
* ``api:edit_resort_create``        — superuser-only;
  creates a resort from a placed pin, deriving its parent region from
  that pin.

The three edit-mode endpoints were gated on the ``edit_map`` waffle flag
until SNOW-724 replaced it with ``request.user.is_superuser`` — the same
audience the flag shipped (``superusers=True``), expressed without a DB
row. So each gate is asserted twice, on an anonymous visitor and on an
authenticated non-superuser, and every positive path runs through
``_superuser_client()``. The ``resorts_geojson`` endpoint is not gated.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.regions.forms import RESORT_DETAIL_FIELDS
from apps.regions.models import Resort
from tests.factories import (
    MicroRegionFactory,
    ResortFactory,
    SubRegionFactory,
    UserFactory,
)


def _superuser_client() -> Client:
    """Return a test client signed in as a superuser.

    The editor's whole audience since SNOW-724 — every positive-path test
    below needs one, and the default ``Client()`` is anonymous.
    """
    client = Client()
    client.force_login(UserFactory.create(is_superuser=True))
    return client


def _ordinary_client() -> Client:
    """Return a test client signed in as an authenticated non-superuser.

    The interesting negative case: being signed in is not enough.
    """
    client = Client()
    client.force_login(UserFactory.create(is_staff=False))
    return client


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
        assert feature["properties"]["id"] == geocoded.slug
        assert feature["properties"]["name"] == "Both"
        assert feature["properties"]["region_id"] == geocoded.region.region_id
        assert feature["properties"]["needs_review"] is False
        # SNOW-543: every feature carries its tier, so the map can size the
        # pin without a second request.
        assert feature["properties"]["tier"] == "STANDARD"

    def test_tier_is_carried_per_feature(self) -> None:
        """SNOW-543: the curated tier reaches the layer that sizes the pin."""
        core = ResortFactory.create(
            name="Zermatt", geocoded=True, tier=Resort.Tier.CORE
        )
        minor = ResortFactory.create(
            name="Realp", latitude=46.6, longitude=8.5, tier=Resort.Tier.MINOR
        )

        client = Client()
        resp = client.get(reverse("api:resorts_geojson"))
        by_id = {
            f["properties"]["id"]: f["properties"] for f in resp.json()["features"]
        }

        assert by_id[core.slug]["tier"] == "CORE"
        assert by_id[minor.slug]["tier"] == "MINOR"

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
    """Tests for ``GET /api/edit/resorts/queue/`` (superuser-only)."""

    def test_response_shape(self) -> None:
        """SNOW-85: response carries ``all_resorts`` and ``sub_regions``."""
        ResortFactory.create(name="A", latitude=46.0, longitude=7.0)
        ResortFactory.create(name="B")
        client = _superuser_client()
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
        ch1 = MicroRegionFactory.create(region_id="CH-1100")
        ch4a = MicroRegionFactory.create(region_id="CH-4115")
        ch4b = MicroRegionFactory.create(region_id="CH-4116")
        # Out-of-alphabetical-order names within and across regions —
        # the server's sort is what we're asserting on.
        ResortFactory.create(name="zzz", region=ch1)
        ResortFactory.create(name="aaa", region=ch4b)
        ResortFactory.create(name="mmm", region=ch4a)
        ResortFactory.create(name="bbb", region=ch4a)
        client = _superuser_client()
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
        SubRegionFactory.create(
            prefix="ZZ-11",
            name_en="Western Lower Alps",
            name_native="Bas-Alpes occidentales",
        )
        SubRegionFactory.create(
            prefix="ZZ-41",
            name_en="",  # SLF doesn't publish an English name for this one.
            name_native="Unteres Wallis",
        )
        client = _superuser_client()
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
        client = _superuser_client()
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
        region = MicroRegionFactory.create(region_id="CH-1111", name="Lower Chablais")
        resort = ResortFactory.create(
            name="Aigle",
            region=region,
            canton="VD",
            latitude=46.318,
            longitude=6.969,
        )
        client = _superuser_client()
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

    def test_all_resorts_carries_detail_fields(self) -> None:
        """
        Every catalogue entry carries a ``details`` object holding the
        hand-curated metadata (SNOW-500), so the panel can populate its
        edit form the moment a row is selected — no per-resort fetch.
        """
        resort = ResortFactory.create(
            name="Verbier",
            operator_name="Téléverbier SA",
            website="https://www.verbier.ch/",
            why_it_matters="Les 4 Vallées high alpine, heavy off-piste.",
            tier=Resort.Tier.CORE,
            num_lifts=32,
            num_runs=97,
            total_piste_km=410.5,
            base_elevation_m=1500,
            top_elevation_m=3330,
            typical_season_open="11-15",
            typical_season_close="04-30",
        )
        client = _superuser_client()
        resp = client.get(reverse("api:edit_resorts_queue"))
        entry = next(e for e in resp.json()["all_resorts"] if e["id"] == resort.pk)
        assert set(entry["details"]) == set(RESORT_DETAIL_FIELDS)
        assert entry["details"] == {
            "tier": "CORE",
            "name_alt": resort.name_alt,
            "operator_name": "Téléverbier SA",
            "website": "https://www.verbier.ch/",
            "why_it_matters": "Les 4 Vallées high alpine, heavy off-piste.",
            "num_lifts": 32,
            "num_runs": 97,
            "total_piste_km": 410.5,
            "base_elevation_m": 1500,
            "top_elevation_m": 3330,
            "typical_season_open": "11-15",
            "typical_season_close": "04-30",
        }

    def test_detail_fields_are_null_when_unset(self) -> None:
        """Unset numeric detail fields serialise as null, not omitted."""
        resort = ResortFactory.create(name="Sparse")
        client = _superuser_client()
        resp = client.get(reverse("api:edit_resorts_queue"))
        entry = next(e for e in resp.json()["all_resorts"] if e["id"] == resort.pk)
        assert entry["details"]["num_lifts"] is None
        assert entry["details"]["total_piste_km"] is None


@pytest.mark.django_db
class TestEditResortsQueueAdminGate:
    """The queue endpoint must 404 for anyone who is not a superuser."""

    def test_anonymous_returns_404(self) -> None:
        """An anonymous caller is refused, and told nothing about the URL."""
        resp = Client().get(reverse("api:edit_resorts_queue"))
        assert resp.status_code == 404

    def test_authenticated_non_superuser_returns_404(self) -> None:
        """Signing in is not enough — SNOW-724 kept the flag's audience."""
        resp = _ordinary_client().get(reverse("api:edit_resorts_queue"))
        assert resp.status_code == 404

    def test_superuser_returns_200(self) -> None:
        """The other half of the gate: a superuser gets the payload."""
        resp = _superuser_client().get(reverse("api:edit_resorts_queue"))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# edit_resort_save (superuser-only)
# ---------------------------------------------------------------------------


def _post_save(
    client: Client, resort_id: int, **body: Any
) -> Any:  # mock-typing-impractical
    """Helper — POST JSON to the save endpoint."""
    return client.post(
        reverse("api:edit_resort_save", args=[resort_id]),
        data=json.dumps(body),
        content_type="application/json",
    )


@pytest.mark.django_db
class TestEditResortSave:
    """Tests for ``POST /api/edit/resorts/<id>/save/`` (superuser-only)."""

    def test_happy_path_writes_all_fields(self) -> None:
        """A valid POST sets coords + provenance + clears needs_review."""
        resort = ResortFactory.create(name="A", needs_review=True)
        client = _superuser_client()
        resp = _post_save(client, resort.pk, latitude=46.0961, longitude=7.2275)
        assert resp.status_code == 200, resp.content

        resort.refresh_from_db()
        assert resort.latitude == 46.0961
        assert resort.longitude == 7.2275
        assert resort.geocode_source == Resort.GeocodeSource.MANUAL
        assert resort.geocode_confidence == 1.0
        assert resort.geocoded_at is not None
        assert resort.needs_review is False

    def test_response_does_not_carry_next_in_queue(self) -> None:
        """SNOW-85 dropped auto-advance; ``next_in_queue`` is gone."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = _post_save(client, resort.pk, latitude=46.0, longitude=7.0)
        assert resp.status_code == 200
        assert "next_in_queue" not in resp.json()

    def test_unknown_resort_returns_404(self) -> None:
        """Posting against a non-existent resort id returns 404."""
        client = _superuser_client()
        resp = _post_save(client, 99999, latitude=46.0, longitude=7.0)
        assert resp.status_code == 404

    def test_invalid_json_returns_400(self) -> None:
        """A non-JSON body returns 400 invalid_json."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = client.post(
            reverse("api:edit_resort_save", args=[resort.pk]),
            data="not json",
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_json"

    def test_missing_lat_returns_400_invalid_coords(self) -> None:
        """Missing fields return 400 invalid_coords."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = _post_save(client, resort.pk, longitude=7.0)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_coords"

    def test_lat_out_of_swiss_bbox_returns_400_out_of_bounds(self) -> None:
        """Coordinates north of Switzerland are rejected."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = _post_save(client, resort.pk, latitude=50.0, longitude=7.0)
        assert resp.status_code == 400
        assert resp.json()["error"] == "out_of_bounds"

    def test_lon_out_of_swiss_bbox_returns_400_out_of_bounds(self) -> None:
        """Coordinates east of Switzerland are rejected."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = _post_save(client, resort.pk, latitude=46.0, longitude=12.0)
        assert resp.status_code == 400
        assert resp.json()["error"] == "out_of_bounds"

    def test_boundary_latitude_accepted(self) -> None:
        """The exact south boundary of the Swiss bbox is accepted."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        # _SWISS_BBOX = (5.9, 45.8, 10.5, 47.8).
        resp = _post_save(client, resort.pk, latitude=45.8, longitude=7.0)
        assert resp.status_code == 200

    def test_save_clears_needs_review(self) -> None:
        """Saving a resort flagged for review clears the flag."""
        resort = ResortFactory.create(name="A", needs_review=True)
        client = _superuser_client()
        resp = _post_save(client, resort.pk, latitude=46.0, longitude=7.0)
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
        wrong_region = MicroRegionFactory.create(
            region_id="CH-1113",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]],
                ],
            },
        )
        correct_region = MicroRegionFactory.create(
            region_id="CH-1114",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[7.5, 46.0], [8.0, 46.0], [8.0, 46.5], [7.5, 46.5], [7.5, 46.0]],
                ],
            },
        )
        resort = ResortFactory.create(name="Villars-sur-Ollon", region=wrong_region)
        client = _superuser_client()
        # Pin at lon=7.75, lat=46.25 — inside correct_region's polygon.
        resp = _post_save(client, resort.pk, latitude=46.25, longitude=7.75)
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
        region = MicroRegionFactory.create(
            region_id="CH-1111",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[6.0, 46.0], [7.5, 46.0], [7.5, 47.5], [6.0, 47.5], [6.0, 46.0]],
                ],
            },
        )
        resort = ResortFactory.create(name="Aigle", region=region)
        client = _superuser_client()
        resp = _post_save(client, resort.pk, latitude=46.318, longitude=6.969)
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
        far_region = MicroRegionFactory.create(
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
        original_region = MicroRegionFactory.create(region_id="CH-1100")
        resort = ResortFactory.create(name="Edge", region=original_region)
        client = _superuser_client()
        # Pin in the middle of Switzerland, well outside far_region.
        resp = _post_save(client, resort.pk, latitude=46.5, longitude=7.5)
        assert resp.status_code == 200
        assert resp.json()["region_id"] == "CH-1100"
        resort.refresh_from_db()
        assert resort.region_id == original_region.pk
        assert far_region.pk != resort.region_id  # sanity


@pytest.mark.django_db
class TestEditResortSaveDetails:
    """The hand-curated detail fields carried alongside the coordinates."""

    def test_details_are_persisted_and_echoed(self) -> None:
        """A valid ``details`` object is written and returned in the response."""
        resort = ResortFactory.create(name="Verbier")
        client = _superuser_client()
        resp = _post_save(
            client,
            resort.pk,
            latitude=46.0961,
            longitude=7.2275,
            details={
                "name_alt": "Val de Bagnes",
                "operator_name": "Téléverbier SA",
                "website": "https://www.verbier.ch/",
                "why_it_matters": "Les 4 Vallées high alpine, heavy off-piste.",
                "tier": "CORE",
                "num_lifts": 32,
                "num_runs": 97,
                "total_piste_km": 410.5,
                "base_elevation_m": 1500,
                "top_elevation_m": 3330,
                "typical_season_open": "11-15",
                "typical_season_close": "04-30",
            },
        )
        assert resp.status_code == 200, resp.content

        resort.refresh_from_db()
        assert resort.name_alt == "Val de Bagnes"
        assert resort.operator_name == "Téléverbier SA"
        assert resort.website == "https://www.verbier.ch/"
        assert resort.why_it_matters == "Les 4 Vallées high alpine, heavy off-piste."
        assert resort.tier == Resort.Tier.CORE
        assert resort.num_lifts == 32
        assert resort.num_runs == 97
        assert resort.total_piste_km == 410.5
        assert resort.base_elevation_m == 1500
        assert resort.top_elevation_m == 3330
        assert resort.typical_season_open == "11-15"
        assert resort.typical_season_close == "04-30"

        body = resp.json()
        assert body["details"]["num_lifts"] == 32
        assert set(body["details"]) == set(RESORT_DETAIL_FIELDS)

    def test_details_are_optional(self) -> None:
        """Omitting ``details`` entirely leaves every stored value alone."""
        resort = ResortFactory.create(name="A", num_lifts=7)
        client = _superuser_client()
        resp = _post_save(client, resort.pk, latitude=46.0, longitude=7.0)
        assert resp.status_code == 200
        resort.refresh_from_db()
        assert resort.num_lifts == 7

    def test_omitted_keys_keep_their_stored_value(self) -> None:
        """A partial ``details`` object never blanks the keys it omits."""
        resort = ResortFactory.create(
            name="A",
            num_lifts=7,
            operator_name="Original Ops",
        )
        client = _superuser_client()
        resp = _post_save(
            client,
            resort.pk,
            latitude=46.0,
            longitude=7.0,
            details={"num_lifts": 9},
        )
        assert resp.status_code == 200
        resort.refresh_from_db()
        assert resort.num_lifts == 9
        assert resort.operator_name == "Original Ops"

    def test_blank_string_clears_a_numeric_field(self) -> None:
        """An explicitly-blanked field is cleared, not ignored.

        The panel's inputs post their raw string values, so "the operator
        emptied this box" arrives as ``""``.
        """
        resort = ResortFactory.create(name="A", num_lifts=7, operator_name="Ops")
        client = _superuser_client()
        resp = _post_save(
            client,
            resort.pk,
            latitude=46.0,
            longitude=7.0,
            details={"num_lifts": "", "operator_name": ""},
        )
        assert resp.status_code == 200
        resort.refresh_from_db()
        assert resort.num_lifts is None
        assert resort.operator_name == ""

    def test_string_numerics_are_coerced(self) -> None:
        """Numbers arriving as strings (what the inputs post) are coerced."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = _post_save(
            client,
            resort.pk,
            latitude=46.0,
            longitude=7.0,
            details={"num_lifts": "12", "total_piste_km": "88.5"},
        )
        assert resp.status_code == 200
        resort.refresh_from_db()
        assert resort.num_lifts == 12
        assert resort.total_piste_km == 88.5

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("num_lifts", "not a number"),
            ("num_lifts", -3),
            ("website", "not a url"),
            ("typical_season_open", "13-01"),
            ("typical_season_close", "April"),
            ("total_piste_km", -5),
            ("name_alt", "x" * 256),
            ("why_it_matters", "x" * 256),
            ("tier", "MAJOR"),
        ],
    )
    def test_invalid_detail_returns_400_naming_the_field(
        self,
        field: str,
        value: Any,
    ) -> None:
        """Each rejected field is named in the ``fields`` error map."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = _post_save(
            client,
            resort.pk,
            latitude=46.0,
            longitude=7.0,
            details={field: value},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "invalid_details"
        assert field in body["fields"]

    def test_top_below_base_elevation_is_rejected(self) -> None:
        """A top elevation under the base elevation is a data-entry slip."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = _post_save(
            client,
            resort.pk,
            latitude=46.0,
            longitude=7.0,
            details={"base_elevation_m": 2000, "top_elevation_m": 1500},
        )
        assert resp.status_code == 400
        assert "top_elevation_m" in resp.json()["fields"]

    def test_non_object_details_returns_400(self) -> None:
        """``details`` must be a JSON object."""
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = _post_save(
            client,
            resort.pk,
            latitude=46.0,
            longitude=7.0,
            details=["num_lifts"],
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_details"

    def test_rejected_details_leave_coordinates_unwritten(self) -> None:
        """A 400 on a detail field must not half-save the row."""
        resort = ResortFactory.create(name="A", latitude=46.0, longitude=7.0)
        client = _superuser_client()
        resp = _post_save(
            client,
            resort.pk,
            latitude=46.5,
            longitude=7.5,
            details={"num_lifts": "nope"},
        )
        assert resp.status_code == 400
        resort.refresh_from_db()
        assert resort.latitude == 46.0
        assert resort.longitude == 7.0

    def test_unknown_detail_keys_are_ignored(self) -> None:
        """Keys outside RESORT_DETAIL_FIELDS can't reach the model.

        Notably the geocode-provenance columns, which the endpoint owns.
        """
        resort = ResortFactory.create(name="A")
        client = _superuser_client()
        resp = _post_save(
            client,
            resort.pk,
            latitude=46.0,
            longitude=7.0,
            details={"needs_review": True, "canton": "ZZ", "num_lifts": 3},
        )
        assert resp.status_code == 200
        resort.refresh_from_db()
        assert resort.num_lifts == 3
        assert resort.needs_review is False
        assert resort.canton != "ZZ"


@pytest.mark.django_db
class TestEditResortSaveAdminGate:
    """The save endpoint must 404 for anyone who is not a superuser."""

    @pytest.mark.parametrize("client_factory", [Client, _ordinary_client])
    def test_returns_404_and_writes_nothing(
        self, client_factory: Any
    ) -> None:  # mock-typing-impractical
        """Anonymous and signed-in-but-ordinary are both refused.

        The write assertion is the load-bearing half: a gate that 404s
        after mutating the row would be no gate at all.
        """
        resort = ResortFactory.create(name="A")
        resp = client_factory().post(
            reverse("api:edit_resort_save", args=[resort.pk]),
            data=json.dumps({"latitude": 46.0, "longitude": 7.0}),
            content_type="application/json",
        )
        assert resp.status_code == 404
        # The DB row must not have been mutated.
        resort.refresh_from_db()
        assert resort.latitude is None
        assert resort.longitude is None

    def test_superuser_writes_the_row(self) -> None:
        """The other half of the gate: a superuser's save lands."""
        resort = ResortFactory.create(name="A")
        resp = _post_save(
            _superuser_client(), resort.pk, latitude=46.0961, longitude=7.2275
        )
        assert resp.status_code == 200
        resort.refresh_from_db()
        assert resort.latitude == 46.0961


# ---------------------------------------------------------------------------
# edit_resort_create (superuser-only)
# ---------------------------------------------------------------------------


def _post_create(client: Client, **body: Any) -> Any:  # mock-typing-impractical
    """Helper — POST JSON to the create endpoint."""
    return client.post(
        reverse("api:edit_resort_create"),
        data=json.dumps(body),
        content_type="application/json",
    )


def _region_with_square_boundary(region_id: str = "CH-4115") -> Any:
    """Create a region whose polygon covers (46.0–46.5 N, 7.0–7.5 E).

    Every create test needs a region for the pin to land in — the parent
    region is derived from the point, so a resort cannot be created
    without one.
    """
    return MicroRegionFactory.create(
        region_id=region_id,
        boundary={
            "type": "Polygon",
            "coordinates": [
                [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]],
            ],
        },
    )


@pytest.mark.django_db
class TestEditResortCreate:
    """Tests for ``POST /api/edit/resorts/create/`` (superuser-only)."""

    def test_happy_path_creates_row_with_region_from_the_pin(self) -> None:
        """A valid POST inserts a resort bound to the region containing the pin."""
        region = _region_with_square_boundary()
        client = _superuser_client()
        resp = _post_create(
            client,
            name="Nouvelle Station",
            canton="VS",
            latitude=46.25,
            longitude=7.25,
        )
        assert resp.status_code == 201, resp.content

        resort = Resort.objects.get(name="Nouvelle Station")
        assert resort.region_id == region.pk
        assert resort.canton == "VS"
        assert resort.latitude == 46.25
        assert resort.longitude == 7.25
        # Stamped exactly as a save stamps an edited row — the operator
        # placed the pin, so the position is manually confirmed.
        assert resort.geocode_source == Resort.GeocodeSource.MANUAL
        assert resort.geocode_confidence == 1.0
        assert resort.geocoded_at is not None
        assert resort.needs_review is False

    def test_response_carries_a_catalogue_entry(self) -> None:
        """The body holds every field the panel's catalogue rows need."""
        region = _region_with_square_boundary()
        client = _superuser_client()
        resp = _post_create(
            client,
            name="Nouvelle Station",
            canton="VS",
            latitude=46.25,
            longitude=7.25,
        )
        body = resp.json()
        assert body["id"] == Resort.objects.get(name="Nouvelle Station").pk
        assert body["name"] == "Nouvelle Station"
        assert body["region_id"] == region.region_id
        assert body["region_name"] == region.name
        assert body["canton"] == "VS"
        assert body["has_coords"] is True
        assert body["needs_review"] is False
        assert set(body["details"]) == set(RESORT_DETAIL_FIELDS)

    def test_canton_is_upper_cased(self) -> None:
        """A lower-case canton is normalised so "vs" and "VS" are one value."""
        _region_with_square_boundary()
        client = _superuser_client()
        _post_create(client, name="A", canton="vs", latitude=46.25, longitude=7.25)
        assert Resort.objects.get(name="A").canton == "VS"

    def test_details_are_persisted_in_the_same_insert(self) -> None:
        """The detail fields land on the created row without a second call."""
        _region_with_square_boundary()
        client = _superuser_client()
        resp = _post_create(
            client,
            name="Nouvelle Station",
            canton="VS",
            latitude=46.25,
            longitude=7.25,
            details={
                "operator_name": "Remontées SA",
                "num_lifts": 8,
                "base_elevation_m": 1200,
                "top_elevation_m": 2400,
            },
        )
        assert resp.status_code == 201, resp.content
        resort = Resort.objects.get(name="Nouvelle Station")
        assert resort.operator_name == "Remontées SA"
        assert resort.num_lifts == 8
        assert resort.base_elevation_m == 1200
        assert resort.top_elevation_m == 2400

    def test_invalid_detail_field_creates_nothing(self) -> None:
        """A rejected detail field aborts the whole create, leaving no row."""
        _region_with_square_boundary()
        client = _superuser_client()
        resp = _post_create(
            client,
            name="Nouvelle Station",
            canton="VS",
            latitude=46.25,
            longitude=7.25,
            # Top below base — rejected by ResortDetailsForm.clean().
            details={"base_elevation_m": 2400, "top_elevation_m": 1200},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "invalid_details"
        assert "top_elevation_m" in body["fields"]
        assert not Resort.objects.filter(name="Nouvelle Station").exists()

    def test_missing_name_returns_400(self) -> None:
        """A blank name is rejected — nothing else can supply it."""
        _region_with_square_boundary()
        client = _superuser_client()
        resp = _post_create(
            client, name="   ", canton="VS", latitude=46.25, longitude=7.25
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_identity"

    def test_omitted_canton_is_inherited_from_the_region(self) -> None:
        """A blank canton takes the one the region's resorts already use.

        Canton is very nearly a function of the region, so the operator is
        not asked to re-type what the sibling rows already agree on.
        """
        region = _region_with_square_boundary()
        ResortFactory.create(name="Sibling", region=region, canton="GR")
        client = _superuser_client()
        resp = _post_create(client, name="A", latitude=46.25, longitude=7.25)
        assert resp.status_code == 201, resp.content
        assert Resort.objects.get(name="A").canton == "GR"
        assert resp.json()["canton"] == "GR"

    def test_sent_canton_beats_the_inherited_one(self) -> None:
        """An explicit canton wins — border cases need the operator's answer."""
        region = _region_with_square_boundary()
        ResortFactory.create(name="Sibling", region=region, canton="GR")
        client = _superuser_client()
        resp = _post_create(
            client, name="A", canton="BE/VS", latitude=46.25, longitude=7.25
        )
        assert resp.status_code == 201, resp.content
        assert Resort.objects.get(name="A").canton == "BE/VS"

    def test_inherited_canton_is_the_majority_of_the_siblings(self) -> None:
        """A region whose rows disagree contributes its most common value."""
        region = _region_with_square_boundary()
        ResortFactory.create(name="S1", region=region, canton="VS")
        ResortFactory.create(name="S2", region=region, canton="VS")
        ResortFactory.create(name="S3", region=region, canton="BE")
        client = _superuser_client()
        resp = _post_create(client, name="A", latitude=46.25, longitude=7.25)
        assert resp.status_code == 201, resp.content
        assert Resort.objects.get(name="A").canton == "VS"

    def test_blank_sibling_cantons_do_not_count(self) -> None:
        """An empty canton on a sibling is not a value worth inheriting."""
        region = _region_with_square_boundary()
        ResortFactory.create(name="Blank", region=region, canton="")
        client = _superuser_client()
        resp = _post_create(client, name="A", latitude=46.25, longitude=7.25)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_identity"
        assert not Resort.objects.filter(name="A").exists()

    def test_omitted_canton_in_an_empty_region_returns_400(self) -> None:
        """With no sibling to read from, the endpoint has to ask."""
        _region_with_square_boundary()
        client = _superuser_client()
        resp = _post_create(client, name="A", latitude=46.25, longitude=7.25)
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "invalid_identity"
        # The message has to name the region, or the operator cannot tell
        # which of the two identity failures they hit.
        assert "CH-4115" in body["detail"]

    def test_missing_name_is_rejected_before_the_region_lookup(self) -> None:
        """Name is the one value nothing else can supply."""
        client = _superuser_client()
        resp = _post_create(client, canton="VS", latitude=46.25, longitude=7.25)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "name is required"

    def test_over_long_name_returns_400(self) -> None:
        """A name past the column width is rejected, not truncated."""
        _region_with_square_boundary()
        client = _superuser_client()
        resp = _post_create(
            client,
            name="x" * 256,
            canton="VS",
            latitude=46.25,
            longitude=7.25,
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_identity"

    def test_out_of_bbox_returns_400(self) -> None:
        """Coordinates outside Switzerland are rejected before any lookup."""
        _region_with_square_boundary()
        client = _superuser_client()
        resp = _post_create(
            client, name="A", canton="VS", latitude=50.0, longitude=7.25
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "out_of_bounds"

    def test_invalid_json_returns_400(self) -> None:
        """A non-JSON body returns 400 invalid_json."""
        resp = _superuser_client().post(
            reverse("api:edit_resort_create"),
            data="not json",
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_json"

    def test_pin_outside_every_region_returns_400_no_region(self) -> None:
        """``Resort.region`` is not nullable, so a no-coverage pin is refused."""
        _region_with_square_boundary()
        client = _superuser_client()
        # Inside the Swiss bbox but outside the one region's polygon.
        resp = _post_create(client, name="A", canton="VS", latitude=47.0, longitude=9.0)
        assert resp.status_code == 400
        assert resp.json()["error"] == "no_region"
        assert not Resort.objects.filter(name="A").exists()

    def test_duplicate_name_in_same_region_returns_409(self) -> None:
        """A second resort of the same name in one region is refused."""
        region = _region_with_square_boundary()
        ResortFactory.create(name="Verbier", region=region)
        client = _superuser_client()
        resp = _post_create(
            client,
            # Case-insensitive — a double-click that re-types the name
            # slightly differently is still the same duplicate.
            name="verbier",
            canton="VS",
            latitude=46.25,
            longitude=7.25,
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "duplicate_name"
        assert Resort.objects.filter(name__iexact="verbier").count() == 1

    def test_same_name_in_a_different_region_is_allowed(self) -> None:
        """The duplicate guard is scoped to the derived region, not global."""
        _region_with_square_boundary(region_id="CH-4115")
        other = MicroRegionFactory.create(region_id="CH-2222", boundary=None)
        ResortFactory.create(name="Fiesch", region=other)
        client = _superuser_client()
        resp = _post_create(
            client,
            name="Fiesch",
            canton="VS",
            latitude=46.25,
            longitude=7.25,
        )
        assert resp.status_code == 201, resp.content
        assert Resort.objects.filter(name="Fiesch").count() == 2

    def test_get_is_rejected(self) -> None:
        """The endpoint is POST-only."""
        resp = _superuser_client().get(reverse("api:edit_resort_create"))
        assert resp.status_code == 405


@pytest.mark.django_db
class TestEditResortCreateAdminGate:
    """The create endpoint must 404 for anyone who is not a superuser."""

    @pytest.mark.parametrize("client_factory", [Client, _ordinary_client])
    def test_returns_404_and_creates_nothing(
        self, client_factory: Any
    ) -> None:  # mock-typing-impractical
        """Anonymous and signed-in-but-ordinary are both refused."""
        _region_with_square_boundary()
        resp = _post_create(
            client_factory(),
            name="A",
            canton="VS",
            latitude=46.25,
            longitude=7.25,
        )
        assert resp.status_code == 404
        assert not Resort.objects.filter(name="A").exists()

    def test_superuser_creates_the_row(self) -> None:
        """The other half of the gate: a superuser's create lands."""
        _region_with_square_boundary()
        resp = _post_create(
            _superuser_client(),
            name="A",
            canton="VS",
            latitude=46.25,
            longitude=7.25,
        )
        assert resp.status_code == 201, resp.content
        assert Resort.objects.filter(name="A").exists()


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
        from apps.public.api import _point_in_polygon

        assert _point_in_polygon(lat=5.0, lon=5.0, polygon=self.SQUARE) is True

    def test_point_outside_polygon_returns_false(self) -> None:
        """A point clearly outside the outer ring is reported as outside."""
        from apps.public.api import _point_in_polygon

        assert _point_in_polygon(lat=20.0, lon=20.0, polygon=self.SQUARE) is False

    def test_point_inside_hole_returns_false(self) -> None:
        """
        A point inside a hole is reported as outside — the standard
        ray-cast over all rings flips parity correctly for holes.
        """
        from apps.public.api import _point_in_polygon

        assert (
            _point_in_polygon(lat=5.0, lon=5.0, polygon=self.SQUARE_WITH_HOLE) is False
        )

    def test_point_outside_hole_but_inside_outer_returns_true(self) -> None:
        """A point inside the outer ring but outside the hole is inside."""
        from apps.public.api import _point_in_polygon

        assert (
            _point_in_polygon(lat=2.0, lon=2.0, polygon=self.SQUARE_WITH_HOLE) is True
        )

    def test_empty_polygon_returns_false(self) -> None:
        """A degenerate polygon with no rings is treated as empty."""
        from apps.public.api import _point_in_polygon

        assert (
            _point_in_polygon(
                lat=0.0, lon=0.0, polygon={"type": "Polygon", "coordinates": []}
            )
            is False
        )

    def test_multipolygon_single_member_point_inside(self) -> None:
        """A MultiPolygon with one member behaves like the equivalent Polygon."""
        from apps.public.api import _point_in_polygon

        multi = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]],
            ],
        }
        assert _point_in_polygon(lat=5.0, lon=5.0, polygon=multi) is True

    def test_multipolygon_single_member_point_outside(self) -> None:
        """A point outside the only member of a MultiPolygon returns False."""
        from apps.public.api import _point_in_polygon

        multi = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]],
            ],
        }
        assert _point_in_polygon(lat=20.0, lon=20.0, polygon=multi) is False

    def test_multipolygon_point_inside_second_member(self) -> None:
        """A point inside the second (disjoint) member of a MultiPolygon returns True."""
        from apps.public.api import _point_in_polygon

        # Two disjoint squares: [0..10]×[0..10] and [20..30]×[20..30].
        multi = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]],
                [
                    [
                        [20.0, 20.0],
                        [30.0, 20.0],
                        [30.0, 30.0],
                        [20.0, 30.0],
                        [20.0, 20.0],
                    ]
                ],
            ],
        }
        # Inside second member only.
        assert _point_in_polygon(lat=25.0, lon=25.0, polygon=multi) is True

    def test_multipolygon_point_outside_all_members(self) -> None:
        """A point outside every member of a MultiPolygon returns False."""
        from apps.public.api import _point_in_polygon

        multi = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]],
                [
                    [
                        [20.0, 20.0],
                        [30.0, 20.0],
                        [30.0, 30.0],
                        [20.0, 30.0],
                        [20.0, 20.0],
                    ]
                ],
            ],
        }
        # Between the two squares.
        assert _point_in_polygon(lat=15.0, lon=15.0, polygon=multi) is False


class TestBboxOfPolygon:
    """Unit tests for ``_bbox_of_polygon`` helper."""

    SQUARE: dict = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]],
        ],
    }

    def test_polygon_returns_correct_bbox(self) -> None:
        """Bbox of the unit square is (0, 0, 10, 10)."""
        from apps.public.api import _bbox_of_polygon

        assert _bbox_of_polygon(self.SQUARE) == (0.0, 0.0, 10.0, 10.0)

    def test_empty_polygon_returns_none(self) -> None:
        """A Polygon with empty coordinates returns None."""
        from apps.public.api import _bbox_of_polygon

        assert _bbox_of_polygon({"type": "Polygon", "coordinates": []}) is None

    def test_multipolygon_single_member_matches_polygon(self) -> None:
        """A MultiPolygon with one member gives the same bbox as the Polygon."""
        from apps.public.api import _bbox_of_polygon

        multi = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]],
            ],
        }
        assert _bbox_of_polygon(multi) == (0.0, 0.0, 10.0, 10.0)

    def test_multipolygon_multiple_members_enclosing_bbox(self) -> None:
        """Bbox of a MultiPolygon spans all member polygons."""
        from apps.public.api import _bbox_of_polygon

        # Two disjoint squares: [0..10]×[0..10] and [20..30]×[20..30].
        multi = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]],
                [
                    [
                        [20.0, 20.0],
                        [30.0, 20.0],
                        [30.0, 30.0],
                        [20.0, 30.0],
                        [20.0, 20.0],
                    ]
                ],
            ],
        }
        assert _bbox_of_polygon(multi) == (0.0, 0.0, 30.0, 30.0)


@pytest.mark.django_db
class TestRegionForPoint:
    """
    Tests for the region lookup that ``edit_resort_save`` uses
    to auto-rebind a resort's parent region from its saved location.
    """

    def test_finds_containing_region(self) -> None:
        """Returns the region whose polygon contains the test point."""
        from apps.public.api import _region_for_point

        target = MicroRegionFactory.create(
            region_id="CH-A",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[7.0, 46.0], [8.0, 46.0], [8.0, 47.0], [7.0, 47.0], [7.0, 46.0]],
                ],
            },
        )
        # An adjacent region that does not contain the test point.
        MicroRegionFactory.create(
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
        from apps.public.api import _region_for_point

        MicroRegionFactory.create(
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
        from apps.public.api import _region_for_point

        MicroRegionFactory.create(region_id="CH-NOBOUND", boundary=None)
        assert _region_for_point(lat=46.5, lon=7.5) is None


@pytest.mark.django_db
class TestMapViewEditMode:
    """Boot the panel only when querystring and superuser bit both agree.

    Both the ``?edit=resorts`` querystring **and** ``request.user
    .is_superuser`` must hold for ``home()`` to render the edit-resorts
    panel (SNOW-86 / SNOW-344: moved from map_view to home(); SNOW-724
    swapped the ``edit_map`` flag for the superuser check).
    """

    def test_query_string_as_superuser_renders_panel(self) -> None:
        """``?edit=resorts`` as a superuser shows the panel."""
        resp = _superuser_client().get(reverse("public:home") + "?edit=resorts")
        assert resp.status_code == 200
        assert b"edit-resorts-panel" in resp.content

    @pytest.mark.parametrize("client_factory", [Client, _ordinary_client])
    def test_query_string_without_superuser_silent_fallback(
        self, client_factory: Any
    ) -> None:  # mock-typing-impractical
        """``?edit=resorts`` for everyone else renders the normal map."""
        resp = client_factory().get(reverse("public:home") + "?edit=resorts")
        assert resp.status_code == 200
        assert b"edit-resorts-panel" not in resp.content

    def test_the_panel_offers_a_way_out_and_a_way_across(self) -> None:
        """SNOW-755: the panel had no close control and no sibling link.

        The only ways out of edit mode were hand-editing the URL or
        clicking the wordmark, neither of which is an affordance, and
        reaching the other editor meant going back to the staff menu.
        """
        content = (
            _superuser_client()
            .get(reverse("public:home") + "?edit=resorts")
            .content.decode()
        )

        assert 'aria-label="Close editor"' in content
        assert f'href="{reverse("public:home")}"' in content
        assert f'href="{reverse("public:home")}?edit=locations"' in content

    def test_the_bar_marks_resorts_as_the_open_editor(self) -> None:
        """``aria-current`` says which of the two the reader is in."""
        content = (
            _superuser_client()
            .get(reverse("public:home") + "?edit=resorts")
            .content.decode()
        )
        marked = content.split('aria-current="page"')[0]

        assert marked.rstrip().endswith('?edit=resorts"')

    def test_no_query_string_does_not_render_panel(self) -> None:
        """Without the querystring the panel is absent, even for a superuser."""
        resp = _superuser_client().get(reverse("public:home"))
        assert resp.status_code == 200
        assert b"edit-resorts-panel" not in resp.content
