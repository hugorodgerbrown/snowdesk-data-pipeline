"""
tests/observations/test_views.py — Tests for observations.views.

Covers:
  report_form   — flag off → 404; non-HTMX → 400; non-subscriber → 403;
                  no coords → 200 MANUAL state (GPS gate removed SNOW-330);
                  returns form with region banner;
                  returns form with "couldn't match" when no region;
                  returns form with "choose on map" status when no coords.
  report_submit — flag off → 404; non-HTMX → 400; non-subscriber → 403;
                  missing GPS → 400; missing/invalid location_source → 400;
                  missing/invalid observation_type → 400;
                  valid GPS submit → creates row + returns confirmation;
                  GPS_REFINED submit stores differing gps vs report coords;
                  MANUAL submit with out-of-region pin → region=None (not 400);
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
class TestReportFormNoCoords:
    """Missing or unparseable lat/lon renders the MANUAL path (not a 400).

    SNOW-330 removed the GPS gate from report_form: no coords → form renders
    in "choose on map" state instead of returning 400.
    """

    @override_flag("field_observations", active=True)
    def test_missing_lat_lon_returns_200(self, client: Client) -> None:
        """No lat/lon query params → 200 with the MANUAL form state."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        assert "report-form" in response.content.decode()

    @override_flag("field_observations", active=True)
    def test_missing_lon_returns_200(self, client: Client) -> None:
        """Only lat provided — treated as no valid fix → 200 MANUAL state."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.get(FORM_URL, {"lat": "46.1"}, **HTMX_HEADERS)
        assert response.status_code == 200

    @override_flag("field_observations", active=True)
    def test_unparseable_lat_returns_200(self, client: Client) -> None:
        """Non-float lat — treated as no valid fix → 200 MANUAL state."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.get(
            FORM_URL, {"lat": "not-a-number", "lon": "7.1"}, **HTMX_HEADERS
        )
        assert response.status_code == 200

    @override_flag("field_observations", active=True)
    def test_no_coords_form_shows_manual_status_text(self, client: Client) -> None:
        """Form without coords shows the 'choose on map' status message."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        content = response.content.decode()
        assert "choose a location on the map" in content


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
                {"lat": "46.1", "lon": "7.1", "location_source": "GPS"},
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
                {"lat": "46.0", "lon": "7.7", "location_source": "GPS"},
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
                {"lat": "0.0", "lon": "0.0", "location_source": "GPS"},
                **HTMX_HEADERS,
            )
        assert response.status_code == 200
        assert "couldn" in response.content.decode()

    @override_flag("field_observations", active=True)
    def test_form_contains_problem_buttons(self, client: Client) -> None:
        """Each OBSERVATION_TYPE value appears as a submit button in the form."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.get(
                FORM_URL,
                {"lat": "46.1", "lon": "7.1", "location_source": "GPS"},
                **HTMX_HEADERS,
            )
        content = response.content.decode()
        for value in FieldObservation.OBSERVATION_TYPE.values:
            assert value in content

    @override_flag("field_observations", active=True)
    def test_gps_status_text_with_coords(self, client: Client) -> None:
        """Form with GPS coords shows 'Using current GPS location' status."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.get(
                FORM_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": "GPS",
                    "gps_lat": "46.1",
                    "gps_lon": "7.1",
                },
                **HTMX_HEADERS,
            )
        assert response.status_code == 200
        assert "Using current GPS location" in response.content.decode()


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
class TestReportSubmitLocationSourceGate:
    """Missing or invalid location_source returns 400."""

    @override_flag("field_observations", active=True)
    def test_missing_location_source_returns_400(self, client: Client) -> None:
        """POST with valid GPS but no location_source → 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                },
                **HTMX_HEADERS,
            )
        assert response.status_code == 400

    @override_flag("field_observations", active=True)
    def test_invalid_location_source_returns_400(self, client: Client) -> None:
        """POST with an unknown location_source value → 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": "SATELLITE",
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                },
                **HTMX_HEADERS,
            )
        assert response.status_code == 400


@pytest.mark.django_db
class TestReportSubmitObservationTypeGate:
    """Missing or invalid observation_type returns 400."""

    @override_flag("field_observations", active=True)
    def test_missing_observation_type_returns_400(self, client: Client) -> None:
        """POST with valid GPS but no observation_type → 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                },
                **HTMX_HEADERS,
            )
        assert response.status_code == 400

    @override_flag("field_observations", active=True)
    def test_invalid_observation_type_returns_400(self, client: Client) -> None:
        """POST with an unknown observation_type value → 400."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": "UNKNOWN_TYPE",
                },
                **HTMX_HEADERS,
            )
        assert response.status_code == 400


@pytest.mark.django_db
class TestReportSubmitSuccess:
    """Valid POST creates a FieldObservation and returns confirmation."""

    @override_flag("field_observations", active=True)
    def test_creates_observation_row(self, client: Client) -> None:
        """A valid GPS POST creates exactly one FieldObservation row."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "gps_lat": "46.1",
                    "gps_lon": "7.1",
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        assert FieldObservation.objects.filter(subscriber=subscriber).count() == 1
        obs = FieldObservation.objects.get(subscriber=subscriber)
        assert obs.latitude == 46.1
        assert obs.longitude == 7.1
        assert obs.location_source == FieldObservation.LOCATION_SOURCE.GPS
        assert obs.gps_latitude == 46.1
        assert obs.gps_longitude == 7.1
        assert obs.observation_type == FieldObservation.OBSERVATION_TYPE.WHUMPFING

    @override_flag("field_observations", active=True)
    def test_stores_the_submitted_type(self, client: Client) -> None:
        """The stored observation_type matches what was submitted."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.FRACTURES,
                },
                **HTMX_HEADERS,
            )

        obs = FieldObservation.objects.get(subscriber=subscriber)
        assert obs.observation_type == FieldObservation.OBSERVATION_TYPE.FRACTURES

    @override_flag("field_observations", active=True)
    def test_returns_confirmation_partial(self, client: Client) -> None:
        """Valid POST returns the thank-you confirmation fragment."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                },
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
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        assert "Verbier" in response.content.decode()

    @override_flag("field_observations", active=True)
    def test_multiple_reports_same_day_allowed(self, client: Client) -> None:
        """Multiple reports from the same subscriber on the same day are all created.

        To report two problems the user submits two reports — each with a
        different observation_type.
        """
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        types = [
            FieldObservation.OBSERVATION_TYPE.WHUMPFING,
            FieldObservation.OBSERVATION_TYPE.PINWHEELS,
            FieldObservation.OBSERVATION_TYPE.FRACTURES,
        ]
        for obs_type in types:
            with patch("observations.views.region_for_point", return_value=None):
                resp = client.post(
                    SUBMIT_URL,
                    {
                        "lat": "46.1",
                        "lon": "7.1",
                        "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                        "observation_type": obs_type,
                    },
                    **HTMX_HEADERS,
                )
            assert resp.status_code == 200

        assert FieldObservation.objects.filter(subscriber=subscriber).count() == 3

    @override_flag("field_observations", active=True)
    def test_accuracy_converted_metres_to_km(self, client: Client) -> None:
        """accuracy_m in metres is stored as accuracy_radius_km (divided by 1000)."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                    "accuracy_m": "500",
                },
                **HTMX_HEADERS,
            )

        obs = FieldObservation.objects.get(subscriber=subscriber)
        assert obs.accuracy_radius_km == pytest.approx(0.5)

    @override_flag("field_observations", active=True)
    def test_gps_refined_stores_differing_report_and_gps_coords(
        self, client: Client
    ) -> None:
        """GPS_REFINED submit stores the dragged pin as report coords and the
        original fix in gps_latitude/gps_longitude.
        """
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.15",
                    "lon": "7.15",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS_REFINED,
                    "gps_lat": "46.10",
                    "gps_lon": "7.10",
                    "observation_type": FieldObservation.OBSERVATION_TYPE.PINWHEELS,
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        obs = FieldObservation.objects.get(subscriber=subscriber)
        assert obs.latitude == pytest.approx(46.15)
        assert obs.longitude == pytest.approx(7.15)
        assert obs.location_source == FieldObservation.LOCATION_SOURCE.GPS_REFINED
        assert obs.gps_latitude == pytest.approx(46.10)
        assert obs.gps_longitude == pytest.approx(7.10)

    @override_flag("field_observations", active=True)
    def test_manual_pin_outside_region_creates_row_with_null_region(
        self, client: Client
    ) -> None:
        """A MANUAL submit whose point matches no region creates a row with
        region=None and returns 200 — guards the dropped region-required rule.
        """
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        # region_for_point returns None for a point outside all known boundaries.
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "0.0",
                    "lon": "0.0",
                    "location_source": FieldObservation.LOCATION_SOURCE.MANUAL,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        obs = FieldObservation.objects.get(subscriber=subscriber)
        assert obs.region is None
        assert obs.location_source == FieldObservation.LOCATION_SOURCE.MANUAL
        assert obs.gps_latitude is None
        assert obs.gps_longitude is None

    @override_flag("field_observations", active=True)
    def test_manual_submit_has_null_gps_coords(self, client: Client) -> None:
        """MANUAL path (no GPS fix) stores None for gps_latitude/gps_longitude."""
        subscriber = SubscriberFactory.create()
        client.force_login(subscriber.user)

        with patch("observations.views.region_for_point", return_value=None):
            client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.MANUAL,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.FRACTURES,
                    # No gps_lat / gps_lon — MANUAL path.
                },
                **HTMX_HEADERS,
            )

        obs = FieldObservation.objects.get(subscriber=subscriber)
        assert obs.gps_latitude is None
        assert obs.gps_longitude is None


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
