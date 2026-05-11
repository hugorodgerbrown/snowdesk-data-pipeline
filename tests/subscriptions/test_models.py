"""
tests/subscriptions/test_models.py — Tests for subscriptions models.

Covers Subscriber, Subscription, and PasskeyCredential model behaviour,
queryset methods, string representations, and field constraints.
"""

import uuid

import pytest

from subscriptions.aaguids import lookup as aaguid_lookup
from subscriptions.models import PasskeyCredential, Subscriber, Subscription
from tests.factories import (
    MicroRegionFactory,
    PasskeyCredentialFactory,
    SubscriberFactory,
    SubscriptionFactory,
)


class TestAaguidLookup:
    """Tests for subscriptions.aaguids.lookup."""

    def test_returns_name_for_known_aaguid(self):
        assert (
            aaguid_lookup(uuid.UUID("bada5566-a7aa-401f-bd96-45619a55120d"))
            == "1Password"
        )

    def test_returns_none_for_unknown_aaguid(self):
        assert aaguid_lookup(uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")) is None

    def test_returns_none_for_null_aaguid(self):
        assert aaguid_lookup(None) is None


@pytest.mark.django_db
class TestSubscriberModel:
    """Tests for the Subscriber model."""

    def test_str_returns_email(self):
        sub = SubscriberFactory.create(email="alice@example.com")
        assert str(sub) == "alice@example.com"

    def test_to_string_returns_email(self):
        sub = SubscriberFactory.create(email="bob@example.com")
        assert sub.to_string() == "bob@example.com"

    def test_default_status_is_pending_on_fresh_create(self):
        sub = Subscriber.objects.create(email="fresh@example.com")
        assert sub.status == Subscriber.Status.PENDING

    def test_factory_default_status_is_active(self):
        sub = SubscriberFactory.create()
        assert sub.status == Subscriber.Status.ACTIVE

    def test_confirmed_at_nullable(self):
        sub = SubscriberFactory.create()
        sub.confirmed_at = None
        sub.save(update_fields=["confirmed_at"])
        sub.refresh_from_db()
        assert sub.confirmed_at is None

    def test_email_is_unique(self):
        from django.db import IntegrityError

        SubscriberFactory.create(email="unique@example.com")
        with pytest.raises(IntegrityError):
            SubscriberFactory.create(email="unique@example.com")

    def test_has_uuid(self):
        sub = SubscriberFactory.create()
        assert sub.uuid is not None

    def test_has_created_at(self):
        sub = SubscriberFactory.create()
        assert sub.created_at is not None

    def test_status_choices(self):
        assert Subscriber.Status.PENDING == "pending"
        assert Subscriber.Status.ACTIVE == "active"

    def test_pending_status_persists(self):
        sub = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        sub.refresh_from_db()
        assert sub.status == Subscriber.Status.PENDING


@pytest.mark.django_db
class TestSubscriberQuerySet:
    """Tests for SubscriberQuerySet custom methods."""

    def test_active_returns_only_active(self):
        active = SubscriberFactory.create(status=Subscriber.Status.ACTIVE)
        SubscriberFactory.create(status=Subscriber.Status.PENDING)
        result = Subscriber.objects.active()
        assert active in result
        assert result.count() == 1

    def test_active_excludes_pending(self):
        SubscriberFactory.create(status=Subscriber.Status.PENDING)
        result = Subscriber.objects.active()
        assert result.count() == 0

    def test_by_email_case_insensitive(self):
        sub = SubscriberFactory.create(email="Test@Example.COM")
        result = Subscriber.objects.by_email("test@example.com")
        assert sub in result
        assert result.count() == 1

    def test_by_email_no_match(self):
        result = Subscriber.objects.by_email("nobody@example.com")
        assert result.count() == 0


@pytest.mark.django_db
class TestSubscriptionModel:
    """Tests for the Subscription model."""

    def test_str_returns_email_arrow_region(self):
        sub = SubscriptionFactory.create()
        expected = f"{sub.subscriber.email} \u2192 {sub.region.region_id}"
        assert str(sub) == expected

    def test_to_string_matches_str(self):
        sub = SubscriptionFactory.create()
        assert sub.to_string() == str(sub)

    def test_unique_together_constraint(self):
        from django.db import IntegrityError

        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        with pytest.raises(IntegrityError):
            SubscriptionFactory.create(subscriber=subscriber, region=region)

    def test_has_uuid(self):
        sub = SubscriptionFactory.create()
        assert sub.uuid is not None


@pytest.mark.django_db
class TestSubscriptionQuerySet:
    """Tests for SubscriptionQuerySet custom methods."""

    def test_for_subscriber_filters_correctly(self):
        subscriber = SubscriberFactory.create()
        other = SubscriberFactory.create()
        mine = SubscriptionFactory.create(subscriber=subscriber)
        SubscriptionFactory.create(subscriber=other)
        result = Subscription.objects.for_subscriber(subscriber)
        assert list(result) == [mine]

    def test_active_excludes_pending_subscribers(self):
        active_sub = SubscriberFactory.create(status=Subscriber.Status.ACTIVE)
        pending_sub = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        active_sn = SubscriptionFactory.create(subscriber=active_sub)
        SubscriptionFactory.create(subscriber=pending_sub)
        result = Subscription.objects.active()
        assert active_sn in result
        assert result.count() == 1

    def test_active_returns_empty_when_all_pending(self):
        pending_sub = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        SubscriptionFactory.create(subscriber=pending_sub)
        result = Subscription.objects.active()
        assert result.count() == 0


# ---------------------------------------------------------------------------
# PasskeyCredential
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyCredentialModel:
    """Tests for the PasskeyCredential model."""

    def test_str_returns_email_and_name(self):
        passkey = PasskeyCredentialFactory.create(name="My passkey")
        assert "My passkey" in str(passkey)
        assert passkey.subscriber.email in str(passkey)

    def test_to_string_matches_str(self):
        passkey = PasskeyCredentialFactory.create()
        assert passkey.to_string() == str(passkey)

    def test_has_uuid(self):
        passkey = PasskeyCredentialFactory.create()
        assert passkey.uuid is not None

    def test_has_created_at(self):
        passkey = PasskeyCredentialFactory.create()
        assert passkey.created_at is not None

    def test_credential_id_is_unique(self):
        from django.db import IntegrityError

        PasskeyCredentialFactory.create(credential_id="unique-cred")
        with pytest.raises(IntegrityError):
            PasskeyCredentialFactory.create(credential_id="unique-cred")

    def test_cascade_deletes_with_subscriber(self):
        passkey = PasskeyCredentialFactory.create()
        pk = passkey.pk
        passkey.subscriber.delete()
        assert not PasskeyCredential.objects.filter(pk=pk).exists()

    def test_default_sign_count_is_zero(self):
        passkey = PasskeyCredentialFactory.create(sign_count=0)
        assert passkey.sign_count == 0

    def test_backed_up_default_false(self):
        passkey = PasskeyCredentialFactory.create()
        assert passkey.backed_up is False

    def test_last_used_at_nullable(self):
        passkey = PasskeyCredentialFactory.create(last_used_at=None)
        assert passkey.last_used_at is None

    def test_aaguid_nullable(self):
        passkey = PasskeyCredentialFactory.create(aaguid=None)
        assert passkey.aaguid is None

    def test_display_name_uses_aaguid_lookup_when_known(self):
        _1password_aaguid = uuid.UUID("bada5566-a7aa-401f-bd96-45619a55120d")
        passkey = PasskeyCredentialFactory.create(
            aaguid=_1password_aaguid,
            name="Synced passkey — 1 Jan 2025",
        )
        assert passkey.display_name.startswith("1Password — ")

    def test_display_name_falls_back_to_stored_name_for_unknown_aaguid(self):
        unknown_aaguid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        passkey = PasskeyCredentialFactory.create(
            aaguid=unknown_aaguid,
            name="Device passkey — 1 Jan 2025",
        )
        assert passkey.display_name == "Device passkey — 1 Jan 2025"

    def test_display_name_falls_back_to_stored_name_when_aaguid_is_none(self):
        passkey = PasskeyCredentialFactory.create(
            aaguid=None,
            name="Synced passkey — 1 Jan 2025",
        )
        assert passkey.display_name == "Synced passkey — 1 Jan 2025"


@pytest.mark.django_db
class TestPasskeyCredentialQuerySet:
    """Tests for PasskeyCredentialQuerySet custom methods."""

    def test_for_subscriber_returns_correct_passkeys(self):
        sub_a = SubscriberFactory.create()
        sub_b = SubscriberFactory.create()
        pk_a = PasskeyCredentialFactory.create(subscriber=sub_a)
        PasskeyCredentialFactory.create(subscriber=sub_b)
        result = PasskeyCredential.objects.for_subscriber(sub_a)
        assert list(result) == [pk_a]

    def test_by_credential_id_finds_exact_match(self):
        passkey = PasskeyCredentialFactory.create(credential_id="exact-cred-id")
        result = PasskeyCredential.objects.by_credential_id("exact-cred-id")
        assert passkey in result

    def test_by_credential_id_returns_empty_for_unknown(self):
        result = PasskeyCredential.objects.by_credential_id("does-not-exist")
        assert result.count() == 0


@pytest.mark.django_db
class TestSubscriberHasPasskeys:
    """Tests for Subscriber.has_passkeys()."""

    def test_returns_false_when_no_passkeys(self):
        sub = SubscriberFactory.create()
        assert sub.has_passkeys() is False

    def test_returns_true_when_passkey_exists(self):
        sub = SubscriberFactory.create()
        PasskeyCredentialFactory.create(subscriber=sub)
        assert sub.has_passkeys() is True
