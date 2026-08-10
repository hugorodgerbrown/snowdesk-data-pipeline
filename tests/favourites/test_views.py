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
                    fallback; no weather snapshot yet → "coming soon"
                    empty state (SNOW-415); with ForecastPointWeather
                    rows → forecast panel (day strip + hourly detail)
                    renders, response carries X-Data-Generated-At
                    (SNOW-417).
  favourite_detail (SNOW-507) — owner GET 200 full page with page chrome
                    plus the card content; non-owner uuid → 404; unknown
                    uuid → 404; anon → 403; response carries
                    Cache-Control: private, no-store.
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
                        (null for a plain pin, SNOW-499); with the
                        weather_layer flag active, each feature also
                        carries a days property (SNOW-573) — absent when
                        the flag is inactive.
  freshness (SNOW-418) — favourite_card / favourite_list stamp
                        X-Data-Generated-At / -Max-Age / -Unsafe-After;
                        the card's cache_payload / roster_payload
                        json_script sidecars carry the expected shape;
                        the card shows the freshness indicator when a
                        rating exists; the list's rating lookup is
                        batched into one query regardless of favourite
                        count.

The Open-Meteo network call is avoided throughout by patching
``apps.favourites.services.resolve_forecast_point``.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone as django_timezone
from freezegun import freeze_time
from waffle.testutils import override_flag

from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.favourites.models import Favourite
from tests.factories import (
    BulletinFactory,
    FavouriteFactory,
    ForecastPointFactory,
    ForecastPointWeatherFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    ResortFactory,
    UserFactory,
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

    point = ForecastPointFactory.create(latitude=latitude, longitude=longitude)
    with (
        patch("apps.favourites.services.resolve_forecast_point", return_value=point),
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
        with patch("apps.favourites.services.resolve_forecast_point") as mock_resolve:
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
        point = ForecastPointFactory.create()

        with (
            patch(
                "apps.favourites.services.resolve_forecast_point", return_value=point
            ),
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
        assert favourite.forecast_point == point


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

        point = ForecastPointFactory.create(latitude=47.0, longitude=8.0)
        with (
            patch(
                "apps.favourites.services.resolve_forecast_point", return_value=point
            ),
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
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)

        with patch(
            "apps.favourites.services.resolve_forecast_point", return_value=point
        ):
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
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)

        with patch(
            "apps.favourites.services.resolve_forecast_point", return_value=point
        ):
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
        point = ForecastPointFactory.create(latitude=47.0, longitude=8.0)
        with patch(
            "apps.favourites.services.resolve_forecast_point", return_value=point
        ):
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
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)

        with patch(
            "apps.favourites.services.resolve_forecast_point", return_value=point
        ):
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
        point = ForecastPointFactory.create(latitude=47.0, longitude=8.0)
        with patch(
            "apps.favourites.services.resolve_forecast_point", return_value=point
        ):
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

    def test_no_weather_snapshot_shows_coming_soon(self, client: Client) -> None:
        """Without a ForecastPointWeather snapshot, the weather slot shows 'coming soon'."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert "coming soon" in content.lower()

    def test_forecast_panel_renders_day_strip_and_hourly_detail(
        self, client: Client
    ) -> None:
        """With ForecastPointWeather rows, the day strip + hourly detail render (SNOW-417)."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)
        today = django_timezone.localdate()
        ForecastPointWeatherFactory.create(
            forecast_point=favourite.forecast_point,
            valid_for_date=today,
        )
        ForecastPointWeatherFactory.create(
            forecast_point=favourite.forecast_point,
            valid_for_date=today + datetime.timedelta(days=1),
        )

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="favourite-forecast-panel"' in content
        assert 'data-testid="favourite-forecast-day"' in content
        assert 'data-testid="favourite-forecast-hourly"' in content
        assert "coming soon" not in content.lower()
        assert "X-Data-Generated-At" in response

    def test_no_forecast_rows_omits_generated_at_fallback(self, client: Client) -> None:
        """With no ForecastPointWeather rows, freshness headers still stamp (fallback to now)."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)

        response = client.get(_card_url(favourite.uuid), **HTMX_HEADERS)

        assert response.status_code == 200
        assert "X-Data-Generated-At" in response


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

    def test_detail_link_is_htmx_enhanced_onto_the_card_panel(
        self, client: Client
    ) -> None:
        """The manage page's detail link hx-gets the card into the panel.

        SNOW-658 collapsed the "Details" button and the "Open page →" link
        into this one element: an href for the no-JS navigation, an hx-get
        so the card still renders in-page — and so favourites_offline.js
        still sees an HTMX swap to write through (SNOW-418).
        """
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="Mine")

        response = client.get(LIST_URL, **HTMX_HEADERS)

        content = response.content.decode()
        hx_get = f'hx-get="{_card_url(favourite.uuid)}"'
        assert hx_get in content
        assert 'hx-target="#favourite-card-panel"' in content
        assert 'data-testid="favourite-card-panel"' in content
        # The GET is carried by a link, never a button: a GET is a link, a
        # POST is an active control. (The row's own buttons — rename,
        # Remove — are POSTs, so they stay buttons.)
        opener = content[: content.index(hx_get)].rsplit("<", 1)[1]
        assert opener.startswith("a ") or opener.startswith("a\n")

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

    def test_map_variant_keeps_the_detail_link_and_roster_sidecar(
        self, client: Client
    ) -> None:
        """The sheet still links to the detail page and caches for offline."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user, name="Mine")

        response = client.get(f"{LIST_URL}?variant=map", **HTMX_HEADERS)

        content = response.content.decode()
        assert 'data-testid="favourite-list-detail-link"' in content
        assert _detail_url(favourite.uuid) in content
        assert 'id="favourites-roster-cache"' in content

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
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)
        with patch(
            "apps.favourites.services.resolve_forecast_point", return_value=point
        ):
            create_resort_favourite(user, resort)

        response = client.get(GEOJSON_URL)

        data = response.json()
        assert data["features"][0]["properties"]["resort_id"] == resort.pk

    def test_anonymous_gets_403(self, client: Client) -> None:
        """An anonymous GET returns 403."""
        response = client.get(GEOJSON_URL)
        assert response.status_code == 403

    def test_days_absent_when_weather_layer_flag_inactive(self, client: Client) -> None:
        """With the flag off, no feature carries a days key (SNOW-573)."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)
        ForecastPointWeatherFactory.create(
            forecast_point=favourite.forecast_point,
            valid_for_date=datetime.date(2026, 8, 7),
        )

        with override_flag("weather_layer", active=False):
            response = client.get(GEOJSON_URL)

        assert "days" not in response.json()["features"][0]["properties"]

    def test_days_present_when_weather_layer_flag_active(self, client: Client) -> None:
        """With the flag on, days mirrors build_point_weather_days's shape (SNOW-573)."""
        user = UserFactory.create()
        client.force_login(user)
        favourite = FavouriteFactory.create(user=user)
        ForecastPointWeatherFactory.create(
            forecast_point=favourite.forecast_point,
            valid_for_date=datetime.date(2026, 8, 7),
            weather_code=0,  # clear sky
            sunrise=datetime.datetime(2026, 8, 7, 6, 0, tzinfo=datetime.UTC),
            sunset=datetime.datetime(2026, 8, 7, 20, 0, tzinfo=datetime.UTC),
            temperature_2m_max=4.0,
            temperature_2m_min=-3.0,
            snowfall_sum=0.0,
        )

        with (
            freeze_time("2026-08-07T12:00:00Z"),
            override_flag("weather_layer", active=True),
        ):
            response = client.get(GEOJSON_URL)

        days = response.json()["features"][0]["properties"]["days"]
        assert days == {
            "2026-08-07": {
                "icon": "clear-day.svg",
                "label": "Clear",
                "tmax": 4.0,
                "tmin": -3.0,
                "snow": 0.0,
            }
        }

    def test_days_empty_dict_when_no_weather_fetched_yet(self, client: Client) -> None:
        """A favourite with no fetched weather gets an empty days dict, not an error."""
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user)

        with override_flag("weather_layer", active=True):
            response = client.get(GEOJSON_URL)

        assert response.json()["features"][0]["properties"]["days"] == {}

    def test_days_only_covers_the_requesters_own_favourite(
        self, client: Client
    ) -> None:
        """One bulk query still keys weather correctly per favourite (no cross-talk)."""
        user = UserFactory.create()
        client.force_login(user)
        mine = FavouriteFactory.create(user=user)
        other = FavouriteFactory.create(user=UserFactory.create())
        ForecastPointWeatherFactory.create(
            forecast_point=mine.forecast_point,
            valid_for_date=datetime.date(2026, 8, 7),
        )
        ForecastPointWeatherFactory.create(
            forecast_point=other.forecast_point,
            valid_for_date=datetime.date(2026, 8, 7),
        )

        with override_flag("weather_layer", active=True):
            response = client.get(GEOJSON_URL)

        data = response.json()
        assert len(data["features"]) == 1
        assert data["features"][0]["properties"]["uuid"] == str(mine.uuid)
        assert "2026-08-07" in data["features"][0]["properties"]["days"]


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
        assert payload["weather"] is None
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
        assert record["weather"] is None
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
