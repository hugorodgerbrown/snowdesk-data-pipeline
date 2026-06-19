"""
tests/observations/test_views.py — Tests for observations.views.

Covers:
  report_form   — flag off → 404; non-HTMX → 400; non-subscriber → 403;
                  missing GPS → 400; returns form with region banner;
                  returns form with "couldn't match" when no region.
  report_submit — flag off → 404; non-HTMX → 400; non-subscriber → 403;
                  missing GPS → 400; creates row + returns confirmation;
                  rate-limit → 429; multiple reports same day allowed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from waffle.testutils import override_flag

from observations.models import FieldObservation
from tests.factories import (
    MicroRegionFactory,
    SubscriberFactory,
    UserFactory,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FORM_URL = "/partials/report/form/"
SUBMIT_URL = "/partials/report/"

# Type annotation as dict[str, Any] matches the **extra kwargs expected by
# Django's test Client.get/post — same pattern as tests/subscriptions/test_views.py.
HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


# ---------------------------------------------------------------------------
# report_form — GET /partials/report/form/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReportFormFlagGate:
    """Flag-off → 404; non-subscriber → 403."""

    @override_flag("field_observations", active=False)
    def test_flag_off_returns_404(self, client: Client) -> None:
        """When field_observations flag is inactive, GET returns 404."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 404

    @override_flag("field_observations", active=True)
    def test_flag_on_anonymous_gets_4xx(self, client: Client) -> None:
        """Anonymous users are rejected (subscriber gate or flag evaluates False).

        Waffle may return False for anonymous requests without a session;
        either 403 or 404 is correct — the user is blocked.
        """
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code in (403, 404)

    @override_flag("field_observations", active=True)
    def test_flag_on_no_subscriber_profile_returns_403(self, client: Client) -> None:
        """Staff user without Subscriber profile gets 403."""
        user = UserFactory.create(is_staff=True)
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 403


@pytest.mark.django_db
class TestReportFormHtmxGate:
    """Non-HTMX requests are rejected with 400."""

    @override_flag("field_observations", active=True)
    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain GET without HX-Request returns 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        # No HTMX_HEADERS — plain request.
        response = client.get(FORM_URL)
        assert response.status_code == 400


@pytest.mark.django_db
class TestReportFormGpsGate:
    """Missing or unparseable lat/lon returns 400."""

    @override_flag("field_observations", active=True)
    def test_missing_lat_lon_returns_400(self, client: Client) -> None:
        """No lat/lon query params → 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 400

    @override_flag("field_observations", active=True)
    def test_missing_lon_returns_400(self, client: Client) -> None:
        """Only lat provided → 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.get(FORM_URL, {"lat": "46.1"}, **HTMX_HEADERS)
        assert response.status_code == 400

    @override_flag("field_observations", active=True)
    def test_unparseable_lat_returns_400(self, client: Client) -> None:
        """Non-float lat → 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.get(
            FORM_URL, {"lat": "not-a-number", "lon": "7.1"}, **HTMX_HEADERS
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestReportFormSuccess:
    """Successful GET returns the report form partial."""

    @override_flag("field_observations", active=True)
    def test_returns_200_with_form(self, client: Client) -> None:
        """Valid lat/lon with flag active + subscriber returns 200."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.get(
                FORM_URL,
                {"lat": "46.1", "lon": "7.1"},
                **HTMX_HEADERS,
            )
        assert response.status_code == 200
        content = response.content.decode()
        assert "report-form" in content

    @override_flag("field_observations", active=True)
    def test_returns_region_banner_when_matched(self, client: Client) -> None:
        """Region banner appears when region_for_point returns a region."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        fake_region = MicroRegionFactory.create(name="Zermatt-Saas")
        with patch("observations.views.region_for_point", return_value=fake_region):
            response = client.get(
                FORM_URL,
                {"lat": "46.0", "lon": "7.7"},
                **HTMX_HEADERS,
            )
        assert response.status_code == 200
        assert "Zermatt-Saas" in response.content.decode()

    @override_flag("field_observations", active=True)
    def test_returns_fallback_banner_when_no_region(self, client: Client) -> None:
        """'couldn't match' text appears when region_for_point returns None."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.get(
                FORM_URL,
                {"lat": "0.0", "lon": "0.0"},
                **HTMX_HEADERS,
            )
        assert response.status_code == 200
        assert "couldn" in response.content.decode()


# ---------------------------------------------------------------------------
# report_submit — POST /partials/report/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReportSubmitFlagGate:
    """Flag-off → 404; non-subscriber → 403."""

    @override_flag("field_observations", active=False)
    def test_flag_off_returns_404(self, client: Client) -> None:
        """POST with flag inactive returns 404."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.post(
            SUBMIT_URL,
            {"lat": "46.1", "lon": "7.1"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 404

    @override_flag("field_observations", active=True)
    def test_anonymous_gets_4xx(self, client: Client) -> None:
        """Anonymous POST with flag active gets rejected."""
        response = client.post(
            SUBMIT_URL,
            {"lat": "46.1", "lon": "7.1"},
            **HTMX_HEADERS,
        )
        assert response.status_code in (403, 404)


@pytest.mark.django_db
class TestReportSubmitHtmxGate:
    """Non-HTMX POST returns 400."""

    @override_flag("field_observations", active=True)
    def test_non_htmx_returns_400(self, client: Client) -> None:
        """POST without HX-Request returns 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.post(SUBMIT_URL, {"lat": "46.1", "lon": "7.1"})
        assert response.status_code == 400


@pytest.mark.django_db
class TestReportSubmitGpsGate:
    """Missing or bad lat/lon returns 400."""

    @override_flag("field_observations", active=True)
    def test_missing_gps_returns_400(self, client: Client) -> None:
        """POST with no lat/lon → 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.post(SUBMIT_URL, {}, **HTMX_HEADERS)
        assert response.status_code == 400

    @override_flag("field_observations", active=True)
    def test_unparseable_lon_returns_400(self, client: Client) -> None:
        """Non-float lon → 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.post(
            SUBMIT_URL,
            {"lat": "46.1", "lon": "bad"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestReportSubmitSuccess:
    """Valid POST creates a FieldObservation and returns confirmation."""

    @override_flag("field_observations", active=True)
    def test_creates_observation_row(self, client: Client) -> None:
        """A valid POST creates exactly one FieldObservation row."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "observation_types": [FieldObservation.OBSERVATION_TYPE.WHUMPFING],
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        assert FieldObservation.objects.filter(subscriber=subscriber).count() == 1
        obs = FieldObservation.objects.get(subscriber=subscriber)
        assert obs.latitude == 46.1
        assert obs.longitude == 7.1
        assert FieldObservation.OBSERVATION_TYPE.WHUMPFING in obs.observation_types

    @override_flag("field_observations", active=True)
    def test_returns_confirmation_partial(self, client: Client) -> None:
        """Valid POST returns the thank-you confirmation fragment."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {"lat": "46.1", "lon": "7.1"},
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        assert "Thank you" in response.content.decode()

    @override_flag("field_observations", active=True)
    def test_confirmation_shows_region_name(self, client: Client) -> None:
        """Confirmation fragment includes region name when matched."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        region = MicroRegionFactory.create(name="Verbier")
        with patch("observations.views.region_for_point", return_value=region):
            response = client.post(
                SUBMIT_URL,
                {"lat": "46.1", "lon": "7.1"},
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        assert "Verbier" in response.content.decode()

    @override_flag("field_observations", active=True)
    def test_multiple_reports_same_day_allowed(self, client: Client) -> None:
        """Multiple reports from the same subscriber on the same day are all created."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        for _ in range(3):
            with patch("observations.views.region_for_point", return_value=None):
                resp = client.post(
                    SUBMIT_URL,
                    {"lat": "46.1", "lon": "7.1"},
                    **HTMX_HEADERS,
                )
            assert resp.status_code == 200

        assert FieldObservation.objects.filter(subscriber=subscriber).count() == 3

    @override_flag("field_observations", active=True)
    def test_unknown_observation_types_are_filtered(self, client: Client) -> None:
        """POST with unknown observation_type values silently drops them."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "observation_types": [
                        "UNKNOWN_TYPE",
                        FieldObservation.OBSERVATION_TYPE.PINWHEELS,
                    ],
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        obs = FieldObservation.objects.get(subscriber=subscriber)
        assert "UNKNOWN_TYPE" not in obs.observation_types
        assert FieldObservation.OBSERVATION_TYPE.PINWHEELS in obs.observation_types

    @override_flag("field_observations", active=True)
    def test_accuracy_converted_metres_to_km(self, client: Client) -> None:
        """accuracy_m in metres is stored as accuracy_radius_km (divided by 1000)."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            client.post(
                SUBMIT_URL,
                {"lat": "46.1", "lon": "7.1", "accuracy_m": "500"},
                **HTMX_HEADERS,
            )

        obs = FieldObservation.objects.get(subscriber=subscriber)
        assert obs.accuracy_radius_km == pytest.approx(0.5)


@pytest.mark.django_db
class TestReportSubmitRateLimit:
    """Rate limit returns 429 when exceeded."""

    @override_flag("field_observations", active=True)
    def test_rate_limited_branch_returns_429(self, client: Client) -> None:
        """When request.limited is True (set by ratelimit decorator), view returns 429.

        We test the rate-limit branch directly by calling the view with a
        request that has ``limited=True`` pre-set, bypassing decorator
        machinery while still exercising the view's own branch.
        """
        subscriber = SubscriberFactory.create()

        from django.contrib.sessions.backends.db import SessionStore  # noqa: PLC0415
        from django.test import RequestFactory  # noqa: PLC0415
        from django_htmx.middleware import HtmxMiddleware  # noqa: PLC0415

        rf = RequestFactory()
        request = rf.post(
            SUBMIT_URL,
            {"lat": "46.1", "lon": "7.1"},
            HTTP_HX_REQUEST="true",
        )
        request.limited = True  # type: ignore[attr-defined]
        request.user = subscriber.user
        request.session = SessionStore()

        # Apply HTMX middleware so request.htmx is populated.
        # The middleware mutates request.htmx in-place on __call__; the
        # get_response callable is never invoked in this code path so the
        # return type is irrelevant — cast satisfies mypy.
        from django.http import HttpResponse as _HR  # noqa: PLC0415

        htmx_mw = HtmxMiddleware(lambda r: _HR())
        htmx_mw(request)

        # Exercise the 429 branch by patching the gate helpers to pass.
        with patch(
            "observations.views._require_field_observations_flag", return_value=None
        ):
            with patch("observations.views._get_subscriber", return_value=subscriber):
                from observations.views import report_submit  # noqa: PLC0415

                resp = report_submit(request)
                assert resp.status_code == 429
