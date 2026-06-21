"""
tests/subscriptions/test_models.py — Tests for subscriptions models.

Covers Subscriber, Subscription, and PasskeyCredential model behaviour,
queryset methods, string representations, and field constraints.
"""

import uuid

import pytest
from django.contrib.auth import get_user_model

from subscriptions.aaguids import lookup as aaguid_lookup
from subscriptions.models import PasskeyCredential, Subscriber, Subscription
from tests.factories import (
    MicroRegionFactory,
    PasskeyCredentialFactory,
    SubscriberFactory,
    SubscriptionFactory,
    UserFactory,
)

User = get_user_model()


class TestAaguidLookup:
    """Tests for subscriptions.aaguids.lookup."""

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
class TestSubscriberModel:
    """Tests for the Subscriber model."""

    def test_str_returns_email(self) -> None:
        sub = SubscriberFactory.create(user__email="alice@example.com")
        assert str(sub) == "alice@example.com"

    def test_to_string_returns_email(self) -> None:
        sub = SubscriberFactory.create(user__email="bob@example.com")
        assert sub.to_string() == "bob@example.com"

    def test_default_status_is_pending_on_fresh_create(self) -> None:
        user = UserFactory.create(
            username="fresh@example.com",
            email="fresh@example.com",
            is_staff=False,
        )
        sub = Subscriber.objects.create(user=user)
        assert sub.status == Subscriber.Status.PENDING

    def test_factory_default_status_is_active(self) -> None:
        sub = SubscriberFactory.create()
        assert sub.status == Subscriber.Status.ACTIVE

    def test_confirmed_at_nullable(self) -> None:
        sub = SubscriberFactory.create()
        sub.confirmed_at = None
        sub.save(update_fields=["confirmed_at"])
        sub.refresh_from_db()
        assert sub.confirmed_at is None

    def test_one_subscriber_per_user(self) -> None:
        """A second Subscriber for the same User violates the OneToOneField constraint."""
        from django.db import IntegrityError

        existing = SubscriberFactory.create()
        # Attempt to create a second Subscriber linked to the same User.
        with pytest.raises(IntegrityError):
            Subscriber.objects.create(user=existing.user)

    def test_has_uuid(self) -> None:
        sub = SubscriberFactory.create()
        assert sub.uuid is not None

    def test_has_created_at(self) -> None:
        sub = SubscriberFactory.create()
        assert sub.created_at is not None

    def test_status_choices(self) -> None:
        assert Subscriber.Status.PENDING == "pending"
        assert Subscriber.Status.ACTIVE == "active"

    def test_pending_status_persists(self) -> None:
        sub = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        sub.refresh_from_db()
        assert sub.status == Subscriber.Status.PENDING


@pytest.mark.django_db
class TestSubscriberIsActive:
    """Tests for Subscriber.is_active property."""

    def test_is_active_true_when_status_active(self) -> None:
        """is_active returns True when status is ACTIVE."""
        sub = SubscriberFactory.create(status=Subscriber.Status.ACTIVE)
        assert sub.is_active is True

    def test_is_active_false_when_status_pending(self) -> None:
        """is_active returns False when status is PENDING."""
        sub = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        assert sub.is_active is False


@pytest.mark.django_db
class TestGetOrCreateForEmail:
    """Tests for Subscriber.objects.get_or_create_for_email."""

    def test_creates_user_and_subscriber_on_first_call(self) -> None:
        """First call creates both a User and a Subscriber."""
        subscriber, created = Subscriber.objects.get_or_create_for_email(
            "newuser@example.com",
            defaults={"status": Subscriber.Status.PENDING},
        )
        assert created is True
        assert subscriber.user.email == "newuser@example.com"
        assert subscriber.user.username == "newuser@example.com"
        assert subscriber.status == Subscriber.Status.PENDING

    def test_idempotent_on_second_call(self) -> None:
        """Second call returns the existing Subscriber without creating a new one."""
        first, _ = Subscriber.objects.get_or_create_for_email("idempotent@example.com")
        second, created = Subscriber.objects.get_or_create_for_email(
            "idempotent@example.com"
        )
        assert created is False
        assert first.pk == second.pk

    def test_lowercases_email(self) -> None:
        """Email is normalised to lowercase before creating User and Subscriber."""
        subscriber, created = Subscriber.objects.get_or_create_for_email(
            "UPPER@EXAMPLE.COM",
            defaults={"status": Subscriber.Status.PENDING},
        )
        assert created is True
        assert subscriber.user.email == "upper@example.com"
        assert subscriber.user.username == "upper@example.com"

    def test_finds_existing_on_mixed_case_input(self) -> None:
        """Mixed-case email finds the existing lowercase subscriber."""
        first, _ = Subscriber.objects.get_or_create_for_email("case@example.com")
        second, created = Subscriber.objects.get_or_create_for_email("CASE@EXAMPLE.COM")
        assert created is False
        assert first.pk == second.pk


@pytest.mark.django_db
class TestSubscriberQuerySet:
    """Tests for SubscriberQuerySet custom methods."""

    def test_active_returns_only_active(self) -> None:
        active = SubscriberFactory.create(status=Subscriber.Status.ACTIVE)
        SubscriberFactory.create(status=Subscriber.Status.PENDING)
        result = Subscriber.objects.active()
        assert active in result
        assert result.count() == 1

    def test_active_excludes_pending(self) -> None:
        SubscriberFactory.create(status=Subscriber.Status.PENDING)
        result = Subscriber.objects.active()
        assert result.count() == 0

    def test_by_email_exact_match(self) -> None:
        """by_email finds a subscriber when the query is already lowercase."""
        sub = SubscriberFactory.create(user__email="test@example.com")
        result = Subscriber.objects.by_email("test@example.com")
        assert sub in result
        assert result.count() == 1

    def test_by_email_normalises_mixed_case_input(self) -> None:
        """by_email normalises the query to lowercase before filtering.

        Stored emails are always lowercase (form normalisation invariant);
        callers may pass mixed-case input and should still find the record.
        """
        sub = SubscriberFactory.create(user__email="test@example.com")
        result = Subscriber.objects.by_email("TEST@EXAMPLE.com")
        assert sub in result
        assert result.count() == 1

    def test_by_email_no_match(self) -> None:
        result = Subscriber.objects.by_email("nobody@example.com")
        assert result.count() == 0


@pytest.mark.django_db
class TestSubscriptionModel:
    """Tests for the Subscription model."""

    def test_str_returns_email_arrow_region(self) -> None:
        sub = SubscriptionFactory.create()
        expected = f"{sub.subscriber.user.email} → {sub.region.region_id}"
        assert str(sub) == expected

    def test_to_string_matches_str(self) -> None:
        sub = SubscriptionFactory.create()
        assert sub.to_string() == str(sub)

    def test_unique_together_constraint(self) -> None:
        from django.db import IntegrityError

        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        with pytest.raises(IntegrityError):
            SubscriptionFactory.create(subscriber=subscriber, region=region)

    def test_has_uuid(self) -> None:
        sub = SubscriptionFactory.create()
        assert sub.uuid is not None


@pytest.mark.django_db
class TestSubscriptionQuerySet:
    """Tests for SubscriptionQuerySet custom methods."""

    def test_for_subscriber_filters_correctly(self) -> None:
        subscriber = SubscriberFactory.create()
        other = SubscriberFactory.create()
        mine = SubscriptionFactory.create(subscriber=subscriber)
        SubscriptionFactory.create(subscriber=other)
        result = Subscription.objects.for_subscriber(subscriber)
        assert list(result) == [mine]

    def test_active_excludes_pending_subscribers(self) -> None:
        active_sub = SubscriberFactory.create(status=Subscriber.Status.ACTIVE)
        pending_sub = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        active_sn = SubscriptionFactory.create(subscriber=active_sub)
        SubscriptionFactory.create(subscriber=pending_sub)
        result = Subscription.objects.active()
        assert active_sn in result
        assert result.count() == 1

    def test_active_returns_empty_when_all_pending(self) -> None:
        pending_sub = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        SubscriptionFactory.create(subscriber=pending_sub)
        result = Subscription.objects.active()
        assert result.count() == 0


# ---------------------------------------------------------------------------
# Subscriber and Subscription request_log FKs (SNOW-277)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubscriberAcquisitionRequest:
    """Subscriber.acquisition_request FK defaults to None."""

    def test_acquisition_request_defaults_to_none(self) -> None:
        """Factory-created Subscriber has acquisition_request=None."""
        sub = SubscriberFactory.create()
        assert sub.acquisition_request is None

    def test_acquisition_request_can_be_set(self) -> None:
        """acquisition_request can be set to a RequestLog instance."""
        from tests.factories import RequestLogFactory

        req_log = RequestLogFactory.create()
        sub = SubscriberFactory.create(acquisition_request=req_log)
        sub.refresh_from_db()
        assert sub.acquisition_request_id == req_log.pk

    def test_acquisition_request_set_null_on_log_delete(self) -> None:
        """Deleting the RequestLog sets acquisition_request to None (SET_NULL)."""
        from tests.factories import RequestLogFactory

        req_log = RequestLogFactory.create()
        sub = SubscriberFactory.create(acquisition_request=req_log)
        req_log.delete()
        sub.refresh_from_db()
        assert sub.acquisition_request is None


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
        assert kinds == {"in_region", "in_neighbour", "elsewhere", "unknown"}


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


@pytest.mark.django_db
class TestPasskeyCredentialQuerySet:
    """Tests for PasskeyCredentialQuerySet custom methods."""

    def test_for_user_returns_correct_passkeys(self) -> None:
        sub_a = SubscriberFactory.create()
        sub_b = SubscriberFactory.create()
        pk_a = PasskeyCredentialFactory.create(user=sub_a.user)
        PasskeyCredentialFactory.create(user=sub_b.user)
        result = PasskeyCredential.objects.for_user(sub_a.user)
        assert list(result) == [pk_a]

    def test_by_credential_id_finds_exact_match(self) -> None:
        passkey = PasskeyCredentialFactory.create(credential_id="exact-cred-id")
        result = PasskeyCredential.objects.by_credential_id("exact-cred-id")
        assert passkey in result

    def test_by_credential_id_returns_empty_for_unknown(self) -> None:
        result = PasskeyCredential.objects.by_credential_id("does-not-exist")
        assert result.count() == 0


@pytest.mark.django_db
class TestSubscriberHasPasskeys:
    """Tests for Subscriber.has_passkeys()."""

    def test_returns_false_when_no_passkeys(self) -> None:
        sub = SubscriberFactory.create()
        assert sub.has_passkeys() is False

    def test_returns_true_when_passkey_exists(self) -> None:
        sub = SubscriberFactory.create()
        PasskeyCredentialFactory.create(user=sub.user)
        assert sub.has_passkeys() is True
