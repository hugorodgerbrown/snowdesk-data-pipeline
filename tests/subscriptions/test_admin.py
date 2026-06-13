"""
tests/subscriptions/test_admin.py — Tests for subscriptions/admin.py.

Covers:
  - search_fields configuration for each admin class.
  - That get_search_results finds subscribers via partial email search
    (icontains) through each of the three admins — verifying the SNOW-313
    acceptance criterion that email lookup goes via user__email.
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


class TestAdminSearchFieldsConfig:
    """Verify search_fields declarations on each admin class."""

    def test_subscriber_admin_search_fields(self) -> None:
        """SubscriberAdmin.search_fields includes 'user__email'."""
        admin = SubscriberAdmin(Subscriber, AdminSite())
        assert "user__email" in admin.search_fields

    def test_subscription_admin_search_fields(self) -> None:
        """SubscriptionAdmin.search_fields includes 'subscriber__user__email' and 'region__region_id'."""
        admin = SubscriptionAdmin(Subscription, AdminSite())
        assert "subscriber__user__email" in admin.search_fields
        assert "region__region_id" in admin.search_fields

    def test_passkey_credential_admin_search_fields(self) -> None:
        """PasskeyCredentialAdmin.search_fields includes 'subscriber__user__email' and 'name'."""
        admin = PasskeyCredentialAdmin(PasskeyCredential, AdminSite())
        assert "subscriber__user__email" in admin.search_fields
        assert "name" in admin.search_fields


@pytest.mark.django_db
class TestSubscriberAdminSearch:
    """Tests for SubscriberAdmin.get_search_results."""

    def _admin(self) -> SubscriberAdmin:
        """Return a SubscriberAdmin bound to the default admin site."""
        return SubscriberAdmin(Subscriber, AdminSite())

    def test_partial_email_search_finds_subscriber(self) -> None:
        """Partial email fragment matches via user__email icontains."""
        sub = SubscriberFactory.create(user__email="alice@example.com")
        SubscriberFactory.create(user__email="bob@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscriber.objects.all(),
            "alice",
        )
        assert sub in list(qs)

    def test_full_email_search_finds_exact_subscriber(self) -> None:
        """Full email address finds exactly the matching subscriber."""
        sub = SubscriberFactory.create(user__email="alice@example.com")
        SubscriberFactory.create(user__email="bob@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscriber.objects.all(),
            "alice@example.com",
        )
        assert sub in list(qs)

    def test_no_match_returns_empty(self) -> None:
        """A search term with no match returns an empty result set."""
        SubscriberFactory.create(user__email="alice@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscriber.objects.all(),
            "nobody",
        )
        assert qs.count() == 0


@pytest.mark.django_db
class TestSubscriptionAdminSearch:
    """Tests for SubscriptionAdmin.get_search_results."""

    def _admin(self) -> SubscriptionAdmin:
        """Return a SubscriptionAdmin bound to the default admin site."""
        return SubscriptionAdmin(Subscription, AdminSite())

    def test_partial_subscriber_email_finds_subscription(self) -> None:
        """Partial email fragment finds subscriptions via subscriber__user__email icontains."""
        sub = SubscriberFactory.create(user__email="alice@example.com")
        subscription = SubscriptionFactory.create(subscriber=sub)
        other = SubscriberFactory.create(user__email="bob@example.com")
        SubscriptionFactory.create(subscriber=other)

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscription.objects.all(),
            "alice",
        )
        assert subscription in list(qs)

    def test_region_id_search_still_works(self) -> None:
        """A region_id fragment still matches via search_fields."""
        region = MicroRegionFactory.create(region_id="CH-9999")
        subscription = SubscriptionFactory.create(region=region)

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Subscription.objects.all(),
            "CH-9999",
        )
        assert subscription in list(qs)


@pytest.mark.django_db
class TestPasskeyCredentialAdminSearch:
    """Tests for PasskeyCredentialAdmin.get_search_results."""

    def _admin(self) -> PasskeyCredentialAdmin:
        """Return a PasskeyCredentialAdmin bound to the default admin site."""
        return PasskeyCredentialAdmin(PasskeyCredential, AdminSite())

    def test_partial_subscriber_email_finds_passkey(self) -> None:
        """Partial email fragment finds passkeys via subscriber__user__email icontains."""
        sub = SubscriberFactory.create(user__email="alice@example.com")
        passkey = PasskeyCredentialFactory.create(subscriber=sub)
        other = SubscriberFactory.create(user__email="bob@example.com")
        PasskeyCredentialFactory.create(subscriber=other)

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            PasskeyCredential.objects.all(),
            "alice",
        )
        assert passkey in list(qs)

    def test_name_search_still_works(self) -> None:
        """A passkey name fragment still matches via search_fields."""
        passkey = PasskeyCredentialFactory.create(name="My iPhone passkey")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            PasskeyCredential.objects.all(),
            "iPhone",
        )
        assert passkey in list(qs)
