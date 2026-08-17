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

Scoped to the Django test client throughout — per CLAUDE.md's layer rules
there is nothing browser-shaped in this ticket, and a 404 needs no browser.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.routes.models import Route
from tests.factories import RouteFactory, UserFactory

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
