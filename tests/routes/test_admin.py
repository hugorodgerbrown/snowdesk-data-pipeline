"""
tests/routes/test_admin.py — Admin registration smoke test for routes.

Verifies that Route and RouteShare are registered with Django admin and
that both admin classes are read-mostly, mirroring FavouriteAdmin, plus
SNOW-988's download action — the one action on either model, and a read.
"""

from __future__ import annotations

from typing import cast

import pytest
from django.contrib import admin
from django.contrib.messages.storage.cookie import CookieStorage
from django.test import RequestFactory

from apps.routes.admin import RouteAdmin, RouteShareAdmin
from apps.routes.models import Route, RouteShare
from tests.factories import RouteFactory, UserFactory


class TestRouteAdminRegistration:
    """RouteAdmin is registered and configured correctly."""

    def test_route_is_registered(self) -> None:
        """Route is registered with the default admin site."""
        assert Route in admin.site._registry

    def test_admin_class_is_route_admin(self) -> None:
        """The registered admin class is RouteAdmin."""
        assert isinstance(admin.site._registry[Route], RouteAdmin)

    def test_list_display_includes_user_and_name(self) -> None:
        """list_display includes user and name for easy identification."""
        registered = admin.site._registry[Route]
        assert "user" in registered.list_display
        assert "name" in registered.list_display

    def test_list_display_includes_the_derived_figures(self) -> None:
        """list_display surfaces distance, ascent and point count."""
        registered = admin.site._registry[Route]
        assert "distance_m" in registered.list_display
        assert "ascent_m" in registered.list_display
        assert "point_count" in registered.list_display

    def test_list_display_excludes_the_geometry(self) -> None:
        """points is not a changelist column — it is the whole track."""
        registered = admin.site._registry[Route]
        assert "points" not in registered.list_display

    def test_readonly_fields_includes_user_and_geometry(self) -> None:
        """User-generated content is not staff-editable."""
        registered = admin.site._registry[Route]
        assert "user" in registered.readonly_fields
        assert "points" in registered.readonly_fields
        assert "bounds" in registered.readonly_fields

    def test_search_fields_includes_user_email(self) -> None:
        """search_fields includes user__email for lookup by email."""
        registered = admin.site._registry[Route]
        assert "user__email" in registered.search_fields


class TestRouteShareAdminRegistration:
    """RouteShareAdmin is registered and configured correctly (SNOW-764)."""

    def test_route_share_is_registered(self) -> None:
        """RouteShare is registered with the default admin site."""
        assert RouteShare in admin.site._registry

    def test_admin_class_is_route_share_admin(self) -> None:
        """The registered admin class is RouteShareAdmin."""
        assert isinstance(admin.site._registry[RouteShare], RouteShareAdmin)

    def test_list_display_surfaces_the_claim_counters(self) -> None:
        """How far a link travelled is answered on the changelist."""
        registered = admin.site._registry[RouteShare]
        assert "claim_count" in registered.list_display
        assert "last_claimed_at" in registered.list_display

    def test_list_display_includes_the_token_and_its_window(self) -> None:
        """The token identifies the row; expires_at says whether it still works."""
        registered = admin.site._registry[RouteShare]
        assert "token" in registered.list_display
        assert "expires_at" in registered.list_display

    def test_every_field_is_read_only(self) -> None:
        """A share row records a grant; editing one rewrites the record."""
        registered = admin.site._registry[RouteShare]
        for field in ("token", "route", "created_by", "expires_at", "claim_count"):
            assert field in registered.readonly_fields

    def test_search_fields_includes_the_token(self) -> None:
        """A support request arrives carrying the link, so the token is the key."""
        registered = admin.site._registry[RouteShare]
        assert "token" in registered.search_fields


class TestDownloadAsGpxAction:
    """The one action on RouteAdmin (SNOW-988) — a read, not a write."""

    def _action(self) -> "RouteAdmin":
        """Return the registered RouteAdmin.

        Returns:
            The admin instance the site holds, so the test exercises what
            is actually wired up rather than a fresh construction.

        """
        return cast(RouteAdmin, admin.site._registry[Route])

    def test_action_is_registered(self) -> None:
        """The action is on the changelist."""
        assert "download_as_gpx" in self._action().actions

    @pytest.mark.django_db
    def test_returns_a_gpx_attachment_for_one_route(self, rf: RequestFactory) -> None:
        """One selected route streams back as a named .gpx attachment."""
        route = RouteFactory.create(name="Col de la Chaux")
        request = rf.post("/admin/routes/route/")
        request.user = UserFactory.create(is_staff=True)

        response = self._action().download_as_gpx(
            request, Route.objects.filter(pk=route.pk)
        )

        assert response is not None
        assert response.status_code == 200
        assert response["Content-Type"] == "application/gpx+xml"
        assert response["Content-Disposition"] == (
            'attachment; filename="Col-de-la-Chaux.gpx"'
        )
        assert b"<trkpt" in response.content

    @pytest.mark.django_db
    def test_refuses_a_multi_row_selection(self, rf: RequestFactory) -> None:
        """Two routes cannot become one file, and the operator is told."""
        RouteFactory.create_batch(2)
        request = rf.post("/admin/routes/route/")
        request.user = UserFactory.create(is_staff=True)
        storage = CookieStorage(request)
        setattr(request, "_messages", storage)

        response = self._action().download_as_gpx(request, Route.objects.all())

        assert response is None
        assert "Select exactly one route" in str(list(storage)[0])

    @pytest.mark.django_db
    def test_refuses_an_empty_selection(self, rf: RequestFactory) -> None:
        """Nothing selected is the same refusal, not an empty file."""
        request = rf.post("/admin/routes/route/")
        request.user = UserFactory.create(is_staff=True)
        storage = CookieStorage(request)
        setattr(request, "_messages", storage)

        response = self._action().download_as_gpx(request, Route.objects.none())

        assert response is None


@pytest.mark.django_db
class TestTerrainDetailLink:
    """SNOW-1020: a sampled route links to its per-segment terrain page."""

    def _admin(self) -> RouteAdmin:
        """Return the registered RouteAdmin."""
        return cast(RouteAdmin, admin.site._registry[Route])

    def test_is_a_read_only_field(self) -> None:
        """It is a link, not an input."""
        assert "terrain_detail_link" in self._admin().readonly_fields

    def test_links_a_sampled_route_to_its_page(self) -> None:
        """The href is the route's own terrain page."""
        route = RouteFactory.create(slope_samples={"points": [], "segments": []})
        html = self._admin().terrain_detail_link(route)
        assert f"/_route-terrain/{route.uuid}/" in html

    def test_an_unsampled_route_has_no_link(self) -> None:
        """Nothing to link to until the sampler has run."""
        route = RouteFactory.create()
        assert self._admin().terrain_detail_link(route) == "—"
