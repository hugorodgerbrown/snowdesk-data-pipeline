"""
tests/routes/test_views.py — Tests for apps.routes.views.

Covers:
  route_create — anonymous → 403; non-HTMX → 400; no file → 400;
                 a valid .gpx → 200 and a stored Route;
                 over ROUTE_UPLOAD_MAX_BYTES → 413 (and never parsed);
                 a non-GPX body → 400; a waypoint-only .gpx → 400;
                 at ROUTES_MAX_PER_USER → 409 with the limit partial;
                 rate-limited → 429.
  route_rename — owner renames; another user's uuid → 404 (no existence
                 oracle); an unknown uuid → 404; name over max_length →
                 400; anonymous → 403; non-HTMX → 400.
  route_delete — owner deletes; another user's uuid → 404 and the row
                 survives; anonymous → 403; non-HTMX → 400.
  route_list   — SNOW-686: the owner's own routes only; the empty state;
                 anonymous → 403; non-HTMX → 400; POST → 405; both row
                 variants render, and an unknown variant falls back to the
                 default rather than reaching a template path; the map
                 variant's name is a button carrying the route's bbox, and
                 the account variant's is not.
  routes_geojson — SNOW-687: anonymous → 403 JSON; served without an
                 HX-Request header (a fetch(), not a swap); private/no-store;
                 LineString geometry carrying ``points`` verbatim in
                 [lon, lat, ele] order; every property the popup and the
                 fit-to-bounds read; a null ``ascent_m`` surviving as null
                 (and a zero staying a zero); owner scoping; freshness
                 headers WITHOUT X-Data-Unsafe-After; and a query count that
                 does not grow with the number of routes.
  SNOW-764's widening of the last two — route_list and routes_geojson
                 answering for an anonymous session that holds a pending
                 share; the pending rows sorting above the owned ones; a
                 pending FEATURE carrying token + pending and NO uuid; and
                 the headers unchanged.

Scoped to the Django test client throughout — per CLAUDE.md's layer rules
there is nothing browser-shaped in this ticket, and a 404 needs no browser.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.freshness import DEFAULT_MAX_AGE_SECONDS
from apps.routes.constants import ROUTE_LIST_MAP_VARIANT
from apps.routes.models import Route
from tests.factories import RouteFactory, RouteShareFactory, UserFactory

FIXTURES = Path(__file__).parent / "fixtures"

CREATE_URL = "/routes/partials/create/"

HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


def _fixture(name: str) -> bytes:
    """Return the raw bytes of a GPX fixture."""
    return (FIXTURES / name).read_bytes()


def _upload(name: str = "single_track.gpx") -> SimpleUploadedFile:
    """Build a multipart upload from a GPX fixture."""
    return SimpleUploadedFile(name, _fixture(name), content_type="application/gpx+xml")


def _rename_url(uuid: object) -> str:
    """Build the rename URL for a route's uuid."""
    return f"/routes/partials/{uuid}/rename/"


def _delete_url(uuid: object) -> str:
    """Build the delete URL for a route's uuid."""
    return f"/routes/partials/{uuid}/delete/"


LIST_URL = "/routes/partials/list/"

# The map sheet's variant, as apps.public.views._routes_context builds it.
MAP_LIST_URL = f"{LIST_URL}?variant={ROUTE_LIST_MAP_VARIANT}"

# SNOW-687: the map layer's data endpoint. Outside the ``partials/`` prefix
# on purpose — it is a plain-JSON fetch target, not an HTMX fragment.
GEOJSON_URL = "/routes/routes.geojson"


# ---------------------------------------------------------------------------
# route_create — POST /routes/partials/create/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteCreateGates:
    """Auth and HTMX gates."""

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous POST returns 403."""
        response = client.post(CREATE_URL, {"file": _upload()}, **HTMX_HEADERS)
        assert response.status_code == 403

    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain POST without HX-Request returns 400 (invariant 4)."""
        client.force_login(UserFactory.create())
        response = client.post(CREATE_URL, {"file": _upload()})
        assert response.status_code == 400

    def test_get_is_not_allowed(self, client: Client) -> None:
        """The endpoint is POST-only."""
        client.force_login(UserFactory.create())
        response = client.get(CREATE_URL, **HTMX_HEADERS)
        assert response.status_code == 405


@pytest.mark.django_db
class TestRouteCreateSuccess:
    """A valid .gpx creates a row and returns the route-row partial."""

    def test_valid_upload_creates_a_route(self, client: Client) -> None:
        """200, one row, derived figures stored."""
        user = UserFactory.create()
        client.force_login(user)

        response = client.post(CREATE_URL, {"file": _upload()}, **HTMX_HEADERS)

        assert response.status_code == 200
        route = Route.objects.get(user=user)
        assert route.name == "Col de Balme"
        assert route.point_count == 4
        assert route.ascent_m == pytest.approx(300.0)

    def test_source_filename_is_recorded(self, client: Client) -> None:
        """The upload's name is kept as a label."""
        user = UserFactory.create()
        client.force_login(user)

        client.post(CREATE_URL, {"file": _upload()}, **HTMX_HEADERS)

        assert Route.objects.get(user=user).source_filename == "single_track.gpx"

    def test_response_renders_the_route_row_partial(self, client: Client) -> None:
        """The fragment carries the row element HTMX will swap in."""
        user = UserFactory.create()
        client.force_login(user)

        response = client.post(CREATE_URL, {"file": _upload()}, **HTMX_HEADERS)

        route = Route.objects.get(user=user)
        assert f'id="route-{route.uuid}"' in response.content.decode()

    def test_an_elevation_free_file_stores_a_null_ascent(self, client: Client) -> None:
        """Null, not zero, all the way through the view."""
        user = UserFactory.create()
        client.force_login(user)

        response = client.post(
            CREATE_URL, {"file": _upload("no_elevation.gpx")}, **HTMX_HEADERS
        )

        assert response.status_code == 200
        assert Route.objects.get(user=user).ascent_m is None


@pytest.mark.django_db
class TestRouteCreateValidation:
    """Missing, oversized and unreadable uploads."""

    def test_no_file_returns_400(self, client: Client) -> None:
        """A POST with no file part is a 400."""
        client.force_login(UserFactory.create())
        response = client.post(CREATE_URL, {}, **HTMX_HEADERS)
        assert response.status_code == 400

    def test_oversized_upload_returns_413(self, client: Client, settings: Any) -> None:
        """Over ROUTE_UPLOAD_MAX_BYTES is 413, and nothing is stored."""
        settings.ROUTE_UPLOAD_MAX_BYTES = 10
        user = UserFactory.create()
        client.force_login(user)

        response = client.post(CREATE_URL, {"file": _upload()}, **HTMX_HEADERS)

        assert response.status_code == 413
        assert not Route.objects.for_user(user).exists()

    def test_oversized_upload_is_never_parsed(
        self, client: Client, settings: Any
    ) -> None:
        """The size gate runs before the XML parser sees the bytes."""
        settings.ROUTE_UPLOAD_MAX_BYTES = 10
        client.force_login(UserFactory.create())

        with patch("apps.routes.services.routes.parse_gpx") as mock_parse:
            client.post(CREATE_URL, {"file": _upload()}, **HTMX_HEADERS)

        mock_parse.assert_not_called()

    def test_an_upload_at_the_limit_is_accepted(
        self, client: Client, settings: Any
    ) -> None:
        """The bound is inclusive — exactly at the limit is fine."""
        raw = _fixture("single_track.gpx")
        settings.ROUTE_UPLOAD_MAX_BYTES = len(raw)
        user = UserFactory.create()
        client.force_login(user)

        response = client.post(CREATE_URL, {"file": _upload()}, **HTMX_HEADERS)

        assert response.status_code == 200
        assert Route.objects.for_user(user).count() == 1

    def test_non_gpx_body_returns_400(self, client: Client) -> None:
        """Bytes that are not GPX are a handled 400, not a 500."""
        user = UserFactory.create()
        client.force_login(user)
        upload = SimpleUploadedFile(
            "notes.gpx", b"this is not xml at all", content_type="application/gpx+xml"
        )

        response = client.post(CREATE_URL, {"file": upload}, **HTMX_HEADERS)

        assert response.status_code == 400
        assert not Route.objects.for_user(user).exists()

    def test_waypoint_only_gpx_returns_400(self, client: Client) -> None:
        """Well-formed GPX with no route in it is still a 400."""
        user = UserFactory.create()
        client.force_login(user)

        response = client.post(
            CREATE_URL, {"file": _upload("waypoints_only.gpx")}, **HTMX_HEADERS
        )

        assert response.status_code == 400
        assert not Route.objects.for_user(user).exists()

    def test_a_parse_failure_does_not_echo_the_parser_message(
        self, client: Client
    ) -> None:
        """CodeQL py/stack-trace-exposure: exception detail stays in the log.

        expat's message names the line and column it choked on. That is
        parser internals, not something the uploader can act on, so the
        response is a fixed string.
        """
        client.force_login(UserFactory.create())

        response = client.post(
            CREATE_URL, {"file": _upload("malformed.gpx")}, **HTMX_HEADERS
        )

        body = response.content.decode()
        assert response.status_code == 400
        assert "mismatched tag" not in body
        assert "column" not in body

    def test_a_rejected_entity_payload_is_not_reflected_back(
        self, client: Client
    ) -> None:
        """The rejected payload's own system_id must not come back out.

        defusedxml names the entity and its system_id in the exception it
        raises — echoing that would reflect the attacker's input verbatim.
        """
        client.force_login(UserFactory.create())

        response = client.post(CREATE_URL, {"file": _upload("xxe.gpx")}, **HTMX_HEADERS)

        body = response.content.decode()
        assert response.status_code == 400
        assert "system_id" not in body
        assert "/etc/passwd" not in body

    def test_xxe_upload_returns_400(self, client: Client) -> None:
        """A hostile payload is a 400 from the parser, not a file read."""
        user = UserFactory.create()
        client.force_login(user)

        response = client.post(CREATE_URL, {"file": _upload("xxe.gpx")}, **HTMX_HEADERS)

        assert response.status_code == 400
        assert "root:" not in response.content.decode()
        assert not Route.objects.for_user(user).exists()


@pytest.mark.django_db
class TestRouteCreateCap:
    """Reaching the per-user cap renders the limit partial at 409."""

    def test_cap_reached_returns_409_with_limit_partial(
        self, client: Client, settings: Any
    ) -> None:
        """409, not 429 or 200 — a cap is a permanent failure until a delete.

        Mirrors ``favourite_create``'s contract so a client mutation queue
        classifies the doomed upload the same way (SNOW-479).
        """
        settings.ROUTES_MAX_PER_USER = 1
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user)

        response = client.post(CREATE_URL, {"file": _upload()}, **HTMX_HEADERS)

        assert response.status_code == 409
        assert Route.objects.for_user(user).count() == 1
        assert "limit" in response.content.decode().lower()


@pytest.mark.django_db
class TestRouteCreateRateLimit:
    """Rate limit returns 429 when exceeded."""

    def test_rate_limited_branch_returns_429(self) -> None:
        """When request.limited is True, the view returns 429.

        Mirrors ``tests/favourites/test_views.py::TestFavouriteCreateRateLimit``
        — django-ratelimit ORs a pre-set ``request.limited=True`` with its
        own (unmet) check, so pre-setting it short-circuits into the 429
        branch.
        """
        user = UserFactory.create()

        from django.contrib.sessions.backends.db import SessionStore  # noqa: PLC0415
        from django.http import HttpResponse as _HR  # noqa: PLC0415
        from django.test import RequestFactory  # noqa: PLC0415
        from django_htmx.middleware import HtmxMiddleware  # noqa: PLC0415

        rf = RequestFactory()
        request = rf.post(CREATE_URL, {"file": _upload()}, HTTP_HX_REQUEST="true")
        request.limited = True  # type: ignore[attr-defined]
        request.user = user
        request.session = SessionStore()

        HtmxMiddleware(lambda r: _HR())(request)

        from apps.routes.views import route_create  # noqa: PLC0415

        assert route_create(request).status_code == 429


# ---------------------------------------------------------------------------
# route_rename — POST /routes/partials/<uuid>/rename/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteRename:
    """Owner-checked rename."""

    def test_owner_can_rename(self, client: Client) -> None:
        """The new name is persisted and echoed back in the partial."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user, name="Old")

        response = client.post(_rename_url(route.uuid), {"name": "New"}, **HTMX_HEADERS)

        assert response.status_code == 200
        route.refresh_from_db()
        assert route.name == "New"

    def test_rename_advances_updated_at(self, client: Client) -> None:
        """updated_at is in update_fields, so auto_now actually lands."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user, name="Old")
        before = route.updated_at

        client.post(_rename_url(route.uuid), {"name": "New"}, **HTMX_HEADERS)

        route.refresh_from_db()
        assert route.updated_at > before

    def test_another_users_uuid_returns_404_not_403(self, client: Client) -> None:
        """Owner scoping gives no existence oracle."""
        owner = UserFactory.create()
        other = UserFactory.create()
        client.force_login(other)
        route = RouteFactory.create(user=owner, name="Theirs")

        response = client.post(
            _rename_url(route.uuid), {"name": "Mine now"}, **HTMX_HEADERS
        )

        assert response.status_code == 404
        route.refresh_from_db()
        assert route.name == "Theirs"

    def test_unknown_uuid_returns_404(self, client: Client) -> None:
        """An uuid nobody owns answers the same 404 as one somebody else does."""
        client.force_login(UserFactory.create())
        response = client.post(
            _rename_url("00000000-0000-0000-0000-000000000000"),
            {"name": "x"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 404

    def test_name_over_max_length_returns_400(self, client: Client) -> None:
        """An over-length name is a handled 400, not a DB DataError."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user, name="Old")

        response = client.post(
            _rename_url(route.uuid), {"name": "x" * 101}, **HTMX_HEADERS
        )

        assert response.status_code == 400
        route.refresh_from_db()
        assert route.name == "Old"

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous rename returns 403."""
        route = RouteFactory.create()
        response = client.post(_rename_url(route.uuid), {"name": "x"}, **HTMX_HEADERS)
        assert response.status_code == 403

    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain POST without HX-Request returns 400."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user)
        response = client.post(_rename_url(route.uuid), {"name": "x"})
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# route_delete — POST /routes/partials/<uuid>/delete/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteDelete:
    """Owner-checked deletion."""

    def test_owner_can_delete(self, client: Client) -> None:
        """The row is gone and the response is an empty 200."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user)

        response = client.post(_delete_url(route.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        assert response.content == b""
        assert not Route.objects.filter(pk=route.pk).exists()

    def test_another_users_uuid_returns_404_and_the_row_survives(
        self, client: Client
    ) -> None:
        """Owner scoping again — 404, never 403, and nothing is deleted."""
        owner = UserFactory.create()
        other = UserFactory.create()
        client.force_login(other)
        route = RouteFactory.create(user=owner)

        response = client.post(_delete_url(route.uuid), **HTMX_HEADERS)

        assert response.status_code == 404
        assert Route.objects.filter(pk=route.pk).exists()

    def test_unknown_uuid_returns_404(self, client: Client) -> None:
        """An unknown uuid is the same 404."""
        client.force_login(UserFactory.create())
        response = client.post(
            _delete_url("00000000-0000-0000-0000-000000000000"), **HTMX_HEADERS
        )
        assert response.status_code == 404

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous delete returns 403."""
        route = RouteFactory.create()
        response = client.post(_delete_url(route.uuid), **HTMX_HEADERS)
        assert response.status_code == 403
        assert Route.objects.filter(pk=route.pk).exists()

    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain POST without HX-Request returns 400."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user)
        response = client.post(_delete_url(route.uuid))
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# route_list — GET /routes/partials/list/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteListGates:
    """Auth, HTMX and method gates."""

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous list request returns 403."""
        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)
        assert response.status_code == 403

    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain GET without HX-Request returns 400."""
        client.force_login(UserFactory.create())
        response = client.get(MAP_LIST_URL)
        assert response.status_code == 400

    def test_post_is_not_allowed(self, client: Client) -> None:
        """The endpoint is GET-only."""
        client.force_login(UserFactory.create())
        response = client.post(MAP_LIST_URL, **HTMX_HEADERS)
        assert response.status_code == 405


@pytest.mark.django_db
class TestRouteListScoping:
    """The list is the requesting user's own routes and nobody else's."""

    def test_owners_routes_are_listed(self, client: Client) -> None:
        """Each of the user's own routes renders, addressed by its uuid."""
        user = UserFactory.create()
        client.force_login(user)
        first = RouteFactory.create(user=user, name="Haute Route")
        second = RouteFactory.create(user=user, name="Vallée Blanche")

        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)
        body = response.content.decode()

        assert response.status_code == 200
        assert "Haute Route" in body
        assert "Vallée Blanche" in body
        assert f'id="route-{first.uuid}"' in body
        assert f'id="route-{second.uuid}"' in body

    def test_another_users_routes_are_not_listed(self, client: Client) -> None:
        """Owner scoping — a route belonging to someone else never appears."""
        user = UserFactory.create()
        other = UserFactory.create()
        client.force_login(user)
        mine = RouteFactory.create(user=user, name="Mine")
        theirs = RouteFactory.create(user=other, name="Theirs")

        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)
        body = response.content.decode()

        assert "Mine" in body
        assert "Theirs" not in body
        assert str(theirs.uuid) not in body
        assert str(mine.uuid) in body

    def test_empty_state_is_rendered(self, client: Client) -> None:
        """A user with no routes gets the empty line, not a bare list.

        The distinction matters to the panel: routes.js writes its OWN
        failed-load line for a request that did not arrive, so this empty
        state must only ever mean "you really have none".
        """
        client.force_login(UserFactory.create())

        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)
        body = response.content.decode()

        assert response.status_code == 200
        assert 'data-testid="route-list-empty"' in body

    def test_empty_state_names_where_a_gpx_comes_from(self, client: Client) -> None:
        """The map panel's empty clause names a source for a .gpx (SNOW-721).

        The same clause the account page renders — one partial, included by
        both variants — so this asserts the map sheet really does get it and
        the two surfaces cannot drift back apart.
        """
        client.force_login(UserFactory.create())

        body = client.get(MAP_LIST_URL, **HTMX_HEADERS).content.decode()

        assert "Routes can be recorded with smart devices" in body

    def test_empty_state_is_absent_once_a_route_exists(self, client: Client) -> None:
        """A user WITH a route gets no empty clause at all."""
        user = UserFactory.create()
        RouteFactory.create(user=user)
        client.force_login(user)

        body = client.get(MAP_LIST_URL, **HTMX_HEADERS).content.decode()

        assert 'data-testid="route-list-empty"' not in body
        assert "Upload routes as GPX files." not in body


@pytest.mark.django_db
class TestRouteListVariants:
    """``?variant=`` picks a row shape out of a fixed map."""

    def test_map_variant_renders_the_shared_ugc_row(self, client: Client) -> None:
        """``?variant=map`` gets the lean row: a pencil, a trash, no name field.

        The hooks asserted here are the contract three other files depend
        on — inline_rename.js reads ``data-row-renameable`` /
        ``data-row-rename``, and routes.js reads ``data-route-rename`` for
        the uuid — so a row that stopped carrying them would break the
        panel's rename with nothing else failing.
        """
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user)

        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)
        body = response.content.decode()

        assert 'data-testid="route-list"' in body
        assert "data-row-renameable" in body
        assert "data-row-rename" in body
        assert f'data-route-rename="{route.uuid}"' in body
        # A <li> in a <ul>, not the bordered box with an always-visible
        # rename input that SNOW-685 shipped.
        assert "<li" in body
        assert 'name="name"' not in body

    def test_both_variants_render_the_same_shared_row(self, client: Client) -> None:
        """One row, both variants (SNOW-711).

        This reverses what SNOW-686 pinned here: _route.html kept an
        always-visible rename field and an underlined "Remove" while the map
        sheet rendered the shared UGC row, so the same route read two ways.
        Unlike the favourites pair there is nothing left that differs —
        a route has no detail page, so neither list carries a disclosure.
        """
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user)

        default = client.get(LIST_URL, **HTMX_HEADERS).content.decode()
        sheet = client.get(MAP_LIST_URL, **HTMX_HEADERS).content.decode()

        assert 'data-testid="route-list-default"' in default
        for hook in (
            "data-row-renameable",
            "data-row-rename-input",
            f'data-route-rename="{route.uuid}"',
            f'hx-target="#route-{route.uuid}"',
        ):
            assert hook in default, hook
            assert hook in sheet, hook
        # The always-visible field is gone from both — the commit is a
        # fetch from an inline editor, not a form submit.
        assert 'name="name"' not in default
        assert "data-row-disclosure" not in default
        assert "data-row-disclosure" not in sheet

    def test_an_unknown_variant_falls_back_to_the_default(self, client: Client) -> None:
        """An unknown value is not interpolated into a template path.

        The guard that matters: ``variant`` selects out of a dict, so a
        caller cannot reach an arbitrary template with it.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user)

        response = client.get(f"{LIST_URL}?variant=../../etc/passwd", **HTMX_HEADERS)

        assert response.status_code == 200
        assert 'data-testid="route-list-default"' in response.content.decode()


@pytest.mark.django_db
class TestRouteListFocus:
    """The row's name frames the route on the map — and only on the map.

    Hugo: "For routes, resorts, and observations, clicking on the name of an
    item should zoom in to it." The server's whole share of that is two
    attributes on the name, and both are asserted here because the JS half
    (static/js/row_focus.js) can only be as right as what it reads.
    """

    def test_the_map_row_carries_the_routes_bbox(self, client: Client) -> None:
        """``data-row-focus`` is Route.bounds, west/south/east/north.

        The ordinates are formatted through ``%f`` in Python rather than
        interpolated as floats: a template renders a float through the
        active locale, and a decimal comma in a comma-separated attribute
        cannot even be split apart again.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, bounds=[7.4, 46.1, 7.42, 46.12])

        body = client.get(MAP_LIST_URL, **HTMX_HEADERS).content.decode()

        assert 'data-row-focus="7.400000,46.100000,7.420000,46.120000"' in body

    def test_the_map_rows_name_is_a_real_button(self, client: Client) -> None:
        """A `<button>`, not a clickable `<span>`.

        This is the whole reason the label was allowed to grow a second job
        at all: the argument that took rename away from it (SNOW-658) was
        that a span's click is mouse-only. A control announcing "Zoom to
        <name>" is the shape that argument asked for.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, name="Haute Route")

        body = client.get(MAP_LIST_URL, **HTMX_HEADERS).content.decode()

        assert "<button" in body
        assert 'aria-label="Zoom to Haute Route"' in body

    def test_the_account_row_has_no_focus_control(self, client: Client) -> None:
        """/account/routes/ renders the same row with an inert name.

        There is no map on that page to fly, and a button that did nothing
        would read as broken rather than as absent.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user)

        body = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert "data-row-focus" not in body
        assert "Zoom to" not in body


@pytest.mark.django_db
class TestRouteListFigures:
    """The map row's meta line — distance, plus ascent/descent when known."""

    def test_distance_and_ascent_are_rendered(self, client: Client) -> None:
        """Both figures appear, one decimal for km and none for metres."""
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, distance_m=12400.0, ascent_m=850.0)

        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)
        body = response.content.decode()

        assert "12.4 km" in body
        assert "850 m ascent" in body

    def test_descent_is_rendered_beside_ascent(self, client: Client) -> None:
        """Both vertical figures, not one netted against the other.

        An out-and-back and a one-way traverse can carry the same length
        and the same climb; the descent is what separates them, so it is
        shown rather than subtracted.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(
            user=user, distance_m=12400.0, ascent_m=850.0, descent_m=1100.0
        )

        body = client.get(MAP_LIST_URL, **HTMX_HEADERS).content.decode()

        assert "850 m ascent" in body
        assert "1100 m descent" in body

    def test_a_null_descent_is_omitted(self, client: Client) -> None:
        """Descent obeys the same unknown-is-not-flat rule as ascent."""
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(
            user=user, distance_m=12400.0, ascent_m=850.0, descent_m=None
        )

        body = client.get(MAP_LIST_URL, **HTMX_HEADERS).content.decode()

        assert "850 m ascent" in body
        assert "descent" not in body

    def test_a_zero_descent_is_still_rendered(self, client: Client) -> None:
        """A measured zero — a track that only climbs — states itself."""
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(
            user=user, distance_m=12400.0, ascent_m=850.0, descent_m=0.0
        )

        assert (
            "0 m descent" in client.get(MAP_LIST_URL, **HTMX_HEADERS).content.decode()
        )

    def test_a_null_ascent_is_omitted(self, client: Client) -> None:
        """A route with no elevation data shows its distance alone.

        ``ascent_m`` is null — not zero — when the source GPX carried no
        ``<ele>`` at all, and Route's own docstring is explicit that "we
        don't know" and "flat" are different facts. Rendering "0 m ascent"
        for the first would be a false statement about a route somebody may
        be planning to ski, so the meta line drops the figure entirely.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(
            user=user, distance_m=12400.0, ascent_m=None, descent_m=None
        )

        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)
        body = response.content.decode()

        assert "12.4 km" in body
        assert "ascent" not in body
        assert "0 m" not in body

    def test_a_zero_ascent_is_still_rendered(self, client: Client) -> None:
        """Zero is a real measurement and must not be mistaken for absent.

        The inverse of the test above, and the reason the template tests
        ``is not None`` rather than truthiness: a genuinely flat route with
        elevation data present says so.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, distance_m=5000.0, ascent_m=0.0)

        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)

        assert "0 m ascent" in response.content.decode()

    def test_an_unnamed_route_falls_back_to_its_filename(self, client: Client) -> None:
        """A blank name reads as the uploaded filename, never as an empty row."""
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, name="", source_filename="mont-blanc.gpx")

        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)

        assert "mont-blanc.gpx" in response.content.decode()

    def test_a_route_with_neither_name_nor_filename_reads_as_untitled(
        self, client: Client
    ) -> None:
        """The last fallback, so a row always has something to name it."""
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, name="", source_filename="")

        response = client.get(MAP_LIST_URL, **HTMX_HEADERS)

        assert "Untitled route" in response.content.decode()


# ---------------------------------------------------------------------------
# routes_geojson — GET /routes/routes.geojson (SNOW-687)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRoutesGeojsonGates:
    """Auth gate and cache directives."""

    def test_anonymous_gets_403_json(self, client: Client) -> None:
        """An anonymous request is refused in the shape the JS client parses."""
        response = client.get(GEOJSON_URL)

        assert response.status_code == 403
        assert response.json() == {"error": "authentication_required"}

    def test_response_is_private_and_no_store(self, client: Client) -> None:
        """Per-user data must never be held by an intermediate cache."""
        client.force_login(UserFactory.create())

        response = client.get(GEOJSON_URL)

        cache_control = response["Cache-Control"]
        assert "private" in cache_control
        assert "no-store" in cache_control

    def test_not_htmx_gated(self, client: Client) -> None:
        """A plain fetch (no HX-Request header) is served, unlike the partials."""
        client.force_login(UserFactory.create())

        response = client.get(GEOJSON_URL)

        assert response.status_code == 200


@pytest.mark.django_db
class TestRoutesGeojsonShape:
    """The FeatureCollection the MapLibre source consumes."""

    def test_empty_collection_for_a_user_with_no_routes(self, client: Client) -> None:
        """A valid, empty FeatureCollection — never an error, never a null."""
        client.force_login(UserFactory.create())

        payload = client.get(GEOJSON_URL).json()

        assert payload == {"type": "FeatureCollection", "features": []}

    def test_geometry_is_a_linestring_of_the_stored_points(
        self, client: Client
    ) -> None:
        """``points`` is handed over verbatim — [lon, lat, ele], GeoJSON order.

        The stored order is already RFC 7946's, so any transform here would
        be a chance to swap the axes. Asserting identity is what pins that.
        """
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user)

        feature = client.get(GEOJSON_URL).json()["features"][0]

        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "LineString"
        assert feature["geometry"]["coordinates"] == route.points
        # Longitude first: the factory's track sits around 7.4E / 46.1N.
        assert feature["geometry"]["coordinates"][0][0] == pytest.approx(7.4)
        assert feature["geometry"]["coordinates"][0][1] == pytest.approx(46.1)

    def test_every_property_the_client_reads_is_present(self, client: Client) -> None:
        """uuid, name, the figures, duration_s, bounds — popup + fitBounds."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user, name="Haute Route")

        properties = client.get(GEOJSON_URL).json()["features"][0]["properties"]

        assert properties == {
            "uuid": str(route.uuid),
            "name": "Haute Route",
            "distance_m": route.distance_m,
            "ascent_m": route.ascent_m,
            "descent_m": route.descent_m,
            "duration_s": 7200,
            "bounds": route.bounds,
        }

    def test_duration_is_derived_seconds_not_two_timestamps(
        self, client: Client
    ) -> None:
        """The server does the subtraction, so the client parses no dates.

        SNOW-750. Sending ``started_at``/``finished_at`` would push ISO
        parsing and a timezone question onto the popup for a figure the
        server already holds.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(
            user=user,
            started_at=datetime(2026, 3, 13, 9, 41, 38, tzinfo=UTC),
            finished_at=datetime(2026, 3, 13, 14, 41, 35, tzinfo=UTC),
        )

        properties = client.get(GEOJSON_URL).json()["features"][0]["properties"]

        assert properties["duration_s"] == 17997
        assert "started_at" not in properties
        assert "finished_at" not in properties

    def test_an_untimed_route_sends_a_null_duration(self, client: Client) -> None:
        """Null passes through, on the same rule ``ascent_m`` follows.

        The client omits the segment for a null rather than rendering
        "0m" — a planned <rte> has no duration, it does not take no time.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, started_at=None, finished_at=None)

        properties = client.get(GEOJSON_URL).json()["features"][0]["properties"]

        assert properties["duration_s"] is None

    def test_a_null_descent_passes_through_as_null(self, client: Client) -> None:
        """Descent's null survives serialisation exactly as ascent's does."""
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, ascent_m=None, descent_m=None)

        properties = client.get(GEOJSON_URL).json()["features"][0]["properties"]

        assert properties["descent_m"] is None

    def test_per_point_elevation_reaches_the_client_in_the_geometry(
        self, client: Client
    ) -> None:
        """The profile chart's whole data source — no property of its own.

        ``static/js/elevation_profile_core.js`` draws from the third
        ordinate of each coordinate, which RFC 7946 allows and MapLibre
        ignores. If serialisation ever flattened positions to [lon, lat]
        the chart would silently vanish, so this asserts the third slot
        survives with its value.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user)

        coordinates = client.get(GEOJSON_URL).json()["features"][0]["geometry"][
            "coordinates"
        ]

        assert [point[2] for point in coordinates] == [1500.0, 1550.0, 1600.0]

    def test_a_null_ascent_passes_through_as_null(self, client: Client) -> None:
        """A GPX with no <ele> means "unknown", and must not become a zero.

        ``Route``'s docstring is explicit that rendering "flat" for
        "we don't know" is a safety-relevant lie; the client omits the
        ascent line entirely, which it can only do if the null survives
        serialisation.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, ascent_m=None)

        properties = client.get(GEOJSON_URL).json()["features"][0]["properties"]

        assert properties["ascent_m"] is None

    def test_a_zero_ascent_is_still_a_zero(self, client: Client) -> None:
        """The other half of the pair: a genuinely flat track reports 0."""
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user, ascent_m=0.0)

        properties = client.get(GEOJSON_URL).json()["features"][0]["properties"]

        assert properties["ascent_m"] == 0.0


@pytest.mark.django_db
class TestRoutesGeojsonScoping:
    """Owner scoping — the layer draws the requesting user's routes alone."""

    def test_another_users_routes_are_absent(self, client: Client) -> None:
        """A route belonging to somebody else never reaches this payload."""
        user = UserFactory.create()
        other = UserFactory.create()
        client.force_login(user)
        mine = RouteFactory.create(user=user, name="Mine")
        theirs = RouteFactory.create(user=other, name="Theirs")

        payload = client.get(GEOJSON_URL).json()

        uuids = [f["properties"]["uuid"] for f in payload["features"]]
        assert uuids == [str(mine.uuid)]
        assert str(theirs.uuid) not in uuids


@pytest.mark.django_db
class TestRoutesGeojsonFreshness:
    """Freshness headers follow community_reports_geojson, not favourites."""

    def test_generated_at_is_the_newest_routes_updated_at(self, client: Client) -> None:
        """The client's freshness dot reads the most recent edit, not "now"."""
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user)
        newest = RouteFactory.create(user=user)

        response = client.get(GEOJSON_URL)

        assert response["X-Data-Generated-At"] == newest.updated_at.isoformat(
            timespec="seconds"
        )

    def test_headers_are_present_for_an_empty_set(self, client: Client) -> None:
        """A user with no routes still gets a generated-at, falling back to now."""
        client.force_login(UserFactory.create())

        response = client.get(GEOJSON_URL)

        assert response["X-Data-Generated-At"]
        assert response["X-Data-Max-Age"] == str(DEFAULT_MAX_AGE_SECONDS)

    def test_no_unsafe_after_header(self, client: Client) -> None:
        """A user's own track is not safety-critical data.

        ``unsafe_after=None`` means the client's freshness state saturates
        at "stale" and never escalates to "unsafe" — the same call
        ``community_reports_geojson`` makes, and the reason that endpoint
        rather than ``favourites_geojson`` is the precedent here.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user)

        response = client.get(GEOJSON_URL)

        assert "X-Data-Unsafe-After" not in response


@pytest.mark.django_db
class TestRoutesGeojsonQueryCount:
    """The endpoint stays flat as a user's route count grows."""

    def test_query_count_does_not_grow_with_route_count(self, client: Client) -> None:
        """One route and five routes cost the same number of queries.

        Measured rather than asserted at a literal: the client's session
        and auth lookups are counted too, and pinning their number here
        would make this test fail on an unrelated middleware change. What
        matters is that nothing is per-route.
        """
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user)

        with CaptureQueriesContext(connection) as one_route:
            client.get(GEOJSON_URL)

        RouteFactory.create_batch(4, user=user)

        with CaptureQueriesContext(connection) as five_routes:
            response = client.get(GEOJSON_URL)

        assert len(response.json()["features"]) == 5
        assert len(five_routes) == len(one_route)


# ---------------------------------------------------------------------------
# SNOW-764 — the two widened endpoints
# ---------------------------------------------------------------------------


def _follow(client: Client, token: str) -> None:
    """Follow a share link so the client's session holds its token.

    Through the real redirect rather than by writing the session by hand:
    the whole point of the widening is that a token can only arrive this
    way, and a test that planted one directly would pass even if that
    stopped being true.
    """
    response = client.get(f"/routes/s/{token}/")
    assert response.status_code == 302


@pytest.mark.django_db
class TestRouteListPendingShares:
    """route_list answers for a session holding a followed share."""

    def test_an_anonymous_session_with_nothing_pending_still_gets_403(
        self, client: Client
    ) -> None:
        """The widening is not a general opening — this is every visitor."""
        assert client.get(LIST_URL, **HTMX_HEADERS).status_code == 403

    def test_an_anonymous_session_with_a_pending_share_gets_the_list(
        self, client: Client
    ) -> None:
        """They were sent a route; the panel is where a route is read."""
        share = RouteShareFactory.create(route=RouteFactory.create(name="Col Ferret"))
        _follow(client, share.token)

        response = client.get(LIST_URL, **HTMX_HEADERS)

        assert response.status_code == 200
        assert "Col Ferret" in response.content.decode()

    def test_a_pending_row_is_marked_as_shared_with_you(self, client: Client) -> None:
        """The row says it is not yet theirs before the button does."""
        share = RouteShareFactory.create()
        _follow(client, share.token)

        response = client.get(LIST_URL, **HTMX_HEADERS)

        assert "Shared with you" in response.content.decode()

    def test_a_pending_row_is_keyed_on_the_token_not_the_uuid(
        self, client: Client
    ) -> None:
        """A non-owner is never handed the identifier the owner's endpoints use."""
        route = RouteFactory.create()
        share = RouteShareFactory.create(route=route)
        _follow(client, share.token)

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert f"route-share-{share.token}" in content
        assert str(route.uuid) not in content

    def test_a_pending_row_offers_save_and_not_rename_or_remove(
        self, client: Client
    ) -> None:
        """Its one control is the claim; the owner's three would all 404."""
        share = RouteShareFactory.create()
        _follow(client, share.token)

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert f"/routes/partials/share/{share.token}/claim/" in content
        assert "data-row-rename" not in content
        assert "data-row-remove" not in content

    def test_pending_rows_sort_above_owned_ones(self, client: Client) -> None:
        """The claim is the reason the panel was opened."""
        user = UserFactory.create()
        RouteFactory.create(user=user, name="My own route")
        share = RouteShareFactory.create(route=RouteFactory.create(name="Sent to me"))
        client.force_login(user)
        _follow(client, share.token)

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert content.index("Sent to me") < content.index("My own route")

    def test_an_expired_pending_share_drops_out(self, client: Client) -> None:
        """A token that dies between the follow and the read is pruned."""
        share = RouteShareFactory.create()
        _follow(client, share.token)
        share.expires_at = timezone.now() - timedelta(days=1)
        share.save(update_fields=["expires_at", "updated_at"])

        response = client.get(LIST_URL, **HTMX_HEADERS)

        # Nothing pending and nobody signed in — back to the plain 403.
        assert response.status_code == 403

    def test_the_empty_clause_is_silenced_by_a_pending_row(
        self, client: Client
    ) -> None:
        """Saying they have no routes beneath a visible row is a wrong claim."""
        user = UserFactory.create()
        share = RouteShareFactory.create()
        client.force_login(user)
        _follow(client, share.token)

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert 'data-testid="route-list-empty"' not in content

    def test_an_owned_row_keeps_its_own_controls_beside_a_pending_one(
        self, client: Client
    ) -> None:
        """The pending row's context must not leak into the owned loop.

        Both rows render through the same partial, and ``pending_share``
        is what switches it. Set once for the pending loop and left in
        scope, it would turn every owned row into a Save button too.
        """
        user = UserFactory.create()
        route = RouteFactory.create(user=user)
        share = RouteShareFactory.create()
        client.force_login(user)
        _follow(client, share.token)

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert f'id="route-{route.uuid}"' in content
        assert "data-row-rename" in content
        assert "data-row-remove" in content

    def test_the_map_variant_renders_pending_rows_too(self, client: Client) -> None:
        """Both surfaces list the same thing; the deep link lands on the map."""
        share = RouteShareFactory.create()
        _follow(client, share.token)

        response = client.get(
            f"{LIST_URL}?variant={ROUTE_LIST_MAP_VARIANT}", **HTMX_HEADERS
        )

        assert response.status_code == 200
        assert f"route-share-{share.token}" in response.content.decode()


@pytest.mark.django_db
class TestRoutesGeojsonPendingShares:
    """routes_geojson draws a followed share's line."""

    def test_an_anonymous_session_with_nothing_pending_still_gets_403(
        self, client: Client
    ) -> None:
        """Unchanged for every visitor who has not followed a link."""
        assert client.get(GEOJSON_URL).status_code == 403

    def test_a_pending_share_is_drawn(self, client: Client) -> None:
        """The deep link has to land on a map that can show the route."""
        share = RouteShareFactory.create()
        _follow(client, share.token)

        response = client.get(GEOJSON_URL)

        assert response.status_code == 200
        features = response.json()["features"]
        assert len(features) == 1
        assert features[0]["geometry"]["type"] == "LineString"

    def test_a_pending_feature_carries_the_token_and_the_flag(
        self, client: Client
    ) -> None:
        """map.js keys the pending line and its Save CTA off these two."""
        share = RouteShareFactory.create()
        _follow(client, share.token)

        props = client.get(GEOJSON_URL).json()["features"][0]["properties"]

        assert props["token"] == share.token
        assert props["pending"] is True

    def test_a_pending_feature_carries_no_uuid(self, client: Client) -> None:
        """The owner-scoped endpoints are addressed by uuid; a guest gets none."""
        route = RouteFactory.create()
        share = RouteShareFactory.create(route=route)
        _follow(client, share.token)

        body = client.get(GEOJSON_URL)
        props = body.json()["features"][0]["properties"]

        assert "uuid" not in props
        assert str(route.uuid) not in body.content.decode()

    def test_a_pending_feature_carries_the_same_display_fields(
        self, client: Client
    ) -> None:
        """One route reads the same whoever is looking at it."""
        route = RouteFactory.create(name="Col Ferret", distance_m=8200.0)
        share = RouteShareFactory.create(route=route)
        _follow(client, share.token)

        props = client.get(GEOJSON_URL).json()["features"][0]["properties"]

        assert props["name"] == "Col Ferret"
        assert props["distance_m"] == 8200.0
        assert props["ascent_m"] == route.ascent_m
        assert props["descent_m"] == route.descent_m
        assert props["bounds"] == route.bounds
        assert props["duration_s"] is not None

    def test_owned_features_still_carry_a_uuid_and_no_flag(
        self, client: Client
    ) -> None:
        """The owned branch is untouched by the widening."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)
        client.force_login(user)

        props = client.get(GEOJSON_URL).json()["features"][0]["properties"]

        assert props["uuid"] == str(route.uuid)
        assert "token" not in props
        assert "pending" not in props

    def test_owned_and_pending_features_arrive_together(self, client: Client) -> None:
        """A signed-in recipient sees both their own lines and the sent one."""
        user = UserFactory.create()
        RouteFactory.create(user=user)
        share = RouteShareFactory.create()
        client.force_login(user)
        _follow(client, share.token)

        features = client.get(GEOJSON_URL).json()["features"]

        assert len(features) == 2

    def test_the_payload_is_still_private_and_not_stored(self, client: Client) -> None:
        """It varied per user; it now varies per session too, needing the same."""
        share = RouteShareFactory.create()
        _follow(client, share.token)

        response = client.get(GEOJSON_URL)

        assert "private" in response["Cache-Control"]
        assert "no-store" in response["Cache-Control"]
