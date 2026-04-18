"""
tests/subscriptions/test_models.py — Tests for subscriptions models.

Covers Subscriber and Subscription model behaviour, queryset methods,
string representations, field constraints, and the new status/confirmed_at
fields introduced in migration 0002.
"""

import pytest

from subscriptions.models import Subscriber, Subscription
from tests.factories import RegionFactory, SubscriberFactory, SubscriptionFactory


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
        region = RegionFactory.create()
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
