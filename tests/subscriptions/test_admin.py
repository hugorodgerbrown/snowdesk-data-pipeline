"""
tests/subscriptions/test_admin.py — Tests for subscriptions/admin.py.

Covers:
  - EncryptedEmailSearchMixin.get_search_results:
      - 'email:' token finds the subscriber via each admin's encrypted lookup.
      - 'email:' token with wrong address returns empty queryset.
      - Empty 'email:' prefix returns empty queryset.
      - Bare term does NOT match the encrypted email column.
      - Bare term DOES match non-email search fields
        (region__region_id for SubscriptionAdmin, name for PasskeyCredentialAdmin).
"""

from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from subscriptions.admin import (
    PasskeyCredentialAdmin,
    SubscriberAdmin,
    SubscriptionAdmin,
)
from subscriptions.models import PasskeyCredential, Subscriber, Subscription
from tests.factories import (
    MicroRegionFactory,
    PasskeyCredentialFactory,
    SubscriberFactory,
    SubscriptionFactory,
)


def _get_request() -> Any:
    """Return a minimal fake GET request (no authentication needed)."""
    return RequestFactory().get("/admin/")


@pytest.mark.django_db
class TestSubscriberAdminSearch:
    """Tests for SubscriberAdmin.get_search_results."""

    def _admin(self) -> SubscriberAdmin:
        """Return a SubscriberAdmin bound to the default admin site."""
        return SubscriberAdmin(Subscriber, AdminSite())

    def test_email_token_finds_exact_subscriber(self) -> None:
        """'email:alice@example.com' finds the matching subscriber."""
        sub = SubscriberFactory.create(email="alice@example.com")
        SubscriberFactory.create(email="bob@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscriber.objects.all(),
            "email:alice@example.com",
        )
        assert list(qs) == [sub]

    def test_email_token_normalises_case(self) -> None:
        """'email:' token lowercases the address before encrypting."""
        sub = SubscriberFactory.create(email="alice@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscriber.objects.all(),
            "email:ALICE@EXAMPLE.COM",
        )
        assert list(qs) == [sub]

    def test_email_token_wrong_address_returns_empty(self) -> None:
        """An 'email:' token with no matching address returns an empty queryset."""
        SubscriberFactory.create(email="alice@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscriber.objects.all(),
            "email:nobody@example.com",
        )
        assert qs.count() == 0

    def test_empty_email_token_returns_empty(self) -> None:
        """An 'email:' prefix with no address returns an empty queryset."""
        SubscriberFactory.create(email="alice@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscriber.objects.all(),
            "email:",
        )
        assert qs.count() == 0

    def test_bare_term_does_not_match_encrypted_email(self) -> None:
        """A plain-text search does NOT find the encrypted email column."""
        sub = SubscriberFactory.create(email="alice@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscriber.objects.all(),
            "alice",
        )
        # search_fields = ("email",) means Django issues email__icontains="alice"
        # against the ciphertext column. Ciphertext never contains "alice", so
        # the subscriber must NOT appear in the results.
        pks = list(qs.values_list("pk", flat=True))
        assert sub.pk not in pks


@pytest.mark.django_db
class TestSubscriptionAdminSearch:
    """Tests for SubscriptionAdmin.get_search_results."""

    def _admin(self) -> SubscriptionAdmin:
        """Return a SubscriptionAdmin bound to the default admin site."""
        return SubscriptionAdmin(Subscription, AdminSite())

    def test_email_token_finds_subscription_by_subscriber_email(self) -> None:
        """'email:' token finds subscriptions via the subscriber's email."""
        sub = SubscriberFactory.create(email="alice@example.com")
        subscription = SubscriptionFactory.create(subscriber=sub)
        other = SubscriberFactory.create(email="bob@example.com")
        SubscriptionFactory.create(subscriber=other)

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscription.objects.all(),
            "email:alice@example.com",
        )
        assert list(qs) == [subscription]

    def test_region_id_search_still_works(self) -> None:
        """A bare region_id term still matches via search_fields."""
        region = MicroRegionFactory.create(region_id="CH-9999")
        subscription = SubscriptionFactory.create(region=region)

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscription.objects.all(),
            "CH-9999",
        )
        assert subscription in qs

    def test_email_token_does_not_match_region_id(self) -> None:
        """'email:CH-9999' does not match a region_id (wrong field)."""
        region = MicroRegionFactory.create(region_id="CH-9999")
        SubscriptionFactory.create(region=region)

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscription.objects.all(),
            "email:CH-9999",
        )
        assert qs.count() == 0


@pytest.mark.django_db
class TestPasskeyCredentialAdminSearch:
    """Tests for PasskeyCredentialAdmin.get_search_results."""

    def _admin(self) -> PasskeyCredentialAdmin:
        """Return a PasskeyCredentialAdmin bound to the default admin site."""
        return PasskeyCredentialAdmin(PasskeyCredential, AdminSite())

    def test_email_token_finds_passkey_by_subscriber_email(self) -> None:
        """'email:' token finds passkeys via the subscriber's email."""
        sub = SubscriberFactory.create(email="alice@example.com")
        passkey = PasskeyCredentialFactory.create(subscriber=sub)
        other = SubscriberFactory.create(email="bob@example.com")
        PasskeyCredentialFactory.create(subscriber=other)

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            PasskeyCredential.objects.all(),
            "email:alice@example.com",
        )
        assert list(qs) == [passkey]

    def test_name_search_still_works(self) -> None:
        """A bare name term still matches via search_fields."""
        passkey = PasskeyCredentialFactory.create(name="My iPhone passkey")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            PasskeyCredential.objects.all(),
            "iPhone",
        )
        assert passkey in qs

    def test_bare_email_fragment_does_not_match(self) -> None:
        """A bare email fragment does NOT match the encrypted subscriber__email."""
        sub = SubscriberFactory.create(email="alice@example.com")
        PasskeyCredentialFactory.create(subscriber=sub, name="unrelated-name")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            PasskeyCredential.objects.all(),
            "alice",
        )
        # "alice" should not match the encrypted email column; name is the
        # only active search_field, and "alice" is not in the name.
        pks = list(qs.values_list("pk", flat=True))
        assert sub.passkeys.get().pk not in pks
