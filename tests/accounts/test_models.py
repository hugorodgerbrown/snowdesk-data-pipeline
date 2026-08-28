"""
tests/accounts/test_models.py — Tests for accounts models.

Covers Account, Subscription, and PasskeyCredential model behaviour,
queryset methods, string representations, and field constraints.
"""

import datetime
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from apps.accounts.aaguids import lookup as aaguid_lookup
from apps.accounts.models import (
    Account,
    PasskeyCredential,
    Subscription,
    user_is_verified,
)
from tests.factories import (
    AccountFactory,
    MicroRegionFactory,
    PasskeyCredentialFactory,
    SubscriptionFactory,
    UserFactory,
)

User = get_user_model()


class TestAaguidLookup:
    """Tests for apps.accounts.aaguids.lookup."""

    def test_returns_name_for_known_aaguid(self) -> None:
        assert (
            aaguid_lookup(uuid.UUID("bada5566-a7aa-401f-bd96-45619a55120d"))
            == "1Password"
        )

    def test_returns_none_for_unknown_aaguid(self) -> None:
        assert aaguid_lookup(uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")) is None

    def test_returns_none_for_null_aaguid(self) -> None:
        assert aaguid_lookup(None) is None


@pytest.mark.django_db
class TestSubscriptionModel:
    """Tests for the Subscription model."""

    def test_str_returns_email_arrow_region(self) -> None:
        sub = SubscriptionFactory.create()
        expected = f"{sub.account.user.email} → {sub.region.region_id}"
        assert str(sub) == expected

    def test_to_string_matches_str(self) -> None:
        sub = SubscriptionFactory.create()
        assert sub.to_string() == str(sub)

    def test_unique_together_constraint(self) -> None:
        from django.db import IntegrityError

        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        with pytest.raises(IntegrityError):
            SubscriptionFactory.create(account=account, region=region)

    def test_has_uuid(self) -> None:
        sub = SubscriptionFactory.create()
        assert sub.uuid is not None


# ---------------------------------------------------------------------------
# Account and Subscription request_log FKs (SNOW-277, SNOW-514)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountAcquisitionRequest:
    """Account.acquisition_request FK defaults to None."""

    def test_acquisition_request_defaults_to_none(self) -> None:
        """Factory-created Account has acquisition_request=None."""
        account = AccountFactory.create()
        assert account.acquisition_request is None

    def test_acquisition_request_can_be_set(self) -> None:
        """acquisition_request can be set to a RequestLog instance."""
        from tests.factories import RequestLogFactory

        req_log = RequestLogFactory.create()
        account = AccountFactory.create(acquisition_request=req_log)
        account.refresh_from_db()
        assert account.acquisition_request_id == req_log.pk

    def test_acquisition_request_set_null_on_log_delete(self) -> None:
        """Deleting the RequestLog sets acquisition_request to None (SET_NULL)."""
        from tests.factories import RequestLogFactory

        req_log = RequestLogFactory.create()
        account = AccountFactory.create(acquisition_request=req_log)
        req_log.delete()
        account.refresh_from_db()
        assert account.acquisition_request is None


@pytest.mark.django_db
class TestSubscriptionSubscribedVia:
    """Subscription.subscribed_via FK defaults to None."""

    def test_subscribed_via_defaults_to_none(self) -> None:
        """Factory-created Subscription has subscribed_via=None."""
        subscription = SubscriptionFactory.create()
        assert subscription.subscribed_via is None

    def test_subscribed_via_can_be_set(self) -> None:
        """subscribed_via can be set to a RequestLog instance."""
        from tests.factories import RequestLogFactory

        req_log = RequestLogFactory.create()
        subscription = SubscriptionFactory.create(subscribed_via=req_log)
        subscription.refresh_from_db()
        assert subscription.subscribed_via_id == req_log.pk

    def test_subscribed_via_set_null_on_log_delete(self) -> None:
        """Deleting the RequestLog sets subscribed_via to None (SET_NULL)."""
        from tests.factories import RequestLogFactory

        req_log = RequestLogFactory.create()
        subscription = SubscriptionFactory.create(subscribed_via=req_log)
        req_log.delete()
        subscription.refresh_from_db()
        assert subscription.subscribed_via is None


# ---------------------------------------------------------------------------
# Subscription geo-match fields (SNOW-278)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubscriptionGeoMatchFields:
    """Tests for Subscription.geo_match_kind and geo_matched_region (SNOW-278)."""

    def test_geo_match_kind_defaults_to_unknown(self) -> None:
        """Factory-created Subscription has geo_match_kind='unknown' by default."""
        subscription = SubscriptionFactory.create()
        assert subscription.geo_match_kind == Subscription.GeoMatchKind.UNKNOWN

    def test_geo_matched_region_defaults_to_none(self) -> None:
        """Factory-created Subscription has geo_matched_region=None by default."""
        subscription = SubscriptionFactory.create()
        assert subscription.geo_matched_region is None

    def test_geo_match_kind_can_be_set_to_in_region(self) -> None:
        """geo_match_kind can be explicitly set to IN_REGION."""
        region = MicroRegionFactory.create()
        subscription = SubscriptionFactory.create(
            geo_match_kind=Subscription.GeoMatchKind.IN_REGION,
            geo_matched_region=region,
        )
        subscription.refresh_from_db()
        assert subscription.geo_match_kind == Subscription.GeoMatchKind.IN_REGION
        assert subscription.geo_matched_region == region

    def test_geo_match_kind_can_be_set_to_elsewhere(self) -> None:
        """geo_match_kind can be explicitly set to ELSEWHERE."""
        subscription = SubscriptionFactory.create(
            geo_match_kind=Subscription.GeoMatchKind.ELSEWHERE,
        )
        subscription.refresh_from_db()
        assert subscription.geo_match_kind == Subscription.GeoMatchKind.ELSEWHERE

    def test_geo_matched_region_is_nullable(self) -> None:
        """geo_matched_region can be null (elsewhere / unknown cases)."""
        subscription = SubscriptionFactory.create(geo_matched_region=None)
        subscription.refresh_from_db()
        assert subscription.geo_matched_region is None

    def test_geo_matched_region_set_null_on_region_delete(self) -> None:
        """Deleting the matched MicroRegion sets geo_matched_region to None (SET_NULL)."""
        matched = MicroRegionFactory.create()
        subscription = SubscriptionFactory.create(
            geo_match_kind=Subscription.GeoMatchKind.IN_REGION,
            geo_matched_region=matched,
        )
        matched.delete()
        subscription.refresh_from_db()
        assert subscription.geo_matched_region is None

    def test_geomatchkind_choices_are_correct(self) -> None:
        """GeoMatchKind has all four expected values."""
        kinds = {c[0] for c in Subscription.GeoMatchKind.choices}
        assert kinds == {"IN_REGION", "IN_NEIGHBOUR", "ELSEWHERE", "UNKNOWN"}


# ---------------------------------------------------------------------------
# PasskeyCredential
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyCredentialModel:
    """Tests for the PasskeyCredential model."""

    def test_str_returns_email_and_name(self) -> None:
        passkey = PasskeyCredentialFactory.create(name="My passkey")
        assert "My passkey" in str(passkey)
        assert passkey.user.email in str(passkey)

    def test_to_string_matches_str(self) -> None:
        passkey = PasskeyCredentialFactory.create()
        assert passkey.to_string() == str(passkey)

    def test_has_uuid(self) -> None:
        passkey = PasskeyCredentialFactory.create()
        assert passkey.uuid is not None

    def test_has_created_at(self) -> None:
        passkey = PasskeyCredentialFactory.create()
        assert passkey.created_at is not None

    def test_credential_id_is_unique(self) -> None:
        from django.db import IntegrityError

        PasskeyCredentialFactory.create(credential_id="unique-cred")
        with pytest.raises(IntegrityError):
            PasskeyCredentialFactory.create(credential_id="unique-cred")

    def test_cascade_deletes_with_user(self) -> None:
        passkey = PasskeyCredentialFactory.create()
        pk = passkey.pk
        passkey.user.delete()
        assert not PasskeyCredential.objects.filter(pk=pk).exists()

    def test_default_sign_count_is_zero(self) -> None:
        passkey = PasskeyCredentialFactory.create(sign_count=0)
        assert passkey.sign_count == 0

    def test_backed_up_default_false(self) -> None:
        passkey = PasskeyCredentialFactory.create()
        assert passkey.backed_up is False

    def test_last_used_at_nullable(self) -> None:
        passkey = PasskeyCredentialFactory.create(last_used_at=None)
        assert passkey.last_used_at is None

    def test_aaguid_nullable(self) -> None:
        passkey = PasskeyCredentialFactory.create(aaguid=None)
        assert passkey.aaguid is None

    def test_display_name_uses_aaguid_lookup_when_known(self) -> None:
        _1password_aaguid = uuid.UUID("bada5566-a7aa-401f-bd96-45619a55120d")
        passkey = PasskeyCredentialFactory.create(
            aaguid=_1password_aaguid,
            name="Synced passkey — 1 Jan 2025",
        )
        assert passkey.display_name.startswith("1Password — ")

    def test_display_name_falls_back_to_stored_name_for_unknown_aaguid(self) -> None:
        unknown_aaguid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        passkey = PasskeyCredentialFactory.create(
            aaguid=unknown_aaguid,
            name="Device passkey — 1 Jan 2025",
        )
        assert passkey.display_name == "Device passkey — 1 Jan 2025"

    def test_display_name_falls_back_to_stored_name_when_aaguid_is_none(self) -> None:
        passkey = PasskeyCredentialFactory.create(
            aaguid=None,
            name="Synced passkey — 1 Jan 2025",
        )
        assert passkey.display_name == "Synced passkey — 1 Jan 2025"

    def test_provider_name_omits_the_date_display_name_appends(self) -> None:
        """SNOW-746: the name alone, for a surface that prints the date itself.

        The settings page's passkey row carries an "Added {date}" meta line
        under the name, so ``display_name`` there would print the date twice.
        """
        _1password_aaguid = uuid.UUID("bada5566-a7aa-401f-bd96-45619a55120d")
        passkey = PasskeyCredentialFactory.create(
            aaguid=_1password_aaguid,
            name="Synced passkey — 1 Jan 2025",
        )
        assert passkey.provider_name == "1Password"
        assert " — " not in passkey.provider_name
        assert passkey.display_name.startswith("1Password — ")

    def test_provider_name_falls_back_to_stored_name_for_unknown_aaguid(self) -> None:
        """With no provider to look up, both properties agree on the stored name."""
        unknown_aaguid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        passkey = PasskeyCredentialFactory.create(
            aaguid=unknown_aaguid,
            name="Device passkey — 1 Jan 2025",
        )
        assert passkey.provider_name == "Device passkey — 1 Jan 2025"
        assert passkey.provider_name == passkey.display_name


@pytest.mark.django_db
class TestPasskeyCredentialQuerySet:
    """Tests for PasskeyCredentialQuerySet custom methods."""

    def test_for_user_returns_correct_passkeys(self) -> None:
        account_a = AccountFactory.create()
        account_b = AccountFactory.create()
        pk_a = PasskeyCredentialFactory.create(user=account_a.user)
        PasskeyCredentialFactory.create(user=account_b.user)
        result = PasskeyCredential.objects.for_user(account_a.user)
        assert list(result) == [pk_a]

    def test_by_credential_id_finds_exact_match(self) -> None:
        passkey = PasskeyCredentialFactory.create(credential_id="exact-cred-id")
        result = PasskeyCredential.objects.by_credential_id("exact-cred-id")
        assert passkey in result

    def test_by_credential_id_returns_empty_for_unknown(self) -> None:
        result = PasskeyCredential.objects.by_credential_id("does-not-exist")
        assert result.count() == 0


@pytest.mark.django_db
class TestAccountModel:
    """Tests for the Account identity-profile model (SNOW-430)."""

    def test_to_string_returns_user_email(self) -> None:
        account = AccountFactory.create(user__email="alice@example.com")
        assert account.to_string() == "alice@example.com"
        assert str(account) == "alice@example.com"

    def test_defaults_are_unverified_when_built_unverified(self) -> None:
        account = AccountFactory.create(is_verified=False)
        assert account.is_verified is False
        assert account.verified_at is None

    def test_mark_verified_sets_flag_and_timestamp(self) -> None:
        account = AccountFactory.create(is_verified=False)
        now = timezone.now()
        result = account.mark_verified(now)
        assert result is account  # returns self for chaining
        assert account.is_verified is True
        assert account.verified_at == now

    def test_mark_verified_does_not_save(self) -> None:
        """mark_verified mutates in memory only; the caller owns the save."""
        account = AccountFactory.create(is_verified=False)
        account.mark_verified(timezone.now())
        account.refresh_from_db()
        assert account.is_verified is False

    def test_mark_verified_is_idempotent_on_timestamp(self) -> None:
        """A second mark_verified does not overwrite the original verified_at."""
        first = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        account = AccountFactory.create(is_verified=True, verified_at=first)
        account.mark_verified(timezone.now())
        assert account.verified_at == first

    def test_request_email_change_sets_pending(self) -> None:
        account = AccountFactory.create()
        now = timezone.now()
        result = account.request_email_change("New@Example.com", now)
        assert result is account
        assert account.pending_email == "new@example.com"  # lowercased
        assert account.pending_email_requested_at == now

    def test_clear_pending_email(self) -> None:
        account = AccountFactory.create()
        account.request_email_change("new@example.com", timezone.now())
        account.clear_pending_email()
        assert account.pending_email is None
        assert account.pending_email_requested_at is None


@pytest.mark.django_db
class TestUserIsVerified:
    """Tests for the shared ``user_is_verified`` helper (SNOW-477).

    This is the single definition behind both the server-side field-report gate
    (``apps/observations/views.py``) and the client-side ``report_eligible`` flag
    (``apps/public/views.py``).
    """

    def test_false_for_anonymous_user(self) -> None:
        """An anonymous user is never verified."""
        assert user_is_verified(AnonymousUser()) is False

    def test_false_for_authenticated_user_without_account(self) -> None:
        """An authenticated user with no Account row is not verified."""
        user = UserFactory.create()
        assert user_is_verified(user) is False

    def test_false_for_unverified_account(self) -> None:
        """An authenticated user with an unverified Account is not verified."""
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=False)
        assert user_is_verified(user) is False

    def test_true_for_verified_account(self) -> None:
        """An authenticated user with a verified Account is verified."""
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=True)
        assert user_is_verified(user) is True


@pytest.mark.django_db
class TestAccountQuerySet:
    """Tests for AccountQuerySet / AccountManager helpers."""

    def test_verified_filters_to_verified_only(self) -> None:
        verified = AccountFactory.create(is_verified=True)
        AccountFactory.create(is_verified=False)
        result = Account.objects.verified()
        assert list(result) == [verified]

    def test_by_email_lowercases(self) -> None:
        account = AccountFactory.create(user__email="mixed@example.com")
        assert account in Account.objects.by_email("MIXED@example.com")

    def test_get_or_create_for_email_creates_user_and_account(self) -> None:
        account, created = Account.objects.get_or_create_for_email("New@Example.com")
        assert created is True
        assert account.user.username == "new@example.com"
        assert account.user.email == "new@example.com"
        assert account.is_verified is False

    def test_get_or_create_for_email_reuses_existing_user(self) -> None:
        user = UserFactory.create(email="existing@example.com", is_staff=False)
        account, created = Account.objects.get_or_create_for_email(
            "existing@example.com"
        )
        assert created is True  # Account is new, User pre-existed
        assert account.user == user

    def test_get_or_create_for_email_is_idempotent(self) -> None:
        first, _ = Account.objects.get_or_create_for_email("dup@example.com")
        second, created = Account.objects.get_or_create_for_email("dup@example.com")
        assert created is False
        assert first.pk == second.pk

    def test_get_or_create_for_email_applies_defaults(self) -> None:
        account, _ = Account.objects.get_or_create_for_email(
            "named@example.com", defaults={"display_name": "Named"}
        )
        assert account.display_name == "Named"
