"""
tests/public/test_edit_locations_api.py — SNOW-755 in-map location editor.

Covers the five endpoints behind ``?edit=locations``:

* ``GET  api:edit_locations_queue``   — the curated estate, each location's
  links, and the resort catalogue to link against.
* ``POST api:edit_location_create``   — mint a Location and its first link.
* ``POST api:edit_location_save``     — move / rename / re-classify one.
* ``POST api:edit_location_link``     — attach an EXISTING location to a
  second resort.
* ``POST api:edit_location_unlink``   — drop one link, keep the location.

All five are superuser-only, so every gate is asserted twice — on an
anonymous visitor and on an authenticated non-superuser — exactly as
``test_edit_resorts_api.py`` does for the editor this one was modelled on.

``link`` is the endpoint with no resort-editor equivalent and the reason
the estate is worth having: Mont Fort must end up as one row that two
resorts reference, not two rows on the same summit. That is asserted by
row count, not by response shape, because a second Location row is the
failure this endpoint exists to prevent.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.locations.models import Location, ResortLocation
from apps.regions.models import Resort
from tests.factories import (
    LocationFactory,
    ResortFactory,
    ResortLocationFactory,
    UserFactory,
)

# A point inside ``_SWISS_BBOX`` — Mont Fort, the summit four resorts of
# the 4 Vallées share, which is the case the estate exists for.
MONT_FORT_LAT = 46.10361
MONT_FORT_LON = 7.29889


def _superuser_client() -> Client:
    """Return a test client signed in as a superuser — the whole audience."""
    client = Client()
    client.force_login(UserFactory.create(is_superuser=True))
    return client


def _ordinary_client() -> Client:
    """Return a client signed in as a non-superuser.

    The interesting negative case: being signed in is not enough.
    """
    client = Client()
    client.force_login(UserFactory.create(is_staff=False))
    return client


def _post(client: Client, url: str, payload: dict[str, Any]) -> Any:
    """POST ``payload`` as JSON, as the panel's fetch() does.

    Args:
        client: The test client to send with.
        url: The endpoint.
        payload: The request body.

    Returns:
        The response.

    """
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _create_url() -> str:
    """Return the create endpoint's URL."""
    return reverse("api:edit_location_create")


def _save_url(location: Location) -> str:
    """Return the save endpoint's URL for ``location``."""
    return reverse("api:edit_location_save", args=[location.pk])


def _link_url(location: Location) -> str:
    """Return the link endpoint's URL for ``location``."""
    return reverse("api:edit_location_link", args=[location.pk])


def _unlink_url(link: ResortLocation) -> str:
    """Return the unlink endpoint's URL for ``link``."""
    return reverse("api:edit_location_unlink", args=[link.pk])


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSuperuserGate:
    """Every endpoint 404s for anyone who is not a superuser."""

    def test_queue_is_closed_to_the_public(self) -> None:
        """An anonymous visitor sees a 404, not a 403."""
        assert Client().get(reverse("api:edit_locations_queue")).status_code == 404

    def test_queue_is_closed_to_an_ordinary_user(self) -> None:
        """Signing in does not open the editor."""
        resp = _ordinary_client().get(reverse("api:edit_locations_queue"))
        assert resp.status_code == 404

    def test_create_is_closed_to_the_public(self) -> None:
        """The write endpoints refuse before they parse anything."""
        resp = _post(Client(), _create_url(), {"name": "Mont Fort"})
        assert resp.status_code == 404
        assert Location.objects.count() == 0

    def test_create_is_closed_to_an_ordinary_user(self) -> None:
        """Same for an authenticated non-superuser."""
        resp = _post(_ordinary_client(), _create_url(), {"name": "Mont Fort"})
        assert resp.status_code == 404
        assert Location.objects.count() == 0

    def test_save_is_closed_to_the_public(self) -> None:
        """A location cannot be moved by an anonymous caller."""
        location = LocationFactory.create(name="Mont Fort")
        assert _post(Client(), _save_url(location), {}).status_code == 404

    def test_save_is_closed_to_an_ordinary_user(self) -> None:
        """Nor by a signed-in one."""
        location = LocationFactory.create(name="Mont Fort")
        resp = _post(_ordinary_client(), _save_url(location), {})
        assert resp.status_code == 404

    def test_link_is_closed_to_the_public(self) -> None:
        """No link is minted for an anonymous caller."""
        location = LocationFactory.create(name="Mont Fort")
        resort = ResortFactory.create(name="Nendaz")
        resp = _post(
            Client(), _link_url(location), {"resort_id": resort.pk, "role": "TOP"}
        )
        assert resp.status_code == 404
        assert ResortLocation.objects.count() == 0

    def test_link_is_closed_to_an_ordinary_user(self) -> None:
        """Same for an authenticated non-superuser."""
        location = LocationFactory.create(name="Mont Fort")
        resort = ResortFactory.create(name="Nendaz")
        resp = _post(
            _ordinary_client(),
            _link_url(location),
            {"resort_id": resort.pk, "role": "TOP"},
        )
        assert resp.status_code == 404
        assert ResortLocation.objects.count() == 0

    def test_unlink_is_closed_to_the_public(self) -> None:
        """The link survives an anonymous unlink attempt."""
        link = ResortLocationFactory.create()
        assert _post(Client(), _unlink_url(link), {}).status_code == 404
        assert ResortLocation.objects.filter(pk=link.pk).exists()

    def test_unlink_is_closed_to_an_ordinary_user(self) -> None:
        """And a signed-in one."""
        link = ResortLocationFactory.create()
        assert _post(_ordinary_client(), _unlink_url(link), {}).status_code == 404
        assert ResortLocation.objects.filter(pk=link.pk).exists()


# ---------------------------------------------------------------------------
# edit_locations_queue
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueue:
    """Tests for ``GET /api/edit/locations/queue/``."""

    def test_response_shape(self) -> None:
        """The panel needs all four blocks to render at all."""
        resort = ResortFactory.create(name="Verbier")
        location = LocationFactory.create(
            name="Mont Fort",
            kind=Location.KIND.PEAK,
            latitude=MONT_FORT_LAT,
            longitude=MONT_FORT_LON,
        )
        ResortLocationFactory.create(resort=resort, location=location, role="TOP")

        body = _superuser_client().get(reverse("api:edit_locations_queue")).json()

        assert [entry["name"] for entry in body["locations"]] == ["Mont Fort"]
        assert [entry["name"] for entry in body["resorts"]] == ["Verbier"]
        assert {kind["value"] for kind in body["kinds"]} == set(Location.KIND.values)
        assert {role["value"] for role in body["roles"]} == set(
            ResortLocation.ROLE.values
        )

    def test_a_location_carries_its_links(self) -> None:
        """Each row lists the resorts that reach it, without a second call."""
        verbier = ResortFactory.create(name="Verbier")
        nendaz = ResortFactory.create(name="Nendaz")
        location = LocationFactory.create(name="Mont Fort")
        ResortLocationFactory.create(resort=verbier, location=location, role="TOP")
        ResortLocationFactory.create(resort=nendaz, location=location, role="MID")

        body = _superuser_client().get(reverse("api:edit_locations_queue")).json()

        links = body["locations"][0]["links"]
        assert [link["resort_name"] for link in links] == ["Nendaz", "Verbier"]
        assert [link["role"] for link in links] == ["MID", "TOP"]

    def test_kind_and_role_are_independent(self) -> None:
        """A peak can be one resort's mid-station; neither field derives.

        ``Location.kind`` describes the place, ``ResortLocation.role``
        describes one resort's relationship to it, and collapsing them
        would make a shared summit unrepresentable.
        """
        location = LocationFactory.create(name="Attelas", kind=Location.KIND.PEAK)
        ResortLocationFactory.create(location=location, role="MID")

        body = _superuser_client().get(reverse("api:edit_locations_queue")).json()

        entry = body["locations"][0]
        assert entry["kind"] == "PEAK"
        assert entry["links"][0]["role"] == "MID"

    def test_anonymous_locations_are_not_listed(self) -> None:
        """Rows minted from favourites are user data, not curated estate."""
        LocationFactory.create(name="Mont Fort")
        LocationFactory.create(anonymous=True)

        body = _superuser_client().get(reverse("api:edit_locations_queue")).json()

        assert [entry["name"] for entry in body["locations"]] == ["Mont Fort"]

    def test_query_count_does_not_grow_with_the_estate(
        self, django_assert_max_num_queries: Any
    ) -> None:
        """The links are prefetched, so the endpoint is not an N+1.

        Measured against a ten-location estate with two links each — the
        shape the estate reaches after SNOW-732's curation, and the shape
        that turns a missing ``prefetch_related`` into 30 queries.
        """
        resorts = [ResortFactory.create(name=f"Resort {n}") for n in range(2)]
        for n in range(10):
            location = LocationFactory.create(name=f"Location {n}")
            for resort in resorts:
                ResortLocationFactory.create(resort=resort, location=location)
        client = _superuser_client()

        with django_assert_max_num_queries(10):
            resp = client.get(reverse("api:edit_locations_queue"))

        assert len(resp.json()["locations"]) == 10


# ---------------------------------------------------------------------------
# edit_location_create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreate:
    """Tests for ``POST /api/edit/locations/create/``."""

    def test_mints_a_location_and_its_first_link(self) -> None:
        """One click produces both rows, in one transaction."""
        resort = ResortFactory.create(name="Verbier")

        resp = _post(
            _superuser_client(),
            _create_url(),
            {
                "name": "Mont Fort",
                "kind": "PEAK",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
                "resort_id": resort.pk,
                "role": "TOP",
                "is_primary": False,
            },
        )

        assert resp.status_code == 201
        location = Location.objects.get(name="Mont Fort")
        assert location.kind == Location.KIND.PEAK
        assert location.latitude == pytest.approx(MONT_FORT_LAT)
        link = ResortLocation.objects.get(location=location)
        assert link.resort == resort
        assert link.role == ResortLocation.ROLE.TOP
        body = resp.json()
        assert body["id"] == location.pk
        assert body["links"][0]["resort_name"] == "Verbier"

    def test_a_blank_kind_is_allowed(self) -> None:
        """An unclassified place is a legitimate row, as in the sheet."""
        resort = ResortFactory.create()

        resp = _post(
            _superuser_client(),
            _create_url(),
            {
                "name": "Somewhere",
                "kind": "",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
                "resort_id": resort.pk,
                "role": "MID",
            },
        )

        assert resp.status_code == 201
        assert Location.objects.get(name="Somewhere").kind == ""

    def test_a_blank_name_is_rejected(self) -> None:
        """Naming is the curation act — a nameless row is the other kind."""
        resort = ResortFactory.create()

        resp = _post(
            _superuser_client(),
            _create_url(),
            {
                "name": "  ",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
                "resort_id": resort.pk,
                "role": "TOP",
            },
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_identity"
        assert Location.objects.count() == 0

    def test_an_unrecognised_kind_is_rejected_not_blanked(self) -> None:
        """A typo must not silently strip a summit of its classification."""
        resort = ResortFactory.create()

        resp = _post(
            _superuser_client(),
            _create_url(),
            {
                "name": "Mont Fort",
                "kind": "SUMMIT",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
                "resort_id": resort.pk,
                "role": "TOP",
            },
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_kind"
        assert Location.objects.count() == 0

    def test_a_missing_role_is_rejected(self) -> None:
        """A link that does not say what the place is carries nothing."""
        resort = ResortFactory.create()

        resp = _post(
            _superuser_client(),
            _create_url(),
            {
                "name": "Mont Fort",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
                "resort_id": resort.pk,
            },
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_role"
        assert Location.objects.count() == 0

    def test_an_unknown_resort_is_rejected(self) -> None:
        """No orphan location is left behind by a bad resort id."""
        resp = _post(
            _superuser_client(),
            _create_url(),
            {
                "name": "Mont Fort",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
                "resort_id": 999999,
                "role": "TOP",
            },
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "unknown_resort"
        assert Location.objects.count() == 0

    def test_non_numeric_coordinates_are_rejected(self) -> None:
        """The pin is the coordinate — it has to be a number."""
        resort = ResortFactory.create()

        resp = _post(
            _superuser_client(),
            _create_url(),
            {
                "name": "Mont Fort",
                "latitude": "up a bit",
                "longitude": MONT_FORT_LON,
                "resort_id": resort.pk,
                "role": "TOP",
            },
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_coords"
        assert Location.objects.count() == 0

    def test_coordinates_outside_switzerland_are_rejected(self) -> None:
        """Every resort is CH-*, so the estate is Swiss by construction."""
        resort = ResortFactory.create()

        resp = _post(
            _superuser_client(),
            _create_url(),
            {
                "name": "Ben Nevis",
                "latitude": 56.797,
                "longitude": -5.003,
                "resort_id": resort.pk,
                "role": "TOP",
            },
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "out_of_bounds"
        assert Location.objects.count() == 0

    def test_a_malformed_body_is_rejected(self) -> None:
        """Not JSON, not an object — both are the same 400."""
        client = _superuser_client()

        resp = client.post(
            _create_url(), data="not json", content_type="application/json"
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_json"

    def test_a_placed_pin_is_stored_to_five_decimals(self) -> None:
        """The DB must not hold precision the sheet cannot carry.

        ``dump_locations_sheets`` writes five decimals, so storing more
        would make every ``import_locations`` run report an update that
        is only trailing digits.
        """
        resort = ResortFactory.create()

        _post(
            _superuser_client(),
            _create_url(),
            {
                "name": "Mont Fort",
                "latitude": 46.1036123456,
                "longitude": 7.2988987654,
                "resort_id": resort.pk,
                "role": "TOP",
            },
        )

        location = Location.objects.get(name="Mont Fort")
        assert location.latitude == 46.10361
        assert location.longitude == 7.2989


# ---------------------------------------------------------------------------
# edit_location_save
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSave:
    """Tests for ``POST /api/edit/locations/<id>/save/``."""

    def test_moves_the_pin(self) -> None:
        """The coordinate is what the editor exists to set."""
        location = LocationFactory.create(
            name="Mont Fort", latitude=46.0, longitude=7.0
        )

        resp = _post(
            _superuser_client(),
            _save_url(location),
            {
                "name": "Mont Fort",
                "kind": "PEAK",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
            },
        )

        assert resp.status_code == 200
        location.refresh_from_db()
        assert location.latitude == pytest.approx(MONT_FORT_LAT)
        assert location.longitude == pytest.approx(MONT_FORT_LON)

    def test_renames_and_reclassifies(self) -> None:
        """Both curated fields are editable from the same round trip."""
        location = LocationFactory.create(name="Mont Fort", kind=Location.KIND.MID)

        _post(
            _superuser_client(),
            _save_url(location),
            {
                "name": "Mont Fort (summit)",
                "kind": "PEAK",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
            },
        )

        location.refresh_from_db()
        assert location.name == "Mont Fort (summit)"
        assert location.kind == Location.KIND.PEAK

    def test_moving_the_pin_clears_the_resolved_elevation(self) -> None:
        """A height resolved from the old coordinate is now a lie.

        Leaving it is worse than clearing it: a stale elevation is what
        makes a mis-pinned summit look plausible.
        """
        location = LocationFactory.create(
            name="Mont Fort", resolved=True, latitude=46.0, longitude=7.0
        )

        _post(
            _superuser_client(),
            _save_url(location),
            {
                "name": "Mont Fort",
                "kind": "PEAK",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
            },
        )

        location.refresh_from_db()
        assert location.elevation_m is None

    def test_a_rename_alone_keeps_the_resolved_elevation(self) -> None:
        """The height belongs to the coordinate, which did not change."""
        location = LocationFactory.create(
            name="Mont Fort",
            resolved=True,
            latitude=MONT_FORT_LAT,
            longitude=MONT_FORT_LON,
        )

        _post(
            _superuser_client(),
            _save_url(location),
            {
                "name": "Mont Fort (summit)",
                "kind": "PEAK",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
            },
        )

        location.refresh_from_db()
        assert location.elevation_m == pytest.approx(1500.0)

    def test_the_edit_reaches_every_linked_resort(self) -> None:
        """Sharing one row is the point — one save re-pins all of them."""
        location = LocationFactory.create(name="Mont Fort")
        verbier = ResortFactory.create(name="Verbier")
        nendaz = ResortFactory.create(name="Nendaz")
        ResortLocationFactory.create(resort=verbier, location=location, role="TOP")
        ResortLocationFactory.create(resort=nendaz, location=location, role="TOP")

        resp = _post(
            _superuser_client(),
            _save_url(location),
            {
                "name": "Mont Fort",
                "kind": "PEAK",
                "latitude": MONT_FORT_LAT,
                "longitude": MONT_FORT_LON,
            },
        )

        assert {link["resort_name"] for link in resp.json()["links"]} == {
            "Verbier",
            "Nendaz",
        }
        assert Location.objects.count() == 1

    def test_a_rename_leaves_a_full_precision_coordinate_alone(self) -> None:
        """A rename is not a re-placement, at any stored precision.

        ``latitude`` is a plain FloatField and the admin — a live write
        path this editor does not own — imposes no rounding, so a stored
        coordinate routinely carries more than the five decimals the
        panel round-trips. Comparing the rounded incoming value against
        the raw stored one made every such row look moved, so a save that
        only renamed it wiped the resolved elevation AND truncated the
        coordinate to 5dp. Both are silent, and neither was asked for.
        """
        location = LocationFactory.create(
            name="Mont Fort",
            resolved=True,
            latitude=46.1036123456,
            longitude=7.2988987654,
        )

        resp = _post(
            _superuser_client(),
            _save_url(location),
            {
                "name": "Mont Fort (summit)",
                "kind": "PEAK",
                # What the panel sends back for an untouched pin: the
                # stored coordinate, rendered to five decimals.
                "latitude": 46.10361,
                "longitude": 7.2989,
            },
        )

        assert resp.status_code == 200
        location.refresh_from_db()
        assert location.name == "Mont Fort (summit)"
        assert location.latitude == 46.1036123456
        assert location.longitude == 7.2988987654
        assert location.elevation_m == pytest.approx(1500.0)

    def test_a_genuine_move_from_full_precision_still_clears(self) -> None:
        """The fix above must not turn the clear-on-move into a no-op."""
        location = LocationFactory.create(
            name="Mont Fort",
            resolved=True,
            latitude=46.1036123456,
            longitude=7.2988987654,
        )

        _post(
            _superuser_client(),
            _save_url(location),
            {"name": "Mont Fort", "kind": "PEAK", "latitude": 46.2, "longitude": 7.4},
        )

        location.refresh_from_db()
        assert location.latitude == pytest.approx(46.2)
        assert location.elevation_m is None

    def test_an_anonymous_location_cannot_be_named_here(self) -> None:
        """The editor curates the estate the sheets own, and nothing else.

        A location minted from a favourite is user data. Naming it here
        would move it across that boundary silently, and its links would
        then be the sheets' to carry — see ``dump_locations_sheets``.
        """
        location = LocationFactory.create(anonymous=True)

        resp = _post(
            _superuser_client(),
            _save_url(location),
            {"name": "Poached", "latitude": MONT_FORT_LAT, "longitude": MONT_FORT_LON},
        )

        assert resp.status_code == 404
        location.refresh_from_db()
        assert location.name == ""

    def test_an_unknown_location_is_a_404(self) -> None:
        """No row, no edit."""
        resp = _post(
            _superuser_client(),
            reverse("api:edit_location_save", args=[999999]),
            {"name": "X", "latitude": MONT_FORT_LAT, "longitude": MONT_FORT_LON},
        )
        assert resp.status_code == 404

    def test_out_of_bbox_coordinates_are_rejected(self) -> None:
        """The row keeps its old coordinate rather than half-moving."""
        location = LocationFactory.create(
            name="Mont Fort", latitude=46.0, longitude=7.0
        )

        resp = _post(
            _superuser_client(),
            _save_url(location),
            {"name": "Mont Fort", "latitude": 56.797, "longitude": -5.003},
        )

        assert resp.status_code == 400
        location.refresh_from_db()
        assert location.latitude == pytest.approx(46.0)


# ---------------------------------------------------------------------------
# edit_location_link
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLink:
    """Tests for ``POST /api/edit/locations/<id>/link/``.

    The endpoint the estate is worth having: one summit, several resorts,
    one row.
    """

    def test_attaches_an_existing_location_to_a_second_resort(self) -> None:
        """No second Location row is minted — that is the whole point."""
        location = LocationFactory.create(name="Mont Fort")
        verbier = ResortFactory.create(name="Verbier")
        nendaz = ResortFactory.create(name="Nendaz")
        ResortLocationFactory.create(resort=verbier, location=location, role="TOP")

        resp = _post(
            _superuser_client(),
            _link_url(location),
            {"resort_id": nendaz.pk, "role": "TOP"},
        )

        assert resp.status_code == 201
        assert Location.objects.count() == 1
        assert ResortLocation.objects.filter(location=location).count() == 2
        assert {link["resort_name"] for link in resp.json()["links"]} == {
            "Verbier",
            "Nendaz",
        }

    def test_the_second_resort_can_give_it_a_different_role(self) -> None:
        """One point is plausibly one resort's top and another's mid."""
        location = LocationFactory.create(name="Attelas", kind=Location.KIND.PEAK)
        verbier = ResortFactory.create(name="Verbier")
        bruson = ResortFactory.create(name="Bruson")
        ResortLocationFactory.create(resort=verbier, location=location, role="MID")

        _post(
            _superuser_client(),
            _link_url(location),
            {"resort_id": bruson.pk, "role": "TOP"},
        )

        roles = dict(
            ResortLocation.objects.filter(location=location).values_list(
                "resort__name", "role"
            )
        )
        assert roles == {"Verbier": "MID", "Bruson": "TOP"}

    def test_is_primary_is_honoured(self) -> None:
        """The resort page's hero link is set at link time."""
        location = LocationFactory.create(name="Verbier village")
        resort = ResortFactory.create(name="Verbier")

        _post(
            _superuser_client(),
            _link_url(location),
            {"resort_id": resort.pk, "role": "BASE", "is_primary": True},
        )

        assert ResortLocation.objects.get(location=location).is_primary is True

    def test_a_duplicate_link_is_a_400_not_a_500(self) -> None:
        """unique_together is a routine operator mistake, not a crash.

        From the panel, the link about to be added and the one already
        listed look the same until the row is read — so the collision has
        to come back as a sentence, not a stack trace.
        """
        location = LocationFactory.create(name="Mont Fort")
        resort = ResortFactory.create(name="Verbier")
        ResortLocationFactory.create(resort=resort, location=location, role="TOP")

        resp = _post(
            _superuser_client(),
            _link_url(location),
            {"resort_id": resort.pk, "role": "MID"},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "duplicate_link"
        assert ResortLocation.objects.filter(location=location).count() == 1

    def test_an_unrecognised_role_is_rejected(self) -> None:
        """No link is created for a role the model does not have."""
        location = LocationFactory.create(name="Mont Fort")
        resort = ResortFactory.create(name="Verbier")

        resp = _post(
            _superuser_client(),
            _link_url(location),
            {"resort_id": resort.pk, "role": "SUMMIT"},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_role"
        assert ResortLocation.objects.count() == 0

    def test_an_anonymous_location_cannot_be_linked(self) -> None:
        """The invariant ``dump_locations_sheets``'s round trip rests on.

        The dump emits only curated locations, so a link hung on an
        anonymous row is dropped from the links sheet and cannot survive
        a round trip. This endpoint is one of the paths that must not
        create one.
        """
        location = LocationFactory.create(anonymous=True)
        resort = ResortFactory.create(name="Verbier")

        resp = _post(
            _superuser_client(),
            _link_url(location),
            {"resort_id": resort.pk, "role": "TOP"},
        )

        assert resp.status_code == 404
        assert ResortLocation.objects.count() == 0

    def test_an_unknown_location_is_a_404(self) -> None:
        """Linking to nothing is not a 400 with a message, it is a 404."""
        resort = ResortFactory.create()
        resp = _post(
            _superuser_client(),
            reverse("api:edit_location_link", args=[999999]),
            {"resort_id": resort.pk, "role": "TOP"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# edit_location_unlink
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnlink:
    """Tests for ``POST /api/edit/locations/links/<id>/unlink/``."""

    def test_drops_the_link_and_keeps_the_location(self) -> None:
        """Mont Fort outlives Nendaz's interest in it."""
        location = LocationFactory.create(name="Mont Fort")
        verbier = ResortFactory.create(name="Verbier")
        nendaz = ResortFactory.create(name="Nendaz")
        ResortLocationFactory.create(resort=verbier, location=location, role="TOP")
        drop = ResortLocationFactory.create(
            resort=nendaz, location=location, role="TOP"
        )

        resp = _post(_superuser_client(), _unlink_url(drop), {})

        assert resp.status_code == 200
        assert Location.objects.filter(pk=location.pk).exists()
        assert [link["resort_name"] for link in resp.json()["links"]] == ["Verbier"]

    def test_the_last_link_can_go_without_taking_the_location(self) -> None:
        """A curated place with no links is still curated, and still in git."""
        location = LocationFactory.create(name="Mont Fort")
        link = ResortLocationFactory.create(location=location, role="TOP")

        resp = _post(_superuser_client(), _unlink_url(link), {})

        assert resp.status_code == 200
        assert resp.json()["links"] == []
        assert Location.objects.filter(pk=location.pk).exists()

    def test_an_unknown_link_is_a_404(self) -> None:
        """Nothing to unlink."""
        resp = _post(
            _superuser_client(),
            reverse("api:edit_location_unlink", args=[999999]),
            {},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# The page gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEditLocationsPageGate:
    """``?edit=locations`` renders the panel only for a superuser."""

    def test_superuser_sees_the_panel(self) -> None:
        """The querystring and the superuser bit both agree."""
        resp = _superuser_client().get(reverse("public:home") + "?edit=locations")
        assert resp.status_code == 200
        assert b"edit-locations-panel" in resp.content

    @pytest.mark.parametrize("client_factory", [Client, _ordinary_client])
    def test_everyone_else_gets_the_normal_map(self, client_factory: Any) -> None:
        """The URL stays safe to bookmark and safe to paste into a chat."""
        resp = client_factory().get(reverse("public:home") + "?edit=locations")
        assert resp.status_code == 200
        assert b"edit-locations-panel" not in resp.content

    def test_the_panel_carries_all_five_endpoint_urls(self) -> None:
        """The JS reads every URL off the panel's data attributes.

        Three of them are ``__ID__`` templates the panel string-replaces
        at runtime; a missing one is a button that silently POSTs to the
        page it is on.
        """
        content = (
            _superuser_client()
            .get(reverse("public:home") + "?edit=locations")
            .content.decode()
        )

        for attribute in (
            f'data-queue-url="{reverse("api:edit_locations_queue")}"',
            f'data-create-url="{reverse("api:edit_location_create")}"',
            'data-save-url-template="/api/edit/locations/__ID__/save/"',
            'data-link-url-template="/api/edit/locations/__ID__/link/"',
            'data-unlink-url-template="/api/edit/locations/links/__ID__/unlink/"',
        ):
            assert attribute in content

    def test_the_core_module_loads_before_the_wiring(self) -> None:
        """``map_edit_locations.js`` reads the frozen export at parse time.

        Both tags are deferred, so document order is execution order and
        the wrong order is a TypeError on the first line of the IIFE.
        """
        content = (
            _superuser_client()
            .get(reverse("public:home") + "?edit=locations")
            .content.decode()
        )

        assert content.index("map_edit_locations_core.js") < content.index(
            "js/map_edit_locations.js"
        )

    def test_the_normal_map_loads_neither_script(self) -> None:
        """A page nobody is editing is exactly the page it was before."""
        content = Client().get(reverse("public:home")).content.decode()

        assert "map_edit_locations" not in content

    def test_the_panel_offers_a_way_out_and_a_way_across(self) -> None:
        """SNOW-755: the shared bar is what makes edit mode escapable.

        Both are plain links: edit mode is query-string state, so closing
        is a navigation to the bare home URL and switching is a link to
        the other ``?edit=`` value. Neither needs the panel's JS to have
        loaded, or to have survived a half-finished placement.
        """
        content = (
            _superuser_client()
            .get(reverse("public:home") + "?edit=locations")
            .content.decode()
        )

        assert 'aria-label="Close editor"' in content
        assert f'href="{reverse("public:home")}"' in content
        assert f'href="{reverse("public:home")}?edit=resorts"' in content

    def test_the_bar_marks_locations_as_the_open_editor(self) -> None:
        """``aria-current`` follows the panel, not the markup order."""
        content = (
            _superuser_client()
            .get(reverse("public:home") + "?edit=locations")
            .content.decode()
        )
        marked = content.split('aria-current="page"')[0]

        assert marked.rstrip().endswith('?edit=locations"')

    @pytest.mark.parametrize("client_factory", [Client, _ordinary_client])
    def test_the_bar_is_behind_the_same_gate_as_the_panel(
        self, client_factory: Any
    ) -> None:
        """A close control on the public map would advertise the editor."""
        content = (
            client_factory()
            .get(reverse("public:home") + "?edit=locations")
            .content.decode()
        )

        assert "Close editor" not in content

    def test_an_unknown_edit_target_is_ignored(self) -> None:
        """Not an error — simply not an editor."""
        resp = _superuser_client().get(reverse("public:home") + "?edit=wombats")
        assert resp.status_code == 200
        assert b"edit-locations-panel" not in resp.content
        assert b"edit-resorts-panel" not in resp.content


@pytest.mark.django_db
def test_the_resort_catalogue_carries_its_coordinates() -> None:
    """The panel frames the map on a resort before the pin is placed."""
    ResortFactory.create(name="Verbier", latitude=46.0961, longitude=7.2286)

    body = _superuser_client().get(reverse("api:edit_locations_queue")).json()

    entry = body["resorts"][0]
    assert entry["latitude"] == pytest.approx(46.0961)
    assert entry["longitude"] == pytest.approx(7.2286)
    assert entry["region_id"] == Resort.objects.get().region.region_id
