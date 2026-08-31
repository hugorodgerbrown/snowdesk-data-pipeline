"""
tests/accounts/test_admin.py — Tests for apps/accounts/admin.py.

Covers:
  - search_fields configuration for each admin class.
  - That get_search_results finds accounts via partial email search
    (icontains) through each admin — verifying the SNOW-313 acceptance
    criterion that email lookup goes via user__email.
  - PasskeyCredentialAdmin search now goes via user__email (SNOW-334).
  - PushSubscriptionAdmin surfaces mechanism/inactive_at (SNOW-380).
  - AccountAdmin surfaces acquisition_request (SNOW-514).
  - AccountAdmin's delete hooks erase through the shared deletion service,
    so an admin delete removes as much as /account/ deletion does.
"""

from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory

from apps.accounts.admin import (
    AccountAdmin,
    PasskeyCredentialAdmin,
    PushSubscriptionAdmin,
    SubscriptionAdmin,
)
from apps.accounts.models import (
    Account,
    PasskeyCredential,
    PushSubscription,
    Subscription,
)
from apps.core.models import RequestLog
from apps.locations.models import Location
from tests.factories import (
    AccountFactory,
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    PasskeyCredentialFactory,
    RequestLogFactory,
    SubscriptionFactory,
    UserFactory,
)


def _get_request() -> Any:
    """Return a minimal fake GET request (no authentication needed)."""
    return RequestFactory().get("/admin/")


class TestAdminSearchFieldsConfig:
    """Verify search_fields declarations on each admin class."""

    def test_account_admin_search_fields(self) -> None:
        """AccountAdmin.search_fields includes 'user__email'."""
        admin = AccountAdmin(Account, AdminSite())
        assert "user__email" in admin.search_fields

    def test_subscription_admin_search_fields(self) -> None:
        """SubscriptionAdmin.search_fields includes 'account__user__email' and 'region__region_id'."""
        admin = SubscriptionAdmin(Subscription, AdminSite())
        assert "account__user__email" in admin.search_fields
        assert "region__region_id" in admin.search_fields

    def test_passkey_credential_admin_search_fields(self) -> None:
        """PasskeyCredentialAdmin.search_fields includes 'user__email' and 'name'."""
        admin = PasskeyCredentialAdmin(PasskeyCredential, AdminSite())
        assert "user__email" in admin.search_fields
        assert "name" in admin.search_fields


@pytest.mark.django_db
class TestAccountAdminSearch:
    """Tests for AccountAdmin.get_search_results."""

    def _admin(self) -> AccountAdmin:
        """Return an AccountAdmin bound to the default admin site."""
        return AccountAdmin(Account, AdminSite())

    def test_partial_email_search_finds_account(self) -> None:
        """Partial email fragment matches via user__email icontains."""
        account = AccountFactory.create(user__email="alice@example.com")
        AccountFactory.create(user__email="bob@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Account.objects.all(),
            "alice",
        )
        assert account in list(qs)

    def test_full_email_search_finds_exact_account(self) -> None:
        """Full email address finds exactly the matching account."""
        account = AccountFactory.create(user__email="alice@example.com")
        AccountFactory.create(user__email="bob@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Account.objects.all(),
            "alice@example.com",
        )
        assert account in list(qs)

    def test_no_match_returns_empty(self) -> None:
        """A search term with no match returns an empty result set."""
        AccountFactory.create(user__email="alice@example.com")

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            Account.objects.all(),
            "nobody",
        )
        assert qs.count() == 0


@pytest.mark.django_db
class TestAccountAdminAcquisitionRequest:
    """SNOW-514: AccountAdmin surfaces acquisition_request."""

    def test_acquisition_request_in_list_display(self) -> None:
        """'acquisition_request' is a visible column on the changelist."""
        admin = AccountAdmin(Account, AdminSite())
        assert "acquisition_request" in admin.list_display

    def test_acquisition_request_is_readonly(self) -> None:
        """acquisition_request is readonly — the admin edits nothing here."""
        admin = AccountAdmin(Account, AdminSite())
        assert "acquisition_request" in admin.readonly_fields

    def test_acquisition_request_value_shown(self) -> None:
        """A populated acquisition_request round-trips through the queryset."""
        req_log = RequestLogFactory.create()
        account = AccountFactory.create(acquisition_request=req_log)
        account.refresh_from_db()
        assert account.acquisition_request_id == req_log.pk


@pytest.mark.django_db
class TestAccountAdminDeletion:
    """Deleting through the admin erases as much as the user-facing path.

    ``AccountAdmin`` had no delete hooks, so an operator deleting an account
    from the changelist removed the profile row and left behind everything a
    CASCADE cannot reach: the sign-up ``RequestLog`` (holding an IP address,
    city and coordinates), the favourites' minted Locations, and the
    ``auth.User`` itself. Both hooks now route through ``erase_account`` —
    the admin's bulk action calls ``delete_queryset``, single-object deletes
    call ``delete_model``, and a fix to only one of them is half a fix.
    """

    def _admin(self) -> AccountAdmin:
        """Return an AccountAdmin bound to the default admin site."""
        return AccountAdmin(Account, AdminSite())

    def _loaded_account(self) -> tuple[Account, RequestLog, Location]:
        """Return an account with a sign-up request row and one favourite.

        Returns:
            The account, the RequestLog its ``acquisition_request`` points
            at, and the Location its favourite is the only referent of.

        """
        signup = RequestLogFactory.create(account=None, ip_address="203.0.113.20")
        account = AccountFactory.create(acquisition_request=signup)
        location = LocationFactory.create(anonymous=True)
        FavouriteFactory.create(user=account.user, location=location)
        return account, signup, location

    def test_delete_model_erases_everything(self) -> None:
        """Single-object delete takes the user, the log row and the location."""
        account, signup, location = self._loaded_account()
        user_pk = account.user_id

        self._admin().delete_model(_get_request(), account)

        assert not Account.objects.filter(pk=account.pk).exists()
        assert not User.objects.filter(pk=user_pk).exists()
        assert not RequestLog.objects.filter(pk=signup.pk).exists()
        assert not Location.objects.filter(pk=location.pk).exists()

    def test_delete_queryset_erases_every_selected_account(self) -> None:
        """The bulk action erases each row, not just the first."""
        first, first_log, first_location = self._loaded_account()
        second, second_log, second_location = self._loaded_account()

        self._admin().delete_queryset(
            _get_request(), Account.objects.filter(pk__in=[first.pk, second.pk])
        )

        assert not Account.objects.filter(pk__in=[first.pk, second.pk]).exists()
        assert not RequestLog.objects.filter(
            pk__in=[first_log.pk, second_log.pk]
        ).exists()
        assert not Location.objects.filter(
            pk__in=[first_location.pk, second_location.pk]
        ).exists()

    def test_delete_queryset_leaves_unselected_accounts_alone(self) -> None:
        """A bulk delete scoped to one row must not empty the table."""
        target, _, _ = self._loaded_account()
        bystander, bystander_log, bystander_location = self._loaded_account()

        self._admin().delete_queryset(
            _get_request(), Account.objects.filter(pk=target.pk)
        )

        assert Account.objects.filter(pk=bystander.pk).exists()
        assert RequestLog.objects.filter(pk=bystander_log.pk).exists()
        assert Location.objects.filter(pk=bystander_location.pk).exists()


@pytest.mark.django_db
class TestSubscriptionAdminSearch:
    """Tests for SubscriptionAdmin.get_search_results."""

    def _admin(self) -> SubscriptionAdmin:
        """Return a SubscriptionAdmin bound to the default admin site."""
        return SubscriptionAdmin(Subscription, AdminSite())

    def test_partial_account_email_finds_subscription(self) -> None:
        """Partial email fragment finds subscriptions via account__user__email icontains."""
        account = AccountFactory.create(user__email="alice@example.com")
        subscription = SubscriptionFactory.create(account=account)
        other = AccountFactory.create(user__email="bob@example.com")
        SubscriptionFactory.create(account=other)

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

    def test_partial_account_email_finds_passkey(self) -> None:
        """Partial email fragment finds passkeys via user__email icontains."""
        user_alice = UserFactory.create(
            email="alice@example.com", username="alice@example.com"
        )
        passkey = PasskeyCredentialFactory.create(user=user_alice)
        user_bob = UserFactory.create(
            email="bob@example.com", username="bob@example.com"
        )
        PasskeyCredentialFactory.create(user=user_bob)

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            PasskeyCredential.objects.all(),
            "alice",
        )
        assert passkey in list(qs)

    def test_name_search_still_works(self) -> None:
        """A passkey name fragment still matches via search_fields."""
        passkey = PasskeyCredentialFactory.create(
            user=UserFactory.create(), name="My iPhone passkey"
        )

        admin = self._admin()
        qs, _ = admin.get_search_results(
            _get_request(),
            PasskeyCredential.objects.all(),
            "iPhone",
        )
        assert passkey in list(qs)


class TestPushSubscriptionAdminConfig:
    """SNOW-380: PushSubscriptionAdmin surfaces mechanism and inactive_at."""

    def _admin(self) -> PushSubscriptionAdmin:
        """Return a PushSubscriptionAdmin bound to the default admin site."""
        return PushSubscriptionAdmin(PushSubscription, AdminSite())

    def test_mechanism_in_list_display(self) -> None:
        """'mechanism' is a visible column on the changelist."""
        admin = self._admin()
        assert "mechanism" in admin.list_display

    def test_inactive_at_in_list_filter(self) -> None:
        """'inactive_at' is filterable so operators can find dead subscriptions."""
        admin = self._admin()
        assert "inactive_at" in admin.list_filter

    def test_mechanism_and_inactive_at_are_readonly(self) -> None:
        """Both new fields are readonly — the admin is a diagnostic surface only."""
        admin = self._admin()
        assert "mechanism" in admin.readonly_fields
        assert "inactive_at" in admin.readonly_fields
