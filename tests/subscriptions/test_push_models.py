"""
tests/subscriptions/test_push_models.py — Tests for the PushSubscription model.

Covers:
  - Factory produces a valid PushSubscription.
  - Uniqueness constraint on endpoint.
  - to_dict() returns the pywebpush-shaped dict.
  - to_string() is non-empty for both anonymous (null subscriber) and
    subscriber-owned rows.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from subscriptions.models import PushSubscription
from tests.factories import PushSubscriptionFactory, SubscriberFactory


@pytest.mark.django_db
class TestPushSubscriptionModel:
    """Unit tests for the PushSubscription model."""

    def test_factory_creates_valid_instance(self) -> None:
        """PushSubscriptionFactory produces a saved, valid PushSubscription."""
        sub = PushSubscriptionFactory.create()
        assert sub.pk is not None
        assert sub.endpoint
        assert sub.p256dh
        assert sub.auth
        assert sub.uuid is not None

    def test_endpoint_uniqueness(self) -> None:
        """Two rows with the same endpoint raise IntegrityError."""
        first = PushSubscriptionFactory.create()
        with pytest.raises(IntegrityError):
            PushSubscriptionFactory.create(endpoint=first.endpoint)

    def test_to_dict_returns_pywebpush_shape(self) -> None:
        """to_dict() matches the subscription_info dict pywebpush expects."""
        sub = PushSubscriptionFactory.create()
        result = sub.to_dict()
        assert result == {
            "endpoint": sub.endpoint,
            "keys": {
                "p256dh": sub.p256dh,
                "auth": sub.auth,
            },
        }

    def test_to_string_anon_row(self) -> None:
        """to_string() on a null-subscriber row shows '(anon)'-style text."""
        sub = PushSubscriptionFactory.create(subscriber=None)
        s = sub.to_string()
        assert s
        assert "anon" in s

    def test_to_string_subscriber_owned(self) -> None:
        """to_string() on a subscriber-owned row shows the subscriber email."""
        subscriber = SubscriberFactory.create()
        sub = PushSubscriptionFactory.create(subscriber=subscriber)
        s = sub.to_string()
        assert s
        assert subscriber.email in s

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string()."""
        sub = PushSubscriptionFactory.create()
        assert str(sub) == sub.to_string()

    def test_ordering_most_recent_first(self) -> None:
        """Default ordering is -created_at (most recent first)."""
        first = PushSubscriptionFactory.create()
        second = PushSubscriptionFactory.create()
        rows = list(PushSubscription.objects.all())
        assert rows[0].pk == second.pk
        assert rows[1].pk == first.pk
