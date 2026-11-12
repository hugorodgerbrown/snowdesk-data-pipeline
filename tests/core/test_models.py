"""
tests/core/test_models.py — Tests for the apps.core.models concrete models.

Covers RequestLog's factory creation, to_string() format, default ordering,
and the from_request() manager method (end-to-end capture with a patched
geo_lookup); and Lease (SNOW-1490): acquire() on a free, live and expired
key, the expired-row race, token-checked release() and the admin.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import models, transaction
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.services.geoip import GeoLookup
from apps.core.models import Lease, RequestLog
from tests.factories import AccountFactory, LeaseFactory, RequestLogFactory


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

    def test_account_defaults_to_none(self) -> None:
        """Factory leaves account null by default."""
        log = RequestLogFactory.create()
        assert log.account is None

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

        with patch("apps.bulletins.services.geoip.geo_lookup", return_value=None):
            log = RequestLog.objects.from_request(request)

        assert str(log.ip_address) == "203.0.113.5"
        assert log.user_agent == "TestAgent/1.0"

    def test_from_request_links_authenticated_account(self, rf: RequestFactory) -> None:
        """Authenticated requests link the log to the Account profile on request.user."""
        account = AccountFactory.create()
        request = rf.post("/", REMOTE_ADDR="203.0.113.1")
        request.session = type("S", (), {"session_key": "y"})()
        request.user = account.user  # noqa: PGH003 — auth.User is request.user

        with patch("apps.bulletins.services.geoip.geo_lookup", return_value=None):
            log = RequestLog.objects.from_request(request)

        assert log.account_id == account.pk

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

        with patch("apps.bulletins.services.geoip.geo_lookup", return_value=geo):
            log = RequestLog.objects.from_request(request)

        assert log.country_code == "FR"
        assert log.city == "Grenoble"
        assert log.latitude == pytest.approx(45.18)


# ---------------------------------------------------------------------------
# Lease (SNOW-1490)
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
_TTL = timedelta(seconds=30)


@pytest.mark.django_db
class TestLeaseModel:
    """The model kit: factory, to_string(), ordering and the queryset."""

    def test_factory_creates_a_live_lease(self) -> None:
        """LeaseFactory persists a lease that has not yet expired."""
        lease = LeaseFactory.create()
        assert lease.pk is not None
        assert Lease.objects.live().filter(pk=lease.pk).exists()

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ is to_string(): the key and the expiry."""
        lease = LeaseFactory.create(key="remote-command:x", expires_at=_T0)
        assert (
            str(lease)
            == lease.to_string()
            == "remote-command:x until 2026-11-12 09:00:00"
        )

    def test_ordering_newest_first(self) -> None:
        """The default ordering is -created_at."""
        older = LeaseFactory.create()
        newer = LeaseFactory.create()
        assert list(Lease.objects.all()) == [newer, older]

    def test_live_and_expired_split_on_expires_at(self) -> None:
        """live() holds the unexpired leases; expired() the rest."""
        now = timezone.now()
        live = LeaseFactory.create(expires_at=now + _TTL)
        gone = LeaseFactory.create(expires_at=now - _TTL)
        assert list(Lease.objects.live()) == [live]
        assert list(Lease.objects.expired()) == [gone]


@pytest.mark.django_db
class TestLeaseAcquire:
    """LeaseManager.acquire() takes a free or expired lease, never a live one."""

    def test_free_key_is_inserted(self) -> None:
        """A key with no row is taken, expiring ttl from now."""
        with patch("apps.core.models.timezone.now", return_value=_T0):
            lease = Lease.objects.acquire("k", ttl=_TTL)
        assert lease is not None
        assert lease.key == "k"
        assert lease.expires_at == _T0 + _TTL
        assert Lease.objects.get(key="k").token == lease.token

    def test_live_lease_is_refused(self) -> None:
        """A lease another holder has and that has not expired is not taken."""
        held = LeaseFactory.create(key="k")
        assert Lease.objects.acquire("k", ttl=_TTL) is None
        assert Lease.objects.get(key="k").token == held.token

    def test_expired_lease_is_taken_over_with_a_new_token(self) -> None:
        """An expired lease is renewed in place, under a token of its own."""
        stale = LeaseFactory.create(key="k", expires_at=_T0 - timedelta(seconds=1))
        with patch("apps.core.models.timezone.now", return_value=_T0):
            lease = Lease.objects.acquire("k", ttl=_TTL)
        assert lease is not None
        assert lease.pk == stale.pk
        assert lease.token != stale.token
        row = Lease.objects.get(key="k")
        assert row.token == lease.token
        assert row.expires_at == _T0 + _TTL

    def test_two_claimants_of_one_expired_lease_get_one_lease(self) -> None:
        """Of two claimants that both saw the lease expired, only the first wins.

        Both run at the same ``now``: the second's conditional UPDATE meets
        the expiry the first has already renewed and matches no row.
        """
        LeaseFactory.create(key="k", expires_at=_T0 - timedelta(seconds=1))
        with patch("apps.core.models.timezone.now", return_value=_T0):
            first = Lease.objects.acquire("k", ttl=_TTL)
            second = Lease.objects.acquire("k", ttl=_TTL)
        assert first is not None
        assert second is None
        assert Lease.objects.get(key="k").token == first.token

    def test_takeover_released_before_the_read_returns_none(self) -> None:
        """A row deleted between the takeover and the read back is not held."""
        LeaseFactory.create(key="k", expires_at=_T0 - timedelta(seconds=1))
        with (
            patch("apps.core.models.timezone.now", return_value=_T0),
            patch.object(models.QuerySet, "first", return_value=None),
        ):
            assert Lease.objects.acquire("k", ttl=_TTL) is None

    def test_a_refused_insert_leaves_the_outer_transaction_usable(self) -> None:
        """The INSERT's savepoint keeps a collision out of the caller's transaction."""
        LeaseFactory.create(key="k")
        with transaction.atomic():
            assert Lease.objects.acquire("k", ttl=_TTL) is None
            assert Lease.objects.filter(key="k").count() == 1


@pytest.mark.django_db
class TestLeaseRelease:
    """Lease.release() frees only the holder's own claim."""

    def test_release_deletes_the_holders_lease(self) -> None:
        """The holder's release frees the key for the next claimant."""
        lease = Lease.objects.acquire("k", ttl=_TTL)
        assert lease is not None
        assert lease.release() is True
        assert not Lease.objects.filter(key="k").exists()
        assert Lease.objects.acquire("k", ttl=_TTL) is not None

    def test_stale_holders_release_leaves_the_new_lease(self) -> None:
        """A holder that overran its lease cannot free the one taken over from it."""
        with patch("apps.core.models.timezone.now", return_value=_T0):
            stale = Lease.objects.acquire("k", ttl=_TTL)
        with patch("apps.core.models.timezone.now", return_value=_T0 + 2 * _TTL):
            fresh = Lease.objects.acquire("k", ttl=_TTL)
        assert stale is not None
        assert fresh is not None
        assert stale.release() is False
        assert Lease.objects.get(key="k").token == fresh.token


@pytest.mark.django_db
class TestLeaseAdmin:
    """The Lease admin is a read-only list."""

    def test_changelist_renders(self, admin_client: Client) -> None:
        """The changelist lists a held lease by its key."""
        LeaseFactory.create(key="remote-command:backfill")
        response = admin_client.get(reverse("admin:core_lease_changelist"))
        assert response.status_code == 200
        assert b"remote-command:backfill" in response.content

    def test_admin_is_read_only(self, admin_client: Client) -> None:
        """No lease can be added, changed or deleted from the admin."""
        lease = LeaseFactory.create()
        assert admin_client.get(reverse("admin:core_lease_add")).status_code == 403
        delete = reverse("admin:core_lease_delete", args=[lease.pk])
        assert admin_client.post(delete, {"post": "yes"}).status_code == 403
        assert Lease.objects.filter(pk=lease.pk).exists()
