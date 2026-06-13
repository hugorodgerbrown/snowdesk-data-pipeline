"""
tests/core/services/test_request_log.py — Tests for core.services.request_log.capture.

Exercises the end-to-end capture path: builds a synthetic request via
RequestFactory, patches geo_lookup to return a deterministic GeoLookup, and
asserts that the returned RequestLog row has all fields correctly populated.

Also verifies that anonymous (unauthenticated) requests leave subscriber=None
and that authenticated requests link to request.user.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from bulletins.services.geoip import GeoLookup
from core.models import RequestLog
from core.services.request_log import capture
from tests.factories import SubscriberFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_GEO = GeoLookup(
    country="CH",
    subdivision="VS",
    city="Sion",
    latitude=46.23,
    longitude=7.36,
    accuracy_radius_km=50,
)


def _make_request(
    rf: RequestFactory,
    *,
    ip: str = "203.0.113.1",
    ua: str = "Mozilla/5.0 (Test)",
    accept_language: str = "en-GB,en;q=0.9",
    referer: str = "https://example.com/page",
    user: object = None,
) -> MagicMock:
    """Build a minimal synthetic request with the given metadata."""
    request = rf.post(
        "/subscribe/",
        HTTP_USER_AGENT=ua,
        HTTP_ACCEPT_LANGUAGE=accept_language,
        HTTP_REFERER=referer,
        REMOTE_ADDR=ip,
    )
    if user is not None:
        request.user = user  # type: ignore[assignment]  # test helper accepts any user-like
    else:
        anon = MagicMock()
        anon.is_authenticated = False
        request.user = anon
    # Ensure session_key is available.
    request.session = MagicMock()
    request.session.session_key = "abc123"
    return request  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCapture:
    """Tests for capture(request)."""

    @pytest.fixture()
    def rf(self) -> RequestFactory:
        """Return a Django RequestFactory."""
        return RequestFactory()

    def test_creates_one_row_per_call(self, rf: RequestFactory) -> None:
        """capture() creates exactly one RequestLog row per invocation."""
        request = _make_request(rf)
        with patch("bulletins.services.geoip.geo_lookup", return_value=_FAKE_GEO):
            capture(request)
            capture(request)
        assert RequestLog.objects.count() == 2

    def test_returns_saved_instance(self, rf: RequestFactory) -> None:
        """capture() returns the newly saved RequestLog instance."""
        request = _make_request(rf)
        with patch("bulletins.services.geoip.geo_lookup", return_value=_FAKE_GEO):
            log = capture(request)
        assert log.pk is not None
        assert RequestLog.objects.filter(pk=log.pk).exists()

    def test_ip_captured_from_remote_addr(self, rf: RequestFactory) -> None:
        """capture() stores REMOTE_ADDR as ip_address when no XFF."""
        request = _make_request(rf, ip="203.0.113.7")
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert str(log.ip_address) == "203.0.113.7"

    def test_ip_captured_from_xff(self, rf: RequestFactory) -> None:
        """capture() prefers the leftmost X-Forwarded-For IP."""
        request = _make_request(rf, ip="10.0.0.1")
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.99, 10.0.0.1"
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert str(log.ip_address) == "203.0.113.99"

    def test_geo_fields_populated_from_geo_lookup(self, rf: RequestFactory) -> None:
        """Geo fields on the RequestLog come from the geo_lookup result."""
        request = _make_request(rf)
        with patch("bulletins.services.geoip.geo_lookup", return_value=_FAKE_GEO):
            log = capture(request)
        assert log.country_code == "CH"
        assert log.subdivision_code == "VS"
        assert log.city == "Sion"
        assert log.latitude == pytest.approx(46.23)
        assert log.longitude == pytest.approx(7.36)
        assert log.accuracy_radius_km == 50

    def test_geo_fields_empty_when_lookup_returns_none(
        self, rf: RequestFactory
    ) -> None:
        """When geo_lookup returns None, geo fields are empty / null."""
        request = _make_request(rf)
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert log.country_code == ""
        assert log.city == ""
        assert log.latitude is None

    def test_language_parsed_from_accept_language(self, rf: RequestFactory) -> None:
        """capture() parses the primary language tag from Accept-Language."""
        request = _make_request(rf, accept_language="de-CH,de;q=0.9")
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert log.language == "de"
        assert log.accept_language == "de-CH,de;q=0.9"

    def test_user_agent_captured(self, rf: RequestFactory) -> None:
        """capture() stores the HTTP_USER_AGENT header."""
        request = _make_request(rf, ua="Snowdesk-Test/1.0")
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert log.user_agent == "Snowdesk-Test/1.0"

    def test_referer_captured(self, rf: RequestFactory) -> None:
        """capture() stores the Referer header."""
        request = _make_request(rf, referer="https://example.com/origin")
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert log.referer == "https://example.com/origin"

    def test_session_key_captured(self, rf: RequestFactory) -> None:
        """capture() stores the session key."""
        request = _make_request(rf)
        request.session.session_key = "mysession123"
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert log.session_key == "mysession123"

    def test_anonymous_request_leaves_subscriber_null(self, rf: RequestFactory) -> None:
        """Anonymous requests (unauthenticated) result in subscriber=None."""
        request = _make_request(rf, user=None)
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert log.subscriber is None

    def test_authenticated_request_links_subscriber(self, rf: RequestFactory) -> None:
        """Authenticated requests link the RequestLog to the Subscriber profile on request.user."""
        subscriber = SubscriberFactory.create()
        request = _make_request(rf, user=subscriber.user)
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert log.subscriber_id == subscriber.pk

    def test_sec_purpose_captured(self, rf: RequestFactory) -> None:
        """capture() stores the Sec-Purpose header on sec_purpose."""
        request = _make_request(rf)
        request.META["HTTP_SEC_PURPOSE"] = "prefetch"
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert log.sec_purpose == "prefetch"

    def test_sec_purpose_empty_when_header_absent(self, rf: RequestFactory) -> None:
        """When Sec-Purpose is not sent, sec_purpose is an empty string."""
        request = _make_request(rf)
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert log.sec_purpose == ""

    def test_sec_purpose_truncated_to_64(self, rf: RequestFactory) -> None:
        """Values longer than 64 characters are truncated to fit the field."""
        request = _make_request(rf)
        request.META["HTTP_SEC_PURPOSE"] = "x" * 100
        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            log = capture(request)
        assert len(log.sec_purpose) == 64
