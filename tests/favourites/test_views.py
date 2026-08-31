"""
tests/favourites/test_views.py — Tests for apps.favourites.views.

Covers:
  favourite_create — anonymous → 403; non-HTMX → 400; rate-limited → 429;
                      invalid lat/lon → 400; name over max_length → 400;
                      valid submit → 200 + creates row;
                      cap reached → 409 with the limit-reached partial.
  favourite_create_from_resort (SNOW-499) — happy path (200 + resort FK
                      set); cap reached → 409 with the limit partial;
                      anonymous → 403; non-HTMX → 400; missing/non-integer
                      resort_id → 400; unknown resort_id → 404; ungeocoded
                      resort → 422.
  favourite_resort_toggle (SNOW-504) — first POST creates, second POST
                      deletes the (user, resort) Favourite; cap reached →
                      409 with the limit partial; anonymous → 403;
                      non-HTMX → 400; unknown resort_id → 404; ungeocoded
                      resort → 422; rate-limited → 429.
  favourite_rename — owner isolation (user A cannot rename user B's pin);
                      name over max_length → 400; updated_at advances.
  favourite_delete — owner isolation (user A cannot delete user B's pin);
                      row survives when a non-owner attempts deletion.
  favourite_card — owner GET 200; non-owner uuid → 404 (no existence
                    oracle); anon → 403; non-HTMX → 400; region-null →
                    no-coverage note; region + rating → danger tile +
                    bulletin link; unnamed favourite → coordinate
                    fallback.
  favourite_detail (SNOW-507) — owner GET 200 full page with page chrome
                    plus the card content; non-owner uuid → 404; unknown
                    uuid → 404; anon → 403; response carries
                    Cache-Control: private, no-store.
  card heading rank — the card's title states its own level per caller
                    (``heading_tag``): ``h1`` on favourite_detail (the pin's
                    name IS that page, which carries no other heading), and
                    ``h2`` on /account/favourites/'s hx-get panel, whose
                    page ``h1`` is the only heading above it. It was ``h3``
                    there until SNOW-668, when the list was a ``<section>``
                    on the account hub headed by an ``h2`` eyebrow. The size
                    is ``text-lg`` in every case.
  favourite_card problems — elevation-aware avalanche-problem highlighting
                    (SNOW-422): a region + today's bulletin renders one
                    rating-block per problem card plus an altitude-relevance
                    chip; region=None renders no problems section; a
                    no-elevation-band problem renders unannotated; the
                    section never contains the word "safe" (safety-sensitive
                    — copy is altitude-relative only).
  favourite_list — owner sees only their own favourites; another user's
                    favourites are absent; anon → 403; non-HTMX → 400;
                    empty state when the user has none (SNOW-415); each
                    row carries one detail link to favourites:detail,
                    hx-get-enhanced onto the card panel (SNOW-507,
                    SNOW-658); ``?variant=map`` renders the sheet's lean
                    template instead — same rows and roster sidecar, a
                    plain detail link, no card panel and no "view on the
                    map" link — and an unknown variant falls back to the
                    manage-page template.
  favourites_geojson — returns only the requester's own pins, [lon, lat]
                        coordinate order, Cache-Control: private, no-store;
                        anonymous → 403; each feature carries resort_id
                        (null for a plain pin, SNOW-499) and created_at as
                        ISO-8601 (SNOW-658).
  freshness (SNOW-418) — favourite_card / favourite_list stamp
                        X-Data-Generated-At / -Max-Age / -Unsafe-After;
                        the card's cache_payload / roster_payload
                        json_script sidecars carry the expected shape;
                        the card shows the freshness indicator when a
                        rating exists; the list's rating lookup is
                        batched into one query regardless of favourite
                        count; the card's generated_at is the OLDER of the
                        rating's updated_at and the weather row's
                        fetched_at, and falls back to whichever of the two
                        is present alone.

The Open-Meteo network call is avoided throughout by patching
``apps.favourites.services.fetch_elevation``.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any
from unittest.mock import patch

import pytest
from django.db import connection
from django.template.loader import render_to_string
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone as django_timezone
from freezegun import freeze_time

from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.favourites.models import Favourite
from apps.weather.models import Weather
from tests.factories import (
    BulletinFactory,
    FavouriteFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    ResortFactory,
    UserFactory,
    WeatherFactory,
)

CREATE_URL = "/favourites/partials/create/"
RESORT_CREATE_URL = "/favourites/partials/resort/create/"
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


def _detail_url(uuid: object) -> str:
    """Build the full-page detail URL for a favourite's uuid (SNOW-507)."""
    return f"/favourites/{uuid}/"


def _resort_toggle_url(resort_id: object) -> str:
    """Build the resort-favourite-toggle URL for a resort's pk (SNOW-504)."""
    return f"/favourites/partials/resort/{resort_id}/toggle/"


def _problem_card_render_model(elevation: dict[str, Any] | None) -> dict[str, Any]:
    """Build a minimal current-version render_model dict with one dry problem.

    Mirrors the helper pattern in ``tests/public/test_bulletin_page.py``,
    scoped down to what ``favourite_card``'s problems section needs.

    Args:
        elevation: A render-model elevation dict (``{"lower", "upper",
            "treeline"}``), or ``None`` for a problem with no band data.

    Returns:
        A render_model dict at the current ``RENDER_MODEL_VERSION``.

    """
    problem = {
        "problem_type": "wind_slab",
        "comment_html": "<p>Wind slab comment text.</p>",
        "aspects": ["N", "NE", "E"],
        "elevation": elevation,
        "time_period": "all_day",
        "core_zone_text": None,
        "danger_rating_value": "moderate",
    }
    return {
        "version": RENDER_MODEL_VERSION,
        "source": "slf",
        "danger": {
            "key": "moderate",
            "number": "2",
            "subdivision": None,
            "ratings": [],
        },
        "danger_patterns": [],
        "traits": [
            {
                "category": "dry",
                "time_period": "all_day",
                "title": "Dry avalanches",
                "geography": {"source": "problems"},
                "problems": [problem],
                "prose": None,
                "danger_level": 2,
            }
        ],
        "metadata": {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": "2026-03-15T15:00:00+00:00",
            "unscheduled": False,
            "lang": "en",
        },
        "prose": {
            "snowpack_structure": "<p>The snowpack is generally stable.</p>",
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
            "avalanche_activity": {"highlights": "", "comment": ""},
            "tendency_lead": None,
        },
    }


def _make_todays_bulletin(region: Any, elevation: dict[str, Any] | None = None) -> Any:
    """Create a Bulletin valid for the whole of today, linked to *region*.

    A full-day (00:00–00:00) validity window always spans "now", so
    ``_select_bulletin_for_date`` picks it as today's default regardless of
    the time the test happens to run.

    Args:
        region: The MicroRegion the bulletin covers.
        elevation: Elevation dict passed to the single problem's render
            model entry; ``None`` for a problem with no band data.

    Returns:
        The created Bulletin.

    """
    today = django_timezone.localdate()
    vf = datetime.datetime.combine(today, datetime.time.min, tzinfo=datetime.UTC)
    vt = vf + datetime.timedelta(days=1)
    bulletin = BulletinFactory.create(
        issued_at=vf,
        valid_from=vf,
        valid_to=vt,
        render_model=_problem_card_render_model(elevation),
        render_model_version=RENDER_MODEL_VERSION,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin, region=region, region_name_at_time=region.name
    )
    return bulletin


def _create_via_service(
    user: Any, latitude: float = 46.1, longitude: float = 7.4
) -> Favourite:
    """Create a Favourite directly via the service, mocking the Open-Meteo call."""
    from apps.favourites.services import create_favourite  # noqa: PLC0415

    elevation = 1500.0
    with (
        patch("apps.favourites.services.fetch_elevation", return_value=elevation),
        patch("apps.favourites.services.region_for_point", return_value=None),
    ):
        return create_favourite(user, latitude, longitude)


# ---------------------------------------------------------------------------
# favourite_create — POST /favourites/partials/create/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteCreateAuthGate:
    """Anonymous users are rejected with 403."""

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous POST returns 403."""
        response = client.post(
            CREATE_URL, {"lat": "46.1", "lon": "7.4"}, **HTMX_HEADERS
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestFavouriteCreateHtmxGate:
    """Non-HTMX requests are rejected with 400."""

    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain POST without HX-Request returns 400."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(CREATE_URL, {"lat": "46.1", "lon": "7.4"})
        assert response.status_code == 400


@pytest.mark.django_db
class TestFavouriteCreateValidation:
    """Missing/unparseable lat or lon returns 400."""

    def test_missing_lat_returns_400(self, client: Client) -> None:
        """No lat provided → 400."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(CREATE_URL, {"lon": "7.4"}, **HTMX_HEADERS)
        assert response.status_code == 400

    def test_unparseable_lat_returns_400(self, client: Client) -> None:
        """A non-float lat → 400."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(
            CREATE_URL, {"lat": "not-a-number", "lon": "7.4"}, **HTMX_HEADERS
        )
        assert response.status_code == 400

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

    @pytest.mark.parametrize(
        ("lat", "lon"),
        [
            ("nan", "7.4"),
            ("46.1", "inf"),
            ("95", "7.4"),  # latitude > 90
            ("46.1", "200"),  # longitude > 180
        ],
    )
    def test_invalid_coordinates_return_400_no_row_no_external_call(
        self, client: Client, lat: str, lon: str
    ) -> None:
        """SNOW-464: non-finite / out-of-range coords are rejected before the
        Open-Meteo elevation lookup and any write.
        """
        user = UserFactory.create()
        client.force_login(user)
        with patch("apps.favourites.services.fetch_elevation") as mock_resolve:
            response = client.post(CREATE_URL, {"lat": lat, "lon": lon}, **HTMX_HEADERS)
        assert response.status_code == 400
        assert not Favourite.objects.filter(user=user).exists()
        mock_resolve.assert_not_called()


@pytest.mark.django_db
class TestFavouriteCreateSuccess:
    """A valid submission creates a row and returns the saved-pin partial."""

    def test_valid_submit_creates_favourite(self, client: Client) -> None:
        """Valid lat/lon creates a Favourite and returns 200."""
        user = UserFactory.create()
        client.force_login(user)
        elevation = 1500.0

        with (
            patch("apps.favourites.services.fetch_elevation", return_value=elevation),
            patch("apps.favourites.services.region_for_point", return_value=None),
        ):
            response = client.post(
                CREATE_URL,
                {"lat": "46.1", "lon": "7.4", "name": "My spot"},
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        favourite = Favourite.objects.get(user=user)
        assert favourite.name == "My spot"


@pytest.mark.django_db
class TestFavouriteCreateCap:
    """Reaching the per-user cap renders the limit-reached partial at 409."""

    def test_cap_reached_returns_409_with_limit_partial(
        self, client: Client, settings: Any
    ) -> None:
        """When the cap is reached, the create endpoint returns 409 with the error.

        409 (not the transient 429 or a swap-friendly 200) is what the client
        mutation queue needs to classify the doomed create as a permanent
        failure and stop retrying it — see SNOW-479 and ``favourite_create``.
        """
        settings.FAVOURITES_MAX_PER_USER = 1
        user = UserFactory.create()
        client.force_login(user)
        _create_via_service(user)

        elevation = 1500.0
        with (
            patch("apps.favourites.services.fetch_elevation", return_value=elevation),
            patch("apps.favourites.services.region_for_point", return_value=None),
        ):
            response = client.post(
                CREATE_URL, {"lat": "47.0", "lon": "8.0"}, **HTMX_HEADERS
            )

        assert response.status_code == 409
        assert Favourite.objects.filter(user=user).count() == 1
        content = response.content.decode()
        assert "limit" in content.lower()


@pytest.mark.django_db
class TestFavouriteCreateRateLimit:
    """Rate limit returns 429 when exceeded."""

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

        from apps.favourites.views import favourite_create  # noqa: PLC0415

        resp = favourite_create(request)
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# favourite_create_from_resort — POST /favourites/partials/resort/create/ (SNOW-499)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteCreateFromResortAuthGate:
    """Anonymous users are rejected with 403."""

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous POST returns 403."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        response = client.post(
            RESORT_CREATE_URL, {"resort_id": resort.pk}, **HTMX_HEADERS
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestFavouriteCreateFromResortHtmxGate:
    """Non-HTMX requests are rejected with 400."""

    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain POST without HX-Request returns 400."""
        user = UserFactory.create()
        client.force_login(user)
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        response = client.post(RESORT_CREATE_URL, {"resort_id": resort.pk})
        assert response.status_code == 400


@pytest.mark.django_db
class TestFavouriteCreateFromResortValidation:
    """Missing/non-integer resort_id → 400; unknown resort_id → 404."""

    def test_missing_resort_id_returns_400(self, client: Client) -> None:
        """No resort_id provided → 400."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(RESORT_CREATE_URL, {}, **HTMX_HEADERS)
        assert response.status_code == 400

    def test_non_integer_resort_id_returns_400(self, client: Client) -> None:
        """A non-integer resort_id → 400."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(
            RESORT_CREATE_URL, {"resort_id": "not-an-int"}, **HTMX_HEADERS
        )
        assert response.status_code == 400

    def test_unknown_resort_id_returns_404(self, client: Client) -> None:
        """A resort_id with no matching row → 404."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(RESORT_CREATE_URL, {"resort_id": 999999}, **HTMX_HEADERS)
        assert response.status_code == 404


@pytest.mark.django_db
class TestFavouriteCreateFromResortRateLimit:
    """Rate limit returns 429 when exceeded."""

    def test_rate_limited_branch_returns_429(self, client: Client) -> None:
        """When request.limited is True (set by ratelimit decorator), view returns 429.

        Mirrors ``TestFavouriteCreateRateLimit`` above for the resort-create
        view — django-ratelimit ORs a pre-set ``request.limited=True`` with
        its own (unmet) check, so pre-setting it short-circuits into the
        429 branch.
        """
        user = UserFactory.create()
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)

        from django.contrib.sessions.backends.db import SessionStore  # noqa: PLC0415
        from django.test import RequestFactory  # noqa: PLC0415
        from django_htmx.middleware import HtmxMiddleware  # noqa: PLC0415

        rf = RequestFactory()
        request = rf.post(
            RESORT_CREATE_URL,
            {"resort_id": resort.pk},
            HTTP_HX_REQUEST="true",
        )
        request.limited = True  # type: ignore[attr-defined]
        request.user = user
        request.session = SessionStore()

        from django.http import HttpResponse as _HR  # noqa: PLC0415

        htmx_mw = HtmxMiddleware(lambda r: _HR())
        htmx_mw(request)

        from apps.favourites.views import favourite_create_from_resort  # noqa: PLC0415

        resp = favourite_create_from_resort(request)
        assert resp.status_code == 429


@pytest.mark.django_db
class TestFavouriteCreateFromResortUngeocoded:
    """An ungeocoded resort cannot be favourited — 422."""

    def test_ungeocoded_resort_returns_422(self, client: Client) -> None:
        """A resort with no latitude/longitude returns 422, no row created."""
        user = UserFactory.create()
        client.force_login(user)
        resort = ResortFactory.create(latitude=None, longitude=None)

        response = client.post(
            RESORT_CREATE_URL, {"resort_id": resort.pk}, **HTMX_HEADERS
        )

        assert response.status_code == 422
        assert not Favourite.objects.filter(user=user).exists()


@pytest.mark.django_db
class TestFavouriteCreateFromResortSuccess:
    """A valid submission creates a resort-linked Favourite and returns 200."""

    def test_valid_submit_creates_resort_favourite(self, client: Client) -> None:
        """A geocoded resort_id creates a Favourite with the resort FK set."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(
            name="Verbier", region=region, latitude=46.1, longitude=7.4
        )
        elevation = 1500.0

        with patch("apps.favourites.services.fetch_elevation", return_value=elevation):
            response = client.post(
                RESORT_CREATE_URL, {"resort_id": resort.pk}, **HTMX_HEADERS
            )

        assert response.status_code == 200
        favourite = Favourite.objects.get(user=user)
        assert favourite.resort == resort
        assert favourite.region == region
        assert favourite.name == "Verbier"

    def test_repeat_submit_is_idempotent(self, client: Client) -> None:
        """POSTing the same resort_id twice returns 200 both times, one row."""
        user = UserFactory.create()
        client.force_login(user)
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        elevation = 1500.0

        with patch("apps.favourites.services.fetch_elevation", return_value=elevation):
            first = client.post(
                RESORT_CREATE_URL, {"resort_id": resort.pk}, **HTMX_HEADERS
            )
            second = client.post(
                RESORT_CREATE_URL, {"resort_id": resort.pk}, **HTMX_HEADERS
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert Favourite.objects.filter(user=user, resort=resort).count() == 1
        first_favourite = Favourite.objects.get(user=user, resort=resort)
        assert str(first_favourite.uuid) in second.content.decode()


@pytest.mark.django_db
class TestFavouriteCreateFromResortCap:
    """Reaching the per-user cap renders the limit-reached partial at 409."""

    def test_cap_reached_returns_409_with_limit_partial(
        self, client: Client, settings: Any
    ) -> None:
        """The resort-create endpoint shares the same 409-at-cap contract."""
        settings.FAVOURITES_MAX_PER_USER = 1
        user = UserFactory.create()
        client.force_login(user)
        _create_via_service(user)

        resort = ResortFactory.create(latitude=47.0, longitude=8.0)
        elevation = 1500.0
        with patch("apps.favourites.services.fetch_elevation", return_value=elevation):
            response = client.post(
                RESORT_CREATE_URL, {"resort_id": resort.pk}, **HTMX_HEADERS
            )

        assert response.status_code == 409
        assert Favourite.objects.filter(user=user).count() == 1
        content = response.content.decode()
        assert "limit" in content.lower()


# ---------------------------------------------------------------------------
# favourite_resort_toggle — POST /favourites/partials/resort/<id>/toggle/ (SNOW-504)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteResortToggleAuthGate:
    """Anonymous users are rejected with 403."""

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous POST returns 403."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        response = client.post(_resort_toggle_url(resort.pk), **HTMX_HEADERS)
        assert response.status_code == 403


@pytest.mark.django_db
class TestFavouriteResortToggleHtmxGate:
    """Non-HTMX requests are rejected with 400."""

    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain POST without HX-Request returns 400."""
        user = UserFactory.create()
        client.force_login(user)
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        response = client.post(_resort_toggle_url(resort.pk))
        assert response.status_code == 400


@pytest.mark.django_db
class TestFavouriteResortToggleValidation:
    """An unknown resort_id → 404."""

    def test_unknown_resort_id_returns_404(self, client: Client) -> None:
        """A resort_id with no matching row → 404."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(_resort_toggle_url(999999), **HTMX_HEADERS)
        assert response.status_code == 404


@pytest.mark.django_db
class TestFavouriteResortToggleUngeocoded:
    """An ungeocoded resort cannot be favourited — 422."""

    def test_ungeocoded_resort_returns_422(self, client: Client) -> None:
        """A resort with no latitude/longitude returns 422, no row created."""
        user = UserFactory.create()
        client.force_login(user)
        resort = ResortFactory.create(latitude=None, longitude=None)

        response = client.post(_resort_toggle_url(resort.pk), **HTMX_HEADERS)

        assert response.status_code == 422
        assert not Favourite.objects.filter(user=user).exists()


@pytest.mark.django_db
class TestFavouriteResortToggleRateLimit:
    """Rate limit returns 429 when exceeded."""

    def test_rate_limited_branch_returns_429(self, client: Client) -> None:
        """When request.limited is True (set by ratelimit decorator), view returns 429.

        Mirrors ``TestFavouriteCreateFromResortRateLimit`` above for the
        resort-toggle view — django-ratelimit ORs a pre-set
        ``request.limited=True`` with its own (unmet) check, so pre-setting
        it short-circuits into the 429 branch.
        """
        user = UserFactory.create()
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)

        from django.contrib.sessions.backends.db import SessionStore  # noqa: PLC0415
        from django.test import RequestFactory  # noqa: PLC0415
        from django_htmx.middleware import HtmxMiddleware  # noqa: PLC0415

        rf = RequestFactory()
        request = rf.post(
            _resort_toggle_url(resort.pk),
            HTTP_HX_REQUEST="true",
        )
        request.limited = True  # type: ignore[attr-defined]
        request.user = user
        request.session = SessionStore()

        from django.http import HttpResponse as _HR  # noqa: PLC0415

        htmx_mw = HtmxMiddleware(lambda r: _HR())
        htmx_mw(request)

        from apps.favourites.views import favourite_resort_toggle  # noqa: PLC0415

        resp = favourite_resort_toggle(request, resort.pk)
        assert resp.status_code == 429


@pytest.mark.django_db
class TestFavouriteResortToggleSuccess:
    """Toggling creates then deletes the (user, resort) Favourite."""

    def test_toggle_creates_then_deletes(self, client: Client) -> None:
        """First POST creates a Favourite; second POST deletes it."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(
            name="Verbier", region=region, latitude=46.1, longitude=7.4
        )
        elevation = 1500.0

        with patch("apps.favourites.services.fetch_elevation", return_value=elevation):
            first = client.post(_resort_toggle_url(resort.pk), **HTMX_HEADERS)
            assert first.status_code == 200
            assert Favourite.objects.filter(user=user, resort=resort).exists()
            assert 'data-favourited="true"' in first.content.decode()

            second = client.post(_resort_toggle_url(resort.pk), **HTMX_HEADERS)
            assert second.status_code == 200
            assert not Favourite.objects.filter(user=user, resort=resort).exists()
            assert 'data-favourited="false"' in second.content.decode()


@pytest.mark.django_db
class TestFavouriteResortToggleCap:
    """Reaching the per-user cap renders the limit-reached partial at 409."""

    def test_cap_reached_returns_409_with_limit_partial(
        self, client: Client, settings: Any
    ) -> None:
        """The toggle endpoint shares the same 409-at-cap contract when creating."""
        settings.FAVOURITES_MAX_PER_USER = 1
        user = UserFactory.create()
        client.force_login(user)
        _create_via_service(user)

        resort = ResortFactory.create(latitude=47.0, longitude=8.0)
        elevation = 1500.0
        with patch("apps.favourites.services.fetch_elevation", return_value=elevation):
            response = client.post(_resort_toggle_url(resort.pk), **HTMX_HEADERS)

        assert response.status_code == 409
        assert Favourite.objects.filter(user=user).count() == 1
        content = response.content.decode()
        assert "limit" in content.lower()


# ---------------------------------------------------------------------------
# favourite_rename — POST /favourites/partials/<uuid>/rename/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteRenameOwnerIsolation:
    """Owner isolation — user A cannot rename user B's pin."""

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

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous POST returns 403."""
        response = client.post(
            _rename_url("00000000-0000-0000-0000-000000000000"),
            {"name": "x"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 403

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

    def test_owner_can_delete(self, client: Client) -> None:
        """The owning user can delete their own favourite."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = _create_via_service(user)

        response = client.post(_delete_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        assert not Favourite.objects.filter(pk=favourite.pk).exists()

    def test_other_user_cannot_delete(self, client: Client) -> None:
        """A different user attempting to delete gets 404, row survives."""
        owner = UserFactory.create()
        other_user = UserFactory.create()
        favourite = _create_via_service(owner)

        client.force_login(other_user)
        response = client.post(_delete_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 404
        assert Favourite.objects.filter(pk=favourite.pk).exists()

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

    def test_it_carries_no_row_of_its_own(self, client: Client) -> None:
        """The card shows a favourite; it does not manage one (SNOW-711).

        It rendered a copy of the list's row until SNOW-711 put the card
        UNDERNEATH that very row, at which point the copy read as the same
        row printed twice. Managing a pin belongs to the surface that
        lists it, so the card carries no row, no rename control and no
        Remove — the ones fourteen pixels above it already do that job.
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="My spot")

        content = client.get(_card_url(favourite.uuid), **HTMX_HEADERS).content.decode()

        # The card still names the pin — in its heading, not in a row.
        assert "My spot" in content
        assert "data-row-label" not in content
        assert "data-row-rename" not in content
        assert f"{favourite.uuid}/delete" not in content

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

    def test_non_owner_uuid_returns_404(self, client: Client) -> None:
        """A different user's uuid returns 404, not 403 — no existence oracle."""
        owner = UserFactory.create()
        other_user = UserFactory.create()
        favourite = FavouriteFactory.create(user=owner)

        client.force_login(other_user)
        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 404

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous GET returns 403."""
        response = client.get(
            _card_url("00000000-0000-0000-0000-000000000000"), **HTMX_HEADERS
        )
        assert response.status_code == 403

    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain GET without HX-Request returns 400."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)

        response = client.get(_card_url(favourite.uuid))

        assert response.status_code == 400

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

    def test_no_weather_row_falls_back_to_the_ratings_timestamp(
        self, client: Client
    ) -> None:
        """A pin with a rating but no weather stamps the rating's own timestamp.

        The freshness pair takes the older of the rating's ``updated_at``
        and the weather row's ``fetched_at``. With one of the two absent
        there is nothing to compare against, so the present one stands
        alone rather than the header being dropped or the view raising.

        This test previously used a region-less favourite, which made
        ``day_rating`` None regardless of weather and left it asserting
        only that the header existed at all — it could not have failed.
        """
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        rating = RegionDayRatingFactory.create(region=region, max_rating="considerable")
        favourite = FavouriteFactory.create(user=user, region=region)
        assert not Weather.objects.exists()

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        assert response["X-Data-Generated-At"] == rating.updated_at.isoformat(
            timespec="seconds"
        )

    def test_freshness_reports_the_staler_of_rating_and_weather(
        self, client: Client
    ) -> None:
        """A card mixing both constituents is only as fresh as its stalest one.

        The rating is written now; the weather row was fetched six hours
        ago. The card renders both, so the header must carry the weather's
        ``fetched_at`` — reporting the rating's timestamp would tell the
        offline client the whole card is newer than half of it is.
        """
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, max_rating="considerable")
        favourite = FavouriteFactory.create(user=user, region=region)
        stale_fetched_at = django_timezone.now() - datetime.timedelta(hours=6)
        WeatherFactory.create(
            location=favourite.location,
            observed_on=django_timezone.localdate(),
            fetched_at=stale_fetched_at,
        )

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        assert response["X-Data-Generated-At"] == stale_fetched_at.isoformat(
            timespec="seconds"
        )


# ---------------------------------------------------------------------------
# favourite_detail — GET /favourites/<uuid>/ (SNOW-507)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteDetail:
    """favourite_detail — the favourite's own full, bookmarkable page (SNOW-507)."""

    def test_owner_gets_200_full_page(self, client: Client) -> None:
        """Owner GET renders a full page with page chrome and the card content."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, max_rating="considerable")
        favourite = FavouriteFactory.create(user=user, name="My spot", region=region)

        response = client.get(_detail_url(favourite.uuid))

        assert response.status_code == 200
        content = response.content.decode()
        # Page chrome — a real page, not an HTMX fragment.
        assert "<title>" in content
        assert "My spot" in content
        # The card content, reused verbatim.
        assert 'data-testid="favourite-card"' in content
        assert 'data-testid="favourite-card-rating-chip"' in content
        assert 'data-testid="favourite-card-bulletin-link"' in content
        assert region.get_absolute_url() in content

    def test_back_link_present_when_region_set(self, client: Client) -> None:
        """A "Region bulletin" back-link is shown when favourite.region is set."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        favourite = FavouriteFactory.create(user=user, region=region)

        response = client.get(_detail_url(favourite.uuid))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="favourite-detail-back-link"' in content
        assert region.get_absolute_url() in content

    def test_back_link_absent_when_region_none(self, client: Client) -> None:
        """No back-link is shown when the favourite has no resolved region."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, region=None)

        response = client.get(_detail_url(favourite.uuid))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="favourite-detail-back-link"' not in content
        assert 'data-testid="favourite-card-no-coverage"' in content

    def test_non_owner_uuid_returns_404(self, client: Client) -> None:
        """A different user's uuid returns 404, not 403 — no existence oracle."""
        owner = UserFactory.create()
        other_user = UserFactory.create()
        favourite = FavouriteFactory.create(user=owner)

        client.force_login(other_user)
        response = client.get(_detail_url(favourite.uuid))

        assert response.status_code == 404

    def test_unknown_uuid_returns_404(self, client: Client) -> None:
        """An unknown uuid returns 404."""
        user = UserFactory.create()
        client.force_login(user)

        response = client.get(_detail_url("00000000-0000-0000-0000-000000000000"))

        assert response.status_code == 404

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous GET returns 403."""
        response = client.get(_detail_url("00000000-0000-0000-0000-000000000000"))
        assert response.status_code == 403

    def test_cache_control_is_private_no_store(self, client: Client) -> None:
        """The response is never cacheable by a shared cache — it's per-user."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)

        response = client.get(_detail_url(favourite.uuid))

        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"

    def test_response_carries_freshness_header(self, client: Client) -> None:
        """The page stamps the SNOW-370/418 freshness headers, same as the card."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)

        response = client.get(_detail_url(favourite.uuid))

        assert response.status_code == 200
        assert "X-Data-Generated-At" in response

    def test_no_htmx_header_required(self, client: Client) -> None:
        """Unlike favourite_card, a plain (non-HTMX) GET is not rejected — it's a real page."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)

        response = client.get(_detail_url(favourite.uuid))

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# _favourite_card.html — the title's heading rank, per caller
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteCardHeadingRank:
    """The card's title ranks itself for the outline it lands in.

    One partial, two callers at two depths, so the level is the caller's
    to state (``heading_tag``) and the size never moves — ``text-lg`` in
    both cases. The card was a fixed ``<h2>`` before SNOW-507: correct for
    neither caller then, since the full page had no ``<h1>`` at all and the
    account hub's panel put a single pin's title level with the
    "Favourites" section heading that contained it.

    The panel's own rank has since moved from ``h3`` to ``h2``, and not
    because the card changed: SNOW-668 lifted the list out of that section
    onto /account/favourites/, where the page ``<h1>`` is the only heading
    above it. static/js/favourites_offline.js paints its offline stand-in
    into the same slot and matches, so the two are changed together.
    """

    def _title_tag(self, content: str) -> str:
        """Return the element name the card's title is rendered as.

        Args:
            content: The rendered HTML.

        Returns:
            The tag name, e.g. "h1".

        """
        match = re.search(r'<(h[1-6])[^>]*data-testid="favourite-card-title"', content)
        assert match is not None, "card title heading not found"
        return match.group(1)

    def test_full_page_title_is_the_pages_h1(self, client: Client) -> None:
        """On its own page the pin's name IS the page — so it is the h1.

        favourite_detail.html renders no other heading (its back-link is a
        link), so an h2 here would leave a page whose outline starts at
        level 2.
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="My spot")

        content = client.get(_detail_url(favourite.uuid)).content.decode()

        assert self._title_tag(content) == "h1"

    def test_account_page_panel_title_is_an_h2(self, client: Client) -> None:
        """The hx-get card is an h2 — the page's own h1 is all that outranks it.

        It was an h3 while this list was a section inside the account hub,
        ranking under that section's eyebrow heading. SNOW-668 gave the list
        a page, which removed the intervening heading — and the offline
        stand-in in static/js/favourites_offline.js moved with it, because a
        pin's outline depth must not depend on whether the request reached
        the server.
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="My spot")

        content = client.get(_card_url(favourite.uuid), **HTMX_HEADERS).content.decode()

        assert self._title_tag(content) == "h2"

    def test_the_rank_is_the_only_thing_that_changes(self, client: Client) -> None:
        """Both callers draw the same title at the same size — this is not a visual change."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="My spot")

        page = client.get(_detail_url(favourite.uuid)).content.decode()
        card = client.get(_card_url(favourite.uuid), **HTMX_HEADERS).content.decode()

        for content in (page, card):
            assert (
                'class="text-lg font-semibold text-text-1" '
                'data-testid="favourite-card-title"'
            ) in content

    def test_default_is_h2_for_a_caller_that_states_nothing(self) -> None:
        """A third caller that passes no heading_tag gets the h2 the partial had.

        The map's favourites panel is not such a caller — it renders rows
        with ``hide_disclosure``, so it never fetches a card — but the
        default is what any future surface inherits, and h2 is right for a
        card dropped into a page with an h1 and no section heading between.
        """
        favourite = FavouriteFactory.create(name="My spot")

        content = render_to_string(
            "favourites/partials/_favourite_card.html",
            {"favourite": favourite, "problem_cards": []},
        )

        assert self._title_tag(content) == "h2"


# ---------------------------------------------------------------------------
# favourite_card — avalanche-problems section (SNOW-422)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteCardProblems:
    """favourite_card — elevation-aware problem highlighting (SNOW-422)."""

    def test_region_with_bulletin_renders_one_rating_block_per_card(
        self, client: Client
    ) -> None:
        """A favourite with a today's bulletin renders the problems section."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        _make_todays_bulletin(
            region, elevation={"lower": 2000, "upper": None, "treeline": False}
        )
        favourite = FavouriteFactory.create(user=user, region=region, elevation=2500.0)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "Avalanche problems" in content
        assert content.count('data-testid="rating-block"') == 1
        assert 'data-testid="altitude-relevance-chip"' in content
        assert 'data-relevance="APPLIES"' in content

    def test_region_less_favourite_has_no_problems_section(
        self, client: Client
    ) -> None:
        """A favourite with region=None never renders the problems section."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, region=None)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "Avalanche problems" not in content
        assert 'data-testid="rating-block"' not in content

    def test_problems_section_never_uses_the_word_safe(self, client: Client) -> None:
        """Copy is altitude-relative only — 'safe' must never appear (safety-sensitive).

        HTML comments are stripped before scanning and the match is on a word
        boundary, so neither the ``<!-- nosemgrep: ...-with-safe.-... -->``
        lint-suppression comment nor the unrelated ``unsafe_after_seconds``
        cache-payload JSON key false-positive the check.
        """
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        _make_todays_bulletin(
            region, elevation={"lower": 2000, "upper": None, "treeline": False}
        )
        favourite = FavouriteFactory.create(user=user, region=region, elevation=1500.0)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode().lower()
        content_without_comments = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        assert re.search(r"\bsafe\b", content_without_comments) is None
        assert 'data-relevance="above"' in content

    def test_problem_with_no_elevation_band_renders_unannotated(
        self, client: Client
    ) -> None:
        """A problem with no elevation band renders its card with no relevance chip."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        _make_todays_bulletin(region, elevation=None)
        favourite = FavouriteFactory.create(user=user, region=region, elevation=1500.0)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert content.count('data-testid="rating-block"') == 1
        assert 'data-testid="altitude-relevance-chip"' not in content


# ---------------------------------------------------------------------------
# favourite_list — GET /favourites/partials/list/
# ---------------------------------------------------------------------------


def _row_meta(html: str) -> str:
    """Return the text of the first row's muted meta line.

    The map row renders it as the shared row partial's
    ``[data-row-meta]`` span (includes/_ugc_panel_row.html).

    Args:
        html: A rendered favourites list.

    Returns:
        The span's text content.

    """
    match = re.search(r"<span data-row-meta[^>]*>([^<]*)</span>", html)
    assert match is not None, "no [data-row-meta] in the rendered list"
    return match.group(1).strip()


@pytest.mark.django_db
class TestFavouriteList:
    """favourite_list — owner-scoped favourites list (SNOW-415)."""

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

    def test_row_links_to_the_favourites_own_page(self, client: Client) -> None:
        """Each row carries one detail link to favourites:detail (SNOW-507)."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="Mine")

        response = client.get(LIST_URL, **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="favourite-list-detail-link"' in content
        assert _detail_url(favourite.uuid) in content

    def test_detail_link_is_htmx_enhanced_onto_the_rows_own_panel(
        self, client: Client
    ) -> None:
        """The chevron hx-gets the card into the panel under its own row.

        SNOW-658 collapsed the "Details" button and the "Open page →" link
        into one element: an href for the no-JS navigation, an hx-get so
        the card still renders in-page — and so favourites_offline.js still
        sees an HTMX swap to write through (SNOW-418). SNOW-711 made that
        element the row's trailing chevron and gave every row its own
        panel: there was ONE #favourite-card-panel above the whole list, so
        expanding the fifth row painted its card four rows away from it.
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="Mine")

        response = client.get(LIST_URL, **HTMX_HEADERS)

        content = response.content.decode()
        hx_get = f'hx-get="{_card_url(favourite.uuid)}"'
        assert hx_get in content
        assert f'hx-target="#favourite-panel-{favourite.uuid}"' in content
        assert f'id="favourite-panel-{favourite.uuid}"' in content
        # The panel is the row's next sibling, not a box somewhere above
        # it: that adjacency is the whole point of the change.
        row_at = content.index(f'id="favourite-{favourite.uuid}"')
        assert row_at < content.index(f'id="favourite-panel-{favourite.uuid}"')
        # The GET is carried by a link, never a button: a GET is a link, a
        # POST is an active control. (The row's own buttons — rename,
        # Remove — are POSTs, so they stay buttons.)
        opener = content[: content.index(hx_get)].rsplit("<", 1)[1]
        assert opener.startswith("a ") or opener.startswith("a\n")

    def test_the_disclosure_is_the_only_expand_control(self, client: Client) -> None:
        """One control, not a button and a link beside it.

        The row carried an underlined "Details →" until SNOW-711 — the one
        typographic control on a row whose other controls are icons.
        """
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, name="Mine")

        response = client.get(LIST_URL, **HTMX_HEADERS)

        content = response.content.decode()
        assert content.count("data-row-disclosure") == 1
        assert "Details" not in content
        # It names the row, because "Details" alone names nothing with a
        # list of pins on screen.
        assert 'aria-label="Show details for Mine"' in content
        assert 'aria-expanded="false"' in content

    def test_default_variant_offers_the_map_link(self, client: Client) -> None:
        """The manage-page template keeps its "view on the map" navigation."""
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, name="Mine")

        response = client.get(LIST_URL, **HTMX_HEADERS)

        assert b"View favourites on the map" in response.content

    def test_map_variant_renders_the_lean_template(self, client: Client) -> None:
        """``?variant=map`` drops the card panel and the map link (SNOW-658)."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="Mine")

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "Mine" in content
        assert 'data-testid="favourite-card-panel"' not in content
        assert f'hx-get="{_card_url(favourite.uuid)}"' not in content
        assert "View favourites on the map" not in content

    def test_map_variant_frames_the_pin(self, client: Client) -> None:
        """The row's name carries the pin's coordinates and is a button.

        Hugo: "For routes, resorts, and observations, clicking on the name
        of an item should zoom in to it." A favourite is both of the first
        two — a dropped pin and a saved resort are one model — so one
        control serves both.

        The ordinates are formatted through ``%f`` in Python rather than
        interpolated as floats: a template renders a float through the
        active locale, and a decimal comma in a comma-separated attribute
        cannot even be split apart again.
        """
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, name="Mine", latitude=46.1, longitude=7.5)

        content = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS).content.decode()

        assert 'data-row-focus="7.500000,46.100000"' in content
        assert 'aria-label="Zoom to Mine"' in content

    def test_map_variant_frames_a_pin_by_the_coordinates_the_layer_draws(
        self, client: Client
    ) -> None:
        """The row and favourites_geojson agree on where the pin is.

        Two sources for one coordinate is how a camera comes to rest beside
        a pin rather than on it, so this pins them to the same pair.
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, latitude=45.9, longitude=6.87)

        content = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS).content.decode()
        feature = client.get(GEOJSON_URL).json()["features"][0]

        assert (
            f'data-row-focus="{favourite.longitude:f},{favourite.latitude:f}"'
            in content
        )
        assert feature["geometry"]["coordinates"] == [
            favourite.longitude,
            favourite.latitude,
        ]

    def test_default_variant_has_no_focus_control(self, client: Client) -> None:
        """/account/favourites/ renders the same row with an inert name.

        There is no map on that page to fly, and a button that did nothing
        would read as broken rather than as absent.
        """
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, name="Mine")

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert "data-row-focus" not in content
        assert "Zoom to" not in content

    def test_map_variant_keeps_the_roster_sidecar(self, client: Client) -> None:
        """The sheet still caches the roster for offline reads."""
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, name="Mine")

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        assert 'id="favourites-roster-cache"' in response.content.decode()

    def test_map_variant_does_not_link_the_row_anywhere(self, client: Client) -> None:
        """The map row has no NAVIGATION (SNOW-658, Hugo's design).

        It had three, in turn, in three days: a "Details →" control beside
        the title, the title itself as a link, and the title as a
        click-to-rename target. None came back. A favourite's detail page is
        reached by tapping its pin on the map, which is where the user
        already is, and renaming is the pencil's job.

        The primary line is a control again — it frames the pin on the map —
        but that is not navigation: it leaves the user on the page they are
        on, which is the whole reason this row has no detail link.
        ``test_map_variant_frames_the_pin`` below covers it.
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="Mine")

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        content = response.content.decode()
        assert _detail_url(favourite.uuid) not in content
        assert "Details" not in content

    def test_map_variant_meta_line_is_the_saved_date(self, client: Client) -> None:
        """The row's second line dates the pin — it used to give coordinates.

        SNOW-658, Hugo: "Replace the lat/lon on the favourite with the
        timestamp." A pair of four-decimal numbers is precise, unmemorable
        and impossible to place; the date is what orders a list of saved
        places and tells two similar pins apart.
        """
        user = UserFactory.create()
        client.force_login(user)
        with freeze_time("2026-02-03T09:00:00Z"):
            favourite = FavouriteFactory.create(user=user, name="Mine")

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        assert _row_meta(response.content.decode()) == "Saved 3 Feb 2026"
        # The coordinates are gone entirely, not merely demoted. Asserted
        # against the row's own meta line rather than the whole body: the
        # roster sidecar this list still carries for offline reads
        # (SNOW-418) is JSON, and legitimately holds the coordinates.
        assert f"{favourite.latitude:.4f}" not in _row_meta(response.content.decode())

    def test_map_variant_meta_line_prefixes_the_region(self, client: Client) -> None:
        """A pin that matched a region reads "<region> · saved <date>".

        Hugo's own design for this row — "Alpstein · saved 3 Feb". Region
        is nullable (a pin can fall outside every known boundary), which is
        why the date alone is the default rather than the exception.
        """
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create(name="Alpstein")
        with freeze_time("2026-02-03T09:00:00Z"):
            FavouriteFactory.create(user=user, name="Mine", region=region)

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        assert _row_meta(response.content.decode()) == "Alpstein · saved 3 Feb 2026"

    def test_map_variant_label_is_not_a_rename_trigger(self, client: Client) -> None:
        """The label carries no click affordance — the pencil is the trigger.

        SNOW-658, Hugo: "We have inline editing & the pencil - choose one."
        The label is a ``<span>``, so its click was mouse-only; the pencil
        is a real 44x44 button in the tab order. What went with it is the
        pair of classes that advertised the label as editable.
        """
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, name="Mine")

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        content = response.content.decode()
        assert "cursor-text" not in content
        assert "hover:border-border-strong" not in content
        # The pencil is still there — this is a choice between two, not a
        # removal of both.
        assert "data-row-rename" in content

    def test_map_variant_renames_in_place_on_the_label(self, client: Client) -> None:
        """The row carries its own inline editor, hidden until asked for.

        Not an always-visible field (that is the manage page's row, and a
        list of live inputs is not a list at rest) and not a
        ``window.prompt`` (which is what this row had for a day). The
        editor is server-rendered so its aria-label passes through a
        template, and static/js/inline_rename.js reveals it.
        """
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, name="Mine")

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        content = response.content.decode()
        assert "data-row-renameable" in content
        assert "data-row-rename-input" in content
        # Hidden at rest: the row reads as a row until the user asks.
        editor = content[content.index("data-row-rename-input") :]
        assert "hidden" in editor[: editor.index(">")]
        # It is not a form field — the commit is a fetch, not a submit —
        # so it carries no `name`, and the map variant still has none.
        assert 'name="name"' not in content

    def test_map_variant_offers_rename_and_remove_as_icon_controls(
        self, client: Client
    ) -> None:
        """Both actions are visible controls on the row, trash last.

        Hugo's design: no ellipsis menu on any panel. Remove is one tap in
        the same place on every panel's rows, and Rename — this panel's own
        extra — is the pencil immediately before it.
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="Mine")

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        content = response.content.decode()
        assert 'role="menu"' not in content
        rename = content.index(f'data-favourite-rename="{favourite.uuid}"')
        remove = content.index(_delete_url(favourite.uuid))
        assert rename < remove
        # Each names the row it acts on — "Rename" alone names nothing
        # with a list of pins on screen.
        assert 'aria-label="Rename Mine"' in content
        assert 'aria-label="Remove Mine"' in content

    def test_both_variants_render_the_same_shared_row(self, client: Client) -> None:
        """The account page's row IS the map's row now (SNOW-711).

        This reverses the assertion SNOW-658 left here, which pinned the
        account page's always-visible name field and underlined "Remove" as
        deliberate. They were not deliberate for long: the same pin read
        one way on the map and another on /account/, and this was the last
        surface managing user data with a text field and a text button.
        Only the disclosure differs — the map reaches a pin's detail by
        tapping the pin.
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="Mine")

        account = client.get(LIST_URL, **HTMX_HEADERS).content.decode()
        sheet = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS).content.decode()

        for hook in (
            "data-row-renameable",
            "data-row-rename-input",
            f'data-favourite-rename="{favourite.uuid}"',
            _delete_url(favourite.uuid),
        ):
            assert hook in account, hook
            assert hook in sheet, hook
        # The always-visible input is gone from both. It was never
        # reachable without JS anyway: /account/ loads this list by hx-get.
        assert 'name="name"' not in account
        # One slot differs, and only that one.
        assert "data-row-disclosure" in account
        assert "data-row-disclosure" not in sheet

    def test_both_variants_address_a_row_by_the_same_id(self, client: Client) -> None:
        """``favourite-<uuid>`` on both, so a Remove targets it either way."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="Mine")

        account = client.get(LIST_URL, **HTMX_HEADERS).content.decode()
        sheet = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS).content.decode()

        assert f'id="favourite-{favourite.uuid}"' in account
        assert f'id="favourite-{favourite.uuid}"' in sheet
        assert f'hx-target="#favourite-{favourite.uuid}"' in account
        assert f'hx-target="#favourite-{favourite.uuid}"' in sheet

    def test_the_list_carries_the_rename_url_template(self, client: Client) -> None:
        """account_favourites.js builds a row's rename URL from this.

        On the list rather than on each row: every row would carry the same
        string, and this element is rendered by the same endpoint the rows
        are.
        """
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, name="Mine")

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert 'data-rename-url-template="/favourites/partials/__UUID__/rename/"' in (
            content
        )

    def test_map_variant_empty_state(self, client: Client) -> None:
        """A user with no favourites sees the empty-state copy in the sheet."""
        user = UserFactory.create()
        client.force_login(user)

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        assert response.status_code == 200
        assert b"no saved favourites" in response.content.lower()

    @pytest.mark.parametrize("variant", ["", "unknown", "../../etc/passwd"])
    def test_unknown_variant_falls_back_to_the_manage_template(
        self, client: Client, variant: str
    ) -> None:
        """An unrecognised variant never reaches a template path."""
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, name="Mine")

        response = client.get(f"{LIST_URL}?variant={variant}", **HTMX_HEADERS)

        assert response.status_code == 200
        assert b'data-testid="favourite-card-panel"' in response.content

    def test_empty_state_when_no_favourites(self, client: Client) -> None:
        """A user with no favourites sees the empty-state copy."""
        user = UserFactory.create()
        client.force_login(user)

        response = client.get(LIST_URL, **HTMX_HEADERS)

        assert response.status_code == 200
        assert b"no saved favourites" in response.content.lower()

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous GET returns 403."""
        response = client.get(LIST_URL, **HTMX_HEADERS)
        assert response.status_code == 403

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

    def test_cache_control_is_private_no_store(self, client: Client) -> None:
        """The response carries Cache-Control: private, no-store."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.get(GEOJSON_URL)
        assert response["Cache-Control"] == "private, no-store"

    def test_resort_id_is_null_for_a_plain_pin(self, client: Client) -> None:
        """A dropped-pin favourite's resort_id property is null (SNOW-499)."""
        user = UserFactory.create()
        client.force_login(user)
        _create_via_service(user)

        response = client.get(GEOJSON_URL)

        data = response.json()
        assert data["features"][0]["properties"]["resort_id"] is None

    def test_resort_id_is_set_for_a_resort_favourite(self, client: Client) -> None:
        """A resort favourite's resort_id property matches the linked Resort (SNOW-499)."""
        from apps.favourites.services import create_resort_favourite  # noqa: PLC0415

        user = UserFactory.create()
        client.force_login(user)
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        elevation = 1500.0
        with patch("apps.favourites.services.fetch_elevation", return_value=elevation):
            create_resort_favourite(user, resort)

        response = client.get(GEOJSON_URL)

        data = response.json()
        assert data["features"][0]["properties"]["resort_id"] == resort.pk

    def test_created_at_is_an_iso_timestamp(self, client: Client) -> None:
        """Each feature carries the pin's save time as ISO-8601 (SNOW-658).

        The map's pin popup renders it as a relative "saved" subheader, and
        reads it off the feature so the popup still opens offline.
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = _create_via_service(user)

        response = client.get(GEOJSON_URL)

        data = response.json()
        created_at = data["features"][0]["properties"]["created_at"]
        assert created_at == favourite.created_at.isoformat()
        assert datetime.datetime.fromisoformat(created_at) == favourite.created_at

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous GET returns 403."""
        response = client.get(GEOJSON_URL)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# SNOW-418 — freshness headers + offline-cache sidecars
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouriteCardFreshness:
    """favourite_card — freshness headers + cache_payload sidecar (SNOW-418)."""

    def test_emits_generated_at_and_unsafe_after_with_rating(
        self, client: Client
    ) -> None:
        """With a rating, headers carry its updated_at and the 48h horizon."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        with freeze_time("2026-01-01T12:00:00Z"):
            rating = RegionDayRatingFactory.create(
                region=region, max_rating="considerable"
            )
        favourite = FavouriteFactory.create(user=user, region=region)

        with freeze_time("2026-01-01T13:00:00Z"):
            response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        assert response["X-Data-Generated-At"] == rating.updated_at.isoformat(
            timespec="seconds"
        )
        assert response["X-Data-Unsafe-After"] == "172800"

    def test_no_region_omits_unsafe_after_header(self, client: Client) -> None:
        """A favourite with no region has no rating, so no unsafe-after header."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, region=None)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        assert "X-Data-Generated-At" in response
        assert "X-Data-Unsafe-After" not in response

    def test_cache_payload_sidecar_has_expected_keys(self, client: Client) -> None:
        """The rendered card carries a favourite-card-cache json_script sidecar."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(
            region=region, max_rating="considerable", max_subdivision="+"
        )
        favourite = FavouriteFactory.create(user=user, region=region, name="My spot")

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="favourite-card-cache"' in content

        payload = _extract_json_script(content, "favourite-card-cache")
        assert payload["uuid"] == str(favourite.uuid)
        assert payload["name"] == "My spot"
        assert payload["region"]["id"] == region.pk
        assert payload["rating"]["max_rating"] == "considerable"
        assert payload["rating"]["max_subdivision"] == "+"
        assert payload["rating"]["digit"] == "3"
        assert payload["unsafe_after_seconds"] == 172800
        assert "generated_at" in payload

    def test_freshness_indicator_shown_when_rating_exists(self, client: Client) -> None:
        """The card renders the freshness indicator partial when a rating exists."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, max_rating="low")
        favourite = FavouriteFactory.create(user=user, region=region)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="freshness-indicator"' in content

    def test_no_freshness_indicator_without_rating(self, client: Client) -> None:
        """No rating for today → no freshness indicator (nothing to date-stamp)."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        favourite = FavouriteFactory.create(user=user, region=region)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="freshness-indicator"' not in content


@pytest.mark.django_db
class TestFavouriteListFreshness:
    """favourite_list — freshness headers + roster_payload sidecar (SNOW-418)."""

    def test_no_ratings_omits_unsafe_after_header(self, client: Client) -> None:
        """With no ratings at all, the response has no unsafe-after header."""
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, region=None)

        response = client.get(LIST_URL, **HTMX_HEADERS)

        assert response.status_code == 200
        assert "X-Data-Generated-At" in response
        assert "X-Data-Unsafe-After" not in response

    def test_generated_at_is_the_oldest_present_rating(self, client: Client) -> None:
        """generated_at is the OLDEST rating's updated_at, not the newest."""
        user = UserFactory.create()
        client.force_login(user)
        region_old = MicroRegionFactory.create()
        region_new = MicroRegionFactory.create()

        with freeze_time("2026-01-01T00:00:00Z"):
            older = RegionDayRatingFactory.create(region=region_old, max_rating="low")
        with freeze_time("2026-01-01T06:00:00Z"):
            RegionDayRatingFactory.create(region=region_new, max_rating="high")

        FavouriteFactory.create(user=user, region=region_old)
        FavouriteFactory.create(user=user, region=region_new)

        with freeze_time("2026-01-01T12:00:00Z"):
            response = client.get(LIST_URL, **HTMX_HEADERS)

        assert response.status_code == 200
        assert response["X-Data-Generated-At"] == older.updated_at.isoformat(
            timespec="seconds"
        )
        assert response["X-Data-Unsafe-After"] == "172800"

    def test_roster_payload_sidecar_has_expected_keys(self, client: Client) -> None:
        """The rendered list carries a favourites-roster-cache json_script sidecar."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, max_rating="high")
        favourite = FavouriteFactory.create(user=user, region=region, name="Mine")

        response = client.get(LIST_URL, **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="favourites-roster-cache"' in content

        roster = _extract_json_script(content, "favourites-roster-cache")
        assert isinstance(roster, list)
        [record] = [r for r in roster if r["uuid"] == str(favourite.uuid)]
        assert record["name"] == "Mine"
        assert record["rating"]["max_rating"] == "high"
        assert "generated_at" in record
        assert record["unsafe_after_seconds"] == 172800

    def test_rating_lookup_is_batched_not_n_plus_one(self, client: Client) -> None:
        """The rating lookup query count does not scale with favourite count.

        A "warm-up" GET runs uncounted before either capture — the waffle
        flag lookup and the CSP-rule lookup are each cached in-process
        after their first hit, so the very first request in a test run
        always costs a couple more queries than every subsequent one,
        independent of anything this test is trying to measure. Without
        the warm-up, that one-off cache-fill cost would be misread as
        this view's rating lookup scaling with the favourite count.
        """
        user = UserFactory.create()
        client.force_login(user)

        region_a = MicroRegionFactory.create()
        region_b = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region_a, max_rating="considerable")
        RegionDayRatingFactory.create(region=region_b, max_rating="high")
        FavouriteFactory.create(user=user, region=region_a)
        FavouriteFactory.create(user=user, region=region_b)

        # Uncounted warm-up — see docstring.
        client.get(LIST_URL, **HTMX_HEADERS)

        with CaptureQueriesContext(connection) as ctx_small:
            response = client.get(LIST_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        small_count = len(ctx_small.captured_queries)

        region_c = MicroRegionFactory.create()
        region_d = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region_c, max_rating="low")
        RegionDayRatingFactory.create(region=region_d, max_rating="moderate")
        FavouriteFactory.create(user=user, region=region_c)
        FavouriteFactory.create(user=user, region=region_d)

        with CaptureQueriesContext(connection) as ctx_large:
            response = client.get(LIST_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        large_count = len(ctx_large.captured_queries)

        assert large_count == small_count, (
            "favourite_list's query count scaled with the favourite count — "
            "the RegionDayRating lookup is no longer batched into one query."
        )


def _extract_json_script(html: str, element_id: str) -> Any:
    """Parse the JSON body of a Django ``json_script``-rendered ``<script>`` tag.

    Args:
        html: The full rendered HTML response body.
        element_id: The ``id`` attribute of the target ``<script>`` tag.

    Returns:
        The parsed JSON value.

    """
    marker = f'id="{element_id}"'
    tag_start = html.index(marker)
    content_start = html.index(">", tag_start) + 1
    content_end = html.index("</script>", content_start)
    return json.loads(html[content_start:content_end])
