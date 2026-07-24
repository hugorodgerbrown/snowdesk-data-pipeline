"""
tests/observations/test_views.py — Tests for observations.views.

Covers:
  report_form   — flag off → 404; non-HTMX → 400; anonymous → 403;
                  authenticated non-staff user → 200;
                  no coords → 200 MANUAL state (GPS gate removed SNOW-330);
                  returns form with region banner;
                  returns form with "couldn't match" when no region;
                  returns form with "choose on map" status when no coords.
  report_submit — flag off → 404; non-HTMX → 400; anonymous → 403;
                  authenticated non-staff user → 200 + creates row;
                  missing GPS → 400; missing/invalid location_source → 400;
                  missing/invalid observation_type → 400;
                  valid GPS submit → creates row + returns confirmation;
                  GPS_REFINED submit stores differing gps vs report coords;
                  MANUAL submit with out-of-region pin → region=None (not 400);
                  rate-limit → 429; multiple reports same day allowed;
                  observed_at (SNOW-420) — valid client instant is stored;
                  absent → falls back to timezone.now default; malformed,
                  too-far-future, or too-old → 400 with no row created.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone
from waffle.testutils import override_flag

from observations.models import FieldObservation
from tests.factories import (
    AccountFactory,
    MicroRegionFactory,
    UserFactory,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FORM_URL = "/partials/report/form/"
SUBMIT_URL = "/partials/report/"

# Type annotation as dict[str, Any] matches the **extra kwargs expected by
# Django's test Client.get/post — same pattern as tests/accounts/test_views.py.
HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


def _verified_user(**kwargs: Any) -> User:
    """Create a User with a verified Account (SNOW-430 gate passes).

    Field reports require a verified email address, so most report tests need a
    user whose ``Account.is_verified`` is True. Extra kwargs forward to
    ``UserFactory`` (e.g. ``is_staff=False``).
    """
    user = UserFactory.create(**kwargs)
    AccountFactory.create(user=user, is_verified=True)
    return user


# ---------------------------------------------------------------------------
# report_form — GET /partials/report/form/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReportFormFlagGate:
    """Flag-off → 404; anonymous → 403."""

    @override_flag("field_observations", active=False)
    def test_flag_off_returns_404(self, client: Client) -> None:
        """When field_observations flag is inactive, GET returns 404."""
        user = _verified_user()
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 404

    @override_flag("field_observations", active=True)
    def test_flag_on_anonymous_gets_403(self, client: Client) -> None:
        """Anonymous users are rejected with 403."""
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 403

    @override_flag("field_observations", active=True)
    def test_authenticated_non_staff_user_gets_200(self, client: Client) -> None:
        """A non-staff authenticated user gets 200 (SNOW-333)."""
        user = _verified_user(is_staff=False)
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 200


@pytest.mark.django_db
class TestReportFormHtmxGate:
    """Non-HTMX requests are rejected with 400."""

    @override_flag("field_observations", active=True)
    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A plain GET without HX-Request returns 400."""
        user = _verified_user()
        client.force_login(user)
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
        user = _verified_user()
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        assert "report-form" in response.content.decode()

    @override_flag("field_observations", active=True)
    def test_missing_lon_returns_200(self, client: Client) -> None:
        """Only lat provided — treated as no valid fix → 200 MANUAL state."""
        user = _verified_user()
        client.force_login(user)
        response = client.get(FORM_URL, {"lat": "46.1"}, **HTMX_HEADERS)
        assert response.status_code == 200

    @override_flag("field_observations", active=True)
    def test_unparseable_lat_returns_200(self, client: Client) -> None:
        """Non-float lat — treated as no valid fix → 200 MANUAL state."""
        user = _verified_user()
        client.force_login(user)
        response = client.get(
            FORM_URL, {"lat": "not-a-number", "lon": "7.1"}, **HTMX_HEADERS
        )
        assert response.status_code == 200

    @override_flag("field_observations", active=True)
    def test_no_coords_form_shows_manual_status_text(self, client: Client) -> None:
        """Form without coords shows the 'choose on map' status message."""
        user = _verified_user()
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        content = response.content.decode()
        assert "move the map to set your location" in content


@pytest.mark.django_db
class TestReportFormManualInputsPresent:
    """MANUAL-state form always contains lat and lon hidden inputs.

    Regression test for BLOCKER 1 (SNOW-330 review): the inputs must be
    present unconditionally so that the JS ``writeFormCoords`` call can
    populate them after a MANUAL map-click, allowing the POST to carry lat/lon.
    """

    @override_flag("field_observations", active=True)
    def test_manual_form_contains_lat_input(self, client: Client) -> None:
        """Form rendered without coords (MANUAL state) contains name="lat"."""
        user = _verified_user()
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        assert 'name="lat"' in response.content.decode()

    @override_flag("field_observations", active=True)
    def test_manual_form_contains_lon_input(self, client: Client) -> None:
        """Form rendered without coords (MANUAL state) contains name="lon"."""
        user = _verified_user()
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        assert 'name="lon"' in response.content.decode()

    @override_flag("field_observations", active=True)
    def test_manual_form_lat_input_is_empty(self, client: Client) -> None:
        """lat hidden input has an empty value when no coords are passed."""
        user = _verified_user()
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="lat" value=""' in content

    @override_flag("field_observations", active=True)
    def test_manual_form_lon_input_is_empty(self, client: Client) -> None:
        """lon hidden input has an empty value when no coords are passed."""
        user = _verified_user()
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="lon" value=""' in content


@pytest.mark.django_db
class TestReportFormSuccess:
    """Successful GET returns the report form partial."""

    @override_flag("field_observations", active=True)
    def test_returns_200_with_form(self, client: Client) -> None:
        """Valid lat/lon with flag active + authenticated user returns 200."""
        user = _verified_user()
        client.force_login(user)
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
        user = _verified_user()
        client.force_login(user)

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
        user = _verified_user()
        client.force_login(user)

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
        user = _verified_user()
        client.force_login(user)

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
        user = _verified_user()
        client.force_login(user)

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
    """Flag-off → 404; anonymous → 403."""

    @override_flag("field_observations", active=False)
    def test_flag_off_returns_404(self, client: Client) -> None:
        """POST with flag inactive returns 404."""
        user = _verified_user()
        client.force_login(user)
        response = client.post(
            SUBMIT_URL,
            {"lat": "46.1", "lon": "7.1"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 404

    @override_flag("field_observations", active=True)
    def test_anonymous_gets_403(self, client: Client) -> None:
        """Anonymous POST with flag active gets 403."""
        response = client.post(
            SUBMIT_URL,
            {"lat": "46.1", "lon": "7.1"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestReportVerificationGate:
    """SNOW-430: an authenticated but unverified user is rejected with 403.

    Field reports require a verified email address — an authenticated user
    with no ``Account`` row, or an unverified one, cannot submit or open the
    form.
    """

    @override_flag("field_observations", active=True)
    def test_form_no_account_gets_403(self, client: Client) -> None:
        """Authenticated user with no Account profile → 403 on the form."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 403

    @override_flag("field_observations", active=True)
    def test_form_unverified_account_gets_403(self, client: Client) -> None:
        """Authenticated user with an unverified Account → 403 on the form."""
        account = AccountFactory.create(is_verified=False)
        client.force_login(account.user)
        response = client.get(FORM_URL, **HTMX_HEADERS)
        assert response.status_code == 403

    @override_flag("field_observations", active=True)
    def test_submit_no_account_gets_403(self, client: Client) -> None:
        """Authenticated user with no Account profile → 403 on submit."""
        user = UserFactory.create()
        client.force_login(user)
        response = client.post(
            SUBMIT_URL,
            {"lat": "46.1", "lon": "7.1"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 403

    @override_flag("field_observations", active=True)
    def test_submit_unverified_account_gets_403(self, client: Client) -> None:
        """Authenticated user with an unverified Account → 403 on submit."""
        account = AccountFactory.create(is_verified=False)
        client.force_login(account.user)
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
        assert response.status_code == 403

    @override_flag("field_observations", active=True)
    def test_submit_unverified_creates_no_row(self, client: Client) -> None:
        """A rejected unverified submit must not create a FieldObservation."""
        account = AccountFactory.create(is_verified=False)
        client.force_login(account.user)
        client.post(
            SUBMIT_URL,
            {
                "lat": "46.1",
                "lon": "7.1",
                "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
            },
            **HTMX_HEADERS,
        )
        assert FieldObservation.objects.filter(user=account.user).count() == 0


@pytest.mark.django_db
class TestReportSubmitHtmxGate:
    """Non-HTMX POST returns 400."""

    @override_flag("field_observations", active=True)
    def test_non_htmx_returns_400(self, client: Client) -> None:
        """POST without HX-Request returns 400."""
        user = _verified_user()
        client.force_login(user)
        response = client.post(SUBMIT_URL, {"lat": "46.1", "lon": "7.1"})
        assert response.status_code == 400


@pytest.mark.django_db
class TestReportSubmitGpsGate:
    """Missing or bad lat/lon returns 400."""

    @override_flag("field_observations", active=True)
    def test_missing_gps_returns_400(self, client: Client) -> None:
        """POST with no lat/lon → 400."""
        user = _verified_user()
        client.force_login(user)
        response = client.post(SUBMIT_URL, {}, **HTMX_HEADERS)
        assert response.status_code == 400

    @override_flag("field_observations", active=True)
    def test_unparseable_lon_returns_400(self, client: Client) -> None:
        """Non-float lon → 400."""
        user = _verified_user()
        client.force_login(user)
        response = client.post(
            SUBMIT_URL,
            {"lat": "46.1", "lon": "bad"},
            **HTMX_HEADERS,
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestReportSubmitInvalidCoordinates:
    """SNOW-464: non-finite / out-of-range coordinates are rejected with 400."""

    @pytest.mark.parametrize(
        ("lat", "lon"),
        [
            ("nan", "7.1"),
            ("46.1", "inf"),
            ("46.1", "-inf"),
            ("95", "7.1"),  # latitude > 90
            ("-95", "7.1"),  # latitude < -90
            ("46.1", "200"),  # longitude > 180
            ("46.1", "-200"),  # longitude < -180
        ],
    )
    @override_flag("field_observations", active=True)
    def test_invalid_coordinates_return_400_and_create_no_row(
        self, client: Client, lat: str, lon: str
    ) -> None:
        """A non-finite or out-of-range lat/lon is rejected before any write."""
        user = _verified_user()
        client.force_login(user)
        with patch("observations.views.region_for_point") as mock_region:
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": lat,
                    "lon": lon,
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                },
                **HTMX_HEADERS,
            )
        assert response.status_code == 400
        assert FieldObservation.objects.count() == 0
        # The region lookup runs only after coordinates parse — never reached.
        mock_region.assert_not_called()

    @override_flag("field_observations", active=True)
    def test_negative_accuracy_is_dropped_not_persisted(self, client: Client) -> None:
        """A present-but-invalid accuracy is dropped to None, never stored.

        Accuracy is optional, so — like an unparseable value — a negative or
        non-finite accuracy is discarded rather than 400'd; the row is created
        with ``accuracy_radius_km=None``, so no invalid value is persisted.
        """
        user = _verified_user()
        client.force_login(user)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "accuracy_m": "-5",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                },
                **HTMX_HEADERS,
            )
        assert response.status_code == 200
        obs = FieldObservation.objects.get(user=user)
        assert obs.accuracy_radius_km is None


@pytest.mark.django_db
class TestReportSubmitLocationSourceGate:
    """Missing or invalid location_source returns 400."""

    @override_flag("field_observations", active=True)
    def test_missing_location_source_returns_400(self, client: Client) -> None:
        """POST with valid GPS but no location_source → 400."""
        user = _verified_user()
        client.force_login(user)
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
        user = _verified_user()
        client.force_login(user)
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
        user = _verified_user()
        client.force_login(user)
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
        user = _verified_user()
        client.force_login(user)
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
        user = _verified_user()
        client.force_login(user)

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
        assert FieldObservation.objects.filter(user=user).count() == 1
        obs = FieldObservation.objects.get(user=user)
        assert obs.latitude == 46.1
        assert obs.longitude == 7.1
        assert obs.location_source == FieldObservation.LOCATION_SOURCE.GPS
        assert obs.gps_latitude == 46.1
        assert obs.gps_longitude == 7.1
        assert obs.observation_type == FieldObservation.OBSERVATION_TYPE.WHUMPFING

    @override_flag("field_observations", active=True)
    def test_authenticated_non_staff_user_creates_row(self, client: Client) -> None:
        """A non-staff authenticated user can submit (SNOW-333)."""
        user = _verified_user(is_staff=False)
        client.force_login(user)

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
        obs = FieldObservation.objects.get(user=user)
        assert obs.user == user

    @override_flag("field_observations", active=True)
    def test_stores_the_submitted_type(self, client: Client) -> None:
        """The stored observation_type matches what was submitted."""
        user = _verified_user()
        client.force_login(user)

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

        obs = FieldObservation.objects.get(user=user)
        assert obs.observation_type == FieldObservation.OBSERVATION_TYPE.FRACTURES

    @override_flag("field_observations", active=True)
    def test_returns_confirmation_partial(self, client: Client) -> None:
        """Valid POST returns the thank-you confirmation fragment."""
        user = _verified_user()
        client.force_login(user)

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
        user = _verified_user()
        client.force_login(user)

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
        """Multiple reports from the same user on the same day are all created.

        To report two problems the user submits two reports — each with a
        different observation_type.
        """
        user = _verified_user()
        client.force_login(user)

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

        assert FieldObservation.objects.filter(user=user).count() == 3

    @override_flag("field_observations", active=True)
    def test_accuracy_converted_metres_to_km(self, client: Client) -> None:
        """accuracy_m in metres is stored as accuracy_radius_km (divided by 1000)."""
        user = _verified_user()
        client.force_login(user)

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

        obs = FieldObservation.objects.get(user=user)
        assert obs.accuracy_radius_km == pytest.approx(0.5)

    @override_flag("field_observations", active=True)
    def test_gps_refined_stores_differing_report_and_gps_coords(
        self, client: Client
    ) -> None:
        """GPS_REFINED submit stores the dragged pin as report coords and the
        original fix in gps_latitude/gps_longitude.
        """
        user = _verified_user()
        client.force_login(user)

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
        obs = FieldObservation.objects.get(user=user)
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
        user = _verified_user()
        client.force_login(user)

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
        obs = FieldObservation.objects.get(user=user)
        assert obs.region is None
        assert obs.location_source == FieldObservation.LOCATION_SOURCE.MANUAL
        assert obs.gps_latitude is None
        assert obs.gps_longitude is None

    @override_flag("field_observations", active=True)
    def test_manual_submit_has_null_gps_coords(self, client: Client) -> None:
        """MANUAL path (no GPS fix) stores None for gps_latitude/gps_longitude."""
        user = _verified_user()
        client.force_login(user)

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

        obs = FieldObservation.objects.get(user=user)
        assert obs.gps_latitude is None
        assert obs.gps_longitude is None


@pytest.mark.django_db
class TestReportSubmitObservedAt:
    """SNOW-420: client-supplied observed_at is validated and stored.

    ``report.js`` stamps ``observed_at`` with the tap-time instant before
    handing the request to the offline mutation queue, so it may reach the
    server well after the fact on replay.
    """

    @override_flag("field_observations", active=True)
    def test_valid_client_observed_at_is_stored(self, client: Client) -> None:
        """A valid ISO 8601 instant a few hours in the past is stored verbatim."""
        user = _verified_user()
        client.force_login(user)

        observed_at = timezone.now() - timedelta(hours=3)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                    "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        obs = FieldObservation.objects.get(user=user)
        assert obs.observed_at == observed_at

    @override_flag("field_observations", active=True)
    def test_absent_observed_at_falls_back_to_now(self, client: Client) -> None:
        """No observed_at in the POST → the model default (timezone.now) fires."""
        user = _verified_user()
        client.force_login(user)

        before = timezone.now()
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
        after = timezone.now()

        assert response.status_code == 200
        obs = FieldObservation.objects.get(user=user)
        assert before <= obs.observed_at <= after

    @override_flag("field_observations", active=True)
    def test_naive_observed_at_is_made_aware(self, client: Client) -> None:
        """A naive (no offset) observed_at value is accepted and made aware."""
        user = _verified_user()
        client.force_login(user)

        naive_recent = (timezone.now() - timedelta(hours=1)).replace(tzinfo=None)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                    "observed_at": naive_recent.isoformat(),
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 200
        obs = FieldObservation.objects.get(user=user)
        assert timezone.is_aware(obs.observed_at)

    @override_flag("field_observations", active=True)
    def test_malformed_observed_at_returns_400_and_creates_no_row(
        self, client: Client
    ) -> None:
        """An unparseable observed_at value → 400, no FieldObservation created."""
        user = _verified_user()
        client.force_login(user)

        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                    "observed_at": "not-a-date",
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 400
        assert FieldObservation.objects.filter(user=user).count() == 0

    @override_flag("field_observations", active=True)
    def test_future_observed_at_beyond_tolerance_returns_400(
        self, client: Client
    ) -> None:
        """observed_at more than the future-tolerance window ahead → 400, no row."""
        user = _verified_user()
        client.force_login(user)

        future = timezone.now() + timedelta(days=1)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                    "observed_at": future.isoformat().replace("+00:00", "Z"),
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 400
        assert FieldObservation.objects.filter(user=user).count() == 0

    @override_flag("field_observations", active=True)
    def test_too_old_observed_at_returns_400(self, client: Client) -> None:
        """observed_at older than the accepted window (30 days) → 400, no row."""
        user = _verified_user()
        client.force_login(user)

        too_old = timezone.now() - timedelta(days=60)
        with patch("observations.views.region_for_point", return_value=None):
            response = client.post(
                SUBMIT_URL,
                {
                    "lat": "46.1",
                    "lon": "7.1",
                    "location_source": FieldObservation.LOCATION_SOURCE.GPS,
                    "observation_type": FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                    "observed_at": too_old.isoformat().replace("+00:00", "Z"),
                },
                **HTMX_HEADERS,
            )

        assert response.status_code == 400
        assert FieldObservation.objects.filter(user=user).count() == 0


@pytest.mark.django_db
class TestReportSubmitRateLimit:
    """Rate limit returns 429 when exceeded."""

    @override_flag("field_observations", active=True)
    def test_rate_limited_branch_returns_429(self) -> None:
        """When request.limited is True (set by ratelimit decorator), view returns 429.

        We test the rate-limit branch directly by calling the view with a
        request that has ``limited=True`` pre-set, bypassing decorator
        machinery while still exercising the view's own branch.
        """
        user = _verified_user()

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
        request.user = user
        request.session = SessionStore()

        # Apply HTMX middleware so request.htmx is populated.
        # The middleware mutates request.htmx in-place on __call__; the
        # get_response callable is never invoked in this code path so the
        # return type is irrelevant — cast satisfies mypy.
        from django.http import HttpResponse as _HR  # noqa: PLC0415

        htmx_mw = HtmxMiddleware(lambda _: _HR())
        htmx_mw(request)

        # Exercise the 429 branch by patching the gate helper to pass.
        with patch(
            "observations.views._require_field_observations_flag", return_value=None
        ):
            from observations.views import report_submit  # noqa: PLC0415

            resp = report_submit(request)
            assert resp.status_code == 429
