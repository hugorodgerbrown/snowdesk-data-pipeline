"""
tests/favourites/test_views.py — Tests for favourites.views.

Covers:
  favourite_create — flag off → 404; anonymous → 403; non-HTMX → 400;
                      rate-limited → 429; invalid lat/lon → 400;
                      name over max_length → 400;
                      valid submit → 200 + creates row;
                      cap reached → 200 with the limit-reached partial.
  favourite_rename — owner isolation (user A cannot rename user B's pin);
                      name over max_length → 400; updated_at advances.
  favourite_delete — owner isolation (user A cannot delete user B's pin);
                      row survives when a non-owner attempts deletion.
  favourite_card — owner GET 200; non-owner uuid → 404 (no existence
                    oracle); anon → 403; flag off → 404; non-HTMX → 400;
                    region-null → no-coverage note; region + rating →
                    danger tile + bulletin link; unnamed favourite →
                    coordinate fallback; no weather snapshot yet →
                    "coming soon" empty state (SNOW-415).
  favourite_list — owner sees only their own favourites; another user's
                    favourites are absent; anon → 403; flag off → 404;
                    non-HTMX → 400; empty state when the user has none
                    (SNOW-415).
  favourites_geojson — returns only the requester's own pins, [lon, lat]
                        coordinate order, Cache-Control: private, no-store;
                        anonymous → 403; flag off → 404.

The Open-Meteo network call is avoided throughout by patching
``favourites.services.resolve_forecast_point``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from freezegun import freeze_time
from waffle.testutils import override_flag

from favourites.models import Favourite
from tests.factories import (
    FavouriteFactory,
    ForecastPointFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    UserFactory,
)

CREATE_URL = "/favourites/partials/create/"
GEOJSON_URL = "/favourites/favourites.geojson"
LIST_URL = "/favourites/partials/list/"

HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


def _rename_url(uuid: object) -> str:
    """Build the rename URL for a favourite's uuid."""
    return f"/favourites/partials/{uuid}/rename/"


def _delete_url(uuid: object) -> str:
    """Build the delete URL for a favourite's uuid."""
    return f"/favourites/partials/{uuid}/delete/"


def _card_url(uuid: object) -> str:
    """Build the detail-card URL for a favourite's uuid."""
    return f"/favourites/partials/{uuid}/card/"


def _create_via_service(
    user: Any, latitude: float = 46.1, longitude: float = 7.4
) -> Favourite:
    """Create a Favourite directly via the service, mocking the Open-Meteo call."""
    from favourites.services import create_favourite  # noqa: PLC0415

    point = ForecastPointFactory.create(latitude=latitude, longitude=longitude)
    with (
        patch("favourites.services.resolve_forecast_point", return_value=point),
        patch("favourites.services.region_for_point", return_value=None),
    ):
        return create_favourite(user, latitude, longitude)


# ---------------------------------------------------------------------------
# favourite_create — POST /favourites/partials/create/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteCreateFlagGate:
    """Flag-off → 404."""

    @override_flag("favourites", active=False)
    def test_flag_off_returns_404(self, client: Client) -> None:
        """When the favourites flag is inactive, POST returns 404."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(
            CREATE_URL, {"lat": "46.1", "lon": "7.4"}, **HTMX_HEADERS
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestFavouriteCreateAuthGate:
    """Anonymous users are rejected with 403."""

    @override_flag("favourites", active=True)
    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous POST returns 403."""
        response = client.post(
            CREATE_URL, {"lat": "46.1", "lon": "7.4"}, **HTMX_HEADERS
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestFavouriteCreateHtmxGate:
    """Non-HTMX requests are rejected with 400."""

    @override_flag("favourites", active=True)
    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain POST without HX-Request returns 400."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(CREATE_URL, {"lat": "46.1", "lon": "7.4"})
        assert response.status_code == 400


@pytest.mark.django_db
class TestFavouriteCreateValidation:
    """Missing/unparseable lat or lon returns 400."""

    @override_flag("favourites", active=True)
    def test_missing_lat_returns_400(self, client: Client) -> None:
        """No lat provided → 400."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(CREATE_URL, {"lon": "7.4"}, **HTMX_HEADERS)
        assert response.status_code == 400

    @override_flag("favourites", active=True)
    def test_unparseable_lat_returns_400(self, client: Client) -> None:
        """A non-float lat → 400."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(
            CREATE_URL, {"lat": "not-a-number", "lon": "7.4"}, **HTMX_HEADERS
        )
        assert response.status_code == 400

    @override_flag("favourites", active=True)
    def test_name_over_max_length_returns_400(self, client: Client) -> None:
        """A name longer than max_length is rejected with 400, not a DB error."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(
            CREATE_URL,
            {"lat": "46.1", "lon": "7.4", "name": "x" * 101},
            **HTMX_HEADERS,
        )
        assert response.status_code == 400
        assert not Favourite.objects.filter(user=user).exists()


@pytest.mark.django_db
class TestFavouriteCreateSuccess:
    """A valid submission creates a row and returns the saved-pin partial."""

    @override_flag("favourites", active=True)
    def test_valid_submit_creates_favourite(self, client: Client) -> None:
        """Valid lat/lon creates a Favourite and returns 200."""
        user = UserFactory.create()
        client.force_login(user)
        point = ForecastPointFactory.create()

        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            response = client.post(
                CREATE_URL,
                {"lat": "46.1", "lon": "7.4", "name": "My spot"},
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        favourite = Favourite.objects.get(user=user)
        assert favourite.name == "My spot"
        assert favourite.forecast_point == point


@pytest.mark.django_db
class TestFavouriteCreateCap:
    """Reaching the per-user cap renders the limit-reached partial at 200."""

    @override_flag("favourites", active=True)
    def test_cap_reached_returns_200_with_limit_partial(
        self, client: Client, settings: Any
    ) -> None:
        """When the cap is reached, the create endpoint returns 200 with the error."""
        settings.FAVOURITES_MAX_PER_USER = 1
        user = UserFactory.create()
        client.force_login(user)
        _create_via_service(user)

        point = ForecastPointFactory.create(latitude=47.0, longitude=8.0)
        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            response = client.post(
                CREATE_URL, {"lat": "47.0", "lon": "8.0"}, **HTMX_HEADERS
            )

        assert response.status_code == 200
        assert Favourite.objects.filter(user=user).count() == 1
        content = response.content.decode()
        assert "limit" in content.lower()


@pytest.mark.django_db
class TestFavouriteCreateRateLimit:
    """Rate limit returns 429 when exceeded."""

    @override_flag("favourites", active=True)
    def test_rate_limited_branch_returns_429(self, client: Client) -> None:
        """When request.limited is True (set by ratelimit decorator), view returns 429.

        Mirrors ``tests/observations/test_views.py::TestReportSubmitRateLimit`` —
        django-ratelimit ORs a pre-set ``request.limited=True`` with its own
        (unmet) check, so pre-setting it short-circuits into the 429 branch.
        """
        user = UserFactory.create()

        from django.contrib.sessions.backends.db import SessionStore  # noqa: PLC0415
        from django.test import RequestFactory  # noqa: PLC0415
        from django_htmx.middleware import HtmxMiddleware  # noqa: PLC0415

        rf = RequestFactory()
        request = rf.post(
            CREATE_URL,
            {"lat": "46.1", "lon": "7.4"},
            HTTP_HX_REQUEST="true",
        )
        request.limited = True  # type: ignore[attr-defined]
        request.user = user
        request.session = SessionStore()

        from django.http import HttpResponse as _HR  # noqa: PLC0415

        htmx_mw = HtmxMiddleware(lambda r: _HR())
        htmx_mw(request)

        with patch("favourites.views._require_favourites_flag", return_value=None):
            from favourites.views import favourite_create  # noqa: PLC0415

            resp = favourite_create(request)
            assert resp.status_code == 429


# ---------------------------------------------------------------------------
# favourite_rename — POST /favourites/partials/<uuid>/rename/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteRenameOwnerIsolation:
    """Owner isolation — user A cannot rename user B's pin."""

    @override_flag("favourites", active=True)
    def test_owner_can_rename(self, client: Client) -> None:
        """The owning user can rename their own favourite."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = _create_via_service(user)

        response = client.post(
            _rename_url(favourite.uuid), {"name": "New name"}, **HTMX_HEADERS
        )

        assert response.status_code == 200
        favourite.refresh_from_db()
        assert favourite.name == "New name"

    @override_flag("favourites", active=True)
    def test_other_user_cannot_rename(self, client: Client) -> None:
        """A different user attempting to rename gets 404, and the row is unchanged."""
        owner = UserFactory.create()
        other_user = UserFactory.create()
        favourite = _create_via_service(owner)

        client.force_login(other_user)
        response = client.post(
            _rename_url(favourite.uuid), {"name": "Hijacked"}, **HTMX_HEADERS
        )

        assert response.status_code == 404
        favourite.refresh_from_db()
        assert favourite.name != "Hijacked"

    @override_flag("favourites", active=False)
    def test_flag_off_returns_404(self, client: Client) -> None:
        """When the favourites flag is inactive, POST returns 404."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(
            _rename_url("00000000-0000-0000-0000-000000000000"),
            {"name": "x"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 404

    @override_flag("favourites", active=True)
    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous POST returns 403."""
        response = client.post(
            _rename_url("00000000-0000-0000-0000-000000000000"),
            {"name": "x"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 403

    @override_flag("favourites", active=True)
    def test_name_over_max_length_returns_400(self, client: Client) -> None:
        """A name longer than max_length is rejected with 400, not a DB error."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = _create_via_service(user)

        response = client.post(
            _rename_url(favourite.uuid), {"name": "x" * 101}, **HTMX_HEADERS
        )

        assert response.status_code == 400
        favourite.refresh_from_db()
        assert favourite.name != "x" * 101

    @override_flag("favourites", active=True)
    def test_updated_at_advances_after_rename(self, client: Client) -> None:
        """updated_at is newer after a rename than at creation (regression)."""
        user = UserFactory.create()
        client.force_login(user)

        with freeze_time("2026-01-01T00:00:00Z"):
            favourite = _create_via_service(user)
        original_updated_at = favourite.updated_at

        with freeze_time("2026-01-01T01:00:00Z"):
            response = client.post(
                _rename_url(favourite.uuid), {"name": "New name"}, **HTMX_HEADERS
            )

        assert response.status_code == 200
        favourite.refresh_from_db()
        assert favourite.updated_at > original_updated_at


# ---------------------------------------------------------------------------
# favourite_delete — POST /favourites/partials/<uuid>/delete/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteDeleteOwnerIsolation:
    """Owner isolation — user A cannot delete user B's pin."""

    @override_flag("favourites", active=True)
    def test_owner_can_delete(self, client: Client) -> None:
        """The owning user can delete their own favourite."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = _create_via_service(user)

        response = client.post(_delete_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        assert not Favourite.objects.filter(pk=favourite.pk).exists()

    @override_flag("favourites", active=True)
    def test_other_user_cannot_delete(self, client: Client) -> None:
        """A different user attempting to delete gets 404, row survives."""
        owner = UserFactory.create()
        other_user = UserFactory.create()
        favourite = _create_via_service(owner)

        client.force_login(other_user)
        response = client.post(_delete_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 404
        assert Favourite.objects.filter(pk=favourite.pk).exists()

    @override_flag("favourites", active=True)
    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous POST returns 403."""
        response = client.post(
            _delete_url("00000000-0000-0000-0000-000000000000"), **HTMX_HEADERS
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# favourite_card — GET /favourites/partials/<uuid>/card/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteCard:
    """favourite_card — owner-scoped detail card (SNOW-415)."""

    @override_flag("favourites", active=True)
    def test_owner_gets_200_with_name_and_altitude(self, client: Client) -> None:
        """Owner GET renders the card with the favourite's name and altitude."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="My spot", elevation=1834.0)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "My spot" in content
        assert "1834" in content

    @override_flag("favourites", active=True)
    def test_unnamed_favourite_falls_back_to_coordinates(self, client: Client) -> None:
        """An unnamed favourite's title falls back to formatted coordinates."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(
            user=user, name="", latitude=46.10123, longitude=7.40456
        )

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "46.10123" in content
        assert "7.40456" in content

    @override_flag("favourites", active=True)
    def test_non_owner_uuid_returns_404(self, client: Client) -> None:
        """A different user's uuid returns 404, not 403 — no existence oracle."""
        owner = UserFactory.create()
        other_user = UserFactory.create()
        favourite = FavouriteFactory.create(user=owner)

        client.force_login(other_user)
        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 404

    @override_flag("favourites", active=True)
    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous GET returns 403."""
        response = client.get(
            _card_url("00000000-0000-0000-0000-000000000000"), **HTMX_HEADERS
        )
        assert response.status_code == 403

    @override_flag("favourites", active=False)
    def test_flag_off_returns_404(self, client: Client) -> None:
        """When the favourites flag is inactive, GET returns 404."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.get(
            _card_url("00000000-0000-0000-0000-000000000000"), **HTMX_HEADERS
        )
        assert response.status_code == 404

    @override_flag("favourites", active=True)
    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain GET without HX-Request returns 400."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)

        response = client.get(_card_url(favourite.uuid))

        assert response.status_code == 400

    @override_flag("favourites", active=True)
    def test_region_null_shows_no_coverage_note(self, client: Client) -> None:
        """A favourite with region=None shows the no-coverage note, no rating."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, region=None)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "No bulletin coverage" in content
        assert "danger-tile" not in content

    @override_flag("favourites", active=True)
    def test_region_with_rating_shows_chip_and_bulletin_link(
        self, client: Client
    ) -> None:
        """A favourite with a region + today's rating shows the danger tile and link."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, max_rating="considerable")
        favourite = FavouriteFactory.create(user=user, region=region)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "danger-tile" in content
        assert region.get_absolute_url() in content

    @override_flag("favourites", active=True)
    def test_no_weather_snapshot_shows_coming_soon(self, client: Client) -> None:
        """Without a ForecastPointWeather snapshot, the weather slot shows 'coming soon'."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "coming soon" in content.lower()


# ---------------------------------------------------------------------------
# favourite_list — GET /favourites/partials/list/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteList:
    """favourite_list — owner-scoped favourites list (SNOW-415)."""

    @override_flag("favourites", active=True)
    def test_owner_sees_only_own_favourites(self, client: Client) -> None:
        """Another user's favourites never appear in the requester's list."""
        user = UserFactory.create()
        other_user = UserFactory.create()
        mine = FavouriteFactory.create(user=user, name="Mine")
        FavouriteFactory.create(user=other_user, name="Theirs")

        client.force_login(user)
        response = client.get(LIST_URL, **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "Mine" in content
        assert "Theirs" not in content
        assert str(mine.uuid) in content

    @override_flag("favourites", active=True)
    def test_empty_state_when_no_favourites(self, client: Client) -> None:
        """A user with no favourites sees the empty-state copy."""
        user = UserFactory.create()
        client.force_login(user)

        response = client.get(LIST_URL, **HTMX_HEADERS)

        assert response.status_code == 200
        assert b"no saved favourites" in response.content.lower()

    @override_flag("favourites", active=True)
    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous GET returns 403."""
        response = client.get(LIST_URL, **HTMX_HEADERS)
        assert response.status_code == 403

    @override_flag("favourites", active=False)
    def test_flag_off_returns_404(self, client: Client) -> None:
        """When the favourites flag is inactive, GET returns 404."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.get(LIST_URL, **HTMX_HEADERS)
        assert response.status_code == 404

    @override_flag("favourites", active=True)
    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain GET without HX-Request returns 400."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.get(LIST_URL)
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# favourites_geojson — GET /favourites/favourites.geojson
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouritesGeojson:
    """favourites_geojson — per-user FeatureCollection, [lon, lat] order."""

    @override_flag("favourites", active=True)
    def test_returns_only_requesters_own_pins(self, client: Client) -> None:
        """Another user's favourites are never included in the response."""
        user = UserFactory.create()
        other_user = UserFactory.create()
        mine = _create_via_service(user, latitude=46.1, longitude=7.4)
        _create_via_service(other_user, latitude=47.0, longitude=8.0)

        client.force_login(user)
        response = client.get(GEOJSON_URL)

        assert response.status_code == 200
        data = response.json()
        uuids = [f["properties"]["uuid"] for f in data["features"]]
        assert uuids == [str(mine.uuid)]

    @override_flag("favourites", active=True)
    def test_coordinates_are_lon_lat_order(self, client: Client) -> None:
        """GeoJSON coordinates are [longitude, latitude] per RFC 7946."""
        user = UserFactory.create()
        favourite = _create_via_service(user, latitude=46.1, longitude=7.4)

        client.force_login(user)
        response = client.get(GEOJSON_URL)

        data = response.json()
        feature = data["features"][0]
        assert feature["geometry"]["coordinates"] == [
            favourite.longitude,
            favourite.latitude,
        ]

    @override_flag("favourites", active=True)
    def test_cache_control_is_private_no_store(self, client: Client) -> None:
        """The response carries Cache-Control: private, no-store."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.get(GEOJSON_URL)
        assert response["Cache-Control"] == "private, no-store"

    @override_flag("favourites", active=True)
    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous GET returns 403."""
        response = client.get(GEOJSON_URL)
        assert response.status_code == 403

    @override_flag("favourites", active=False)
    def test_flag_off_returns_404(self, client: Client) -> None:
        """When the favourites flag is inactive, GET returns 404."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.get(GEOJSON_URL)
        assert response.status_code == 404
