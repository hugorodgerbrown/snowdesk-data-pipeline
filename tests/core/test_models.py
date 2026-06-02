"""
tests/core/test_models.py — Tests for the core.models.RequestLog concrete model.

Covers factory creation, to_string() format, anonymise() field clearing,
default ordering, and the from_request() manager method (end-to-end capture
with a patched geo_lookup).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import RequestFactory

from bulletins.services.geoip import GeoLookup
from core.models import RequestLog
from tests.factories import RequestLogFactory, SubscriberFactory


@pytest.mark.django_db
class TestRequestLogFactory:
    """Factory creates valid RequestLog instances."""

    def test_create_returns_persisted_instance(self) -> None:
        """RequestLogFactory.create() returns a saved row."""
        log = RequestLogFactory.create()
        assert log.pk is not None
        assert RequestLog.objects.filter(pk=log.pk).exists()

    def test_default_method_is_post(self) -> None:
        """Factory default method is 'POST'."""
        log = RequestLogFactory.create()
        assert log.method == "POST"

    def test_subscriber_defaults_to_none(self) -> None:
        """Factory leaves subscriber null by default."""
        log = RequestLogFactory.create()
        assert log.subscriber is None

    def test_country_code_defaults_to_empty(self) -> None:
        """Factory defaults country_code to empty string."""
        log = RequestLogFactory.create()
        assert log.country_code == ""


@pytest.mark.django_db
class TestRequestLogToString:
    """to_string() produces a predictable human-readable string."""

    def test_format_contains_method_path_ip_country(self) -> None:
        """to_string() includes method, path, ip_address, and country_code."""
        log = RequestLogFactory.create(
            method="GET",
            path="/test-path/",
            ip_address="203.0.113.1",
            country_code="CH",
        )
        result = log.to_string()
        assert "GET" in result
        assert "/test-path/" in result
        assert "203.0.113.1" in result
        assert "CH" in result

    def test_str_delegates_to_to_string(self) -> None:
        """str(log) returns the same value as log.to_string()."""
        log = RequestLogFactory.create()
        assert str(log) == log.to_string()


@pytest.mark.django_db
class TestRequestLogAnonymise:
    """anonymise() nulls PII fields and saves the row."""

    def test_anonymise_nulls_ip_address(self) -> None:
        """ip_address is set to None after anonymise()."""
        log = RequestLogFactory.create(ip_address="203.0.113.1")
        log.anonymise()
        log.refresh_from_db()
        assert log.ip_address is None

    def test_anonymise_clears_session_key(self) -> None:
        """session_key is cleared to '' after anonymise()."""
        log = RequestLogFactory.create(session_key="abc123")
        log.anonymise()
        log.refresh_from_db()
        assert log.session_key == ""

    def test_anonymise_clears_user_agent(self) -> None:
        """user_agent is cleared to '' after anonymise()."""
        log = RequestLogFactory.create(user_agent="Mozilla/5.0")
        log.anonymise()
        log.refresh_from_db()
        assert log.user_agent == ""

    def test_anonymise_clears_referer(self) -> None:
        """referer is cleared to '' after anonymise()."""
        log = RequestLogFactory.create(referer="https://example.com/")
        log.anonymise()
        log.refresh_from_db()
        assert log.referer == ""

    def test_anonymise_nulls_latitude_longitude(self) -> None:
        """latitude and longitude are nulled after anonymise()."""
        log = RequestLogFactory.create(latitude=46.2, longitude=7.4)
        log.anonymise()
        log.refresh_from_db()
        assert log.latitude is None
        assert log.longitude is None

    def test_anonymise_retains_country_and_city(self) -> None:
        """country_code and city are retained (not PII by themselves)."""
        log = RequestLogFactory.create(country_code="CH", city="Sion")
        log.anonymise()
        log.refresh_from_db()
        assert log.country_code == "CH"
        assert log.city == "Sion"


@pytest.mark.django_db
class TestRequestLogOrdering:
    """RequestLog rows are ordered by -created_at."""

    def test_ordering_newest_first(self) -> None:
        """Queryset ordering is descending by created_at."""
        log1 = RequestLogFactory.create()
        log2 = RequestLogFactory.create()
        ids = list(RequestLog.objects.values_list("pk", flat=True))
        # log2 was created after log1, so it should appear first.
        assert ids.index(log2.pk) < ids.index(log1.pk)


@pytest.mark.django_db
class TestRequestLogFromRequest:
    """RequestLog.objects.from_request() captures all fields in one call."""

    @pytest.fixture()
    def rf(self) -> RequestFactory:
        """Return a Django RequestFactory."""
        return RequestFactory()

    def test_from_request_captures_ip_and_ua(self, rf: RequestFactory) -> None:
        """from_request() populates ip_address and user_agent."""
        request = rf.post(
            "/",
            REMOTE_ADDR="203.0.113.5",
            HTTP_USER_AGENT="TestAgent/1.0",
        )
        request.session = type("S", (), {"session_key": "x"})()
        anon = type("U", (), {"is_authenticated": False})()
        request.user = anon  # noqa: PGH003 — synthetic minimal user object

        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = RequestLog.objects.from_request(request)

        assert str(log.ip_address) == "203.0.113.5"
        assert log.user_agent == "TestAgent/1.0"

    def test_from_request_links_authenticated_subscriber(
        self, rf: RequestFactory
    ) -> None:
        """Authenticated requests link the log to request.user."""
        subscriber = SubscriberFactory.create()
        request = rf.post("/", REMOTE_ADDR="203.0.113.1")
        request.session = type("S", (), {"session_key": "y"})()
        request.user = subscriber  # noqa: PGH003 — Subscriber is a valid auth user

        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = RequestLog.objects.from_request(request)

        assert log.subscriber_id == subscriber.pk

    def test_from_request_with_geo(self, rf: RequestFactory) -> None:
        """from_request() maps GeoLookup fields onto the RequestLog."""
        geo = GeoLookup(
            country="FR",
            subdivision="73",
            city="Grenoble",
            latitude=45.18,
            longitude=5.72,
            accuracy_radius_km=20,
        )
        request = rf.post("/", REMOTE_ADDR="185.0.1.1")
        request.session = type("S", (), {"session_key": "z"})()
        anon = type("U", (), {"is_authenticated": False})()
        request.user = anon  # noqa: PGH003 — synthetic minimal user object

        with patch("bulletins.services.geoip.geo_lookup", return_value=geo):
            log = RequestLog.objects.from_request(request)

        assert log.country_code == "FR"
        assert log.city == "Grenoble"
        assert log.latitude == pytest.approx(45.18)
