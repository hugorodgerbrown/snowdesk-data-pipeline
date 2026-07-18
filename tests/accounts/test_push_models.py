"""
tests/accounts/test_push_models.py — Tests for the PushSubscription model.

Covers:
  - Factory produces a valid PushSubscription.
  - Uniqueness constraint on endpoint.
  - to_dict() returns the pywebpush-shaped dict.
  - to_string() is non-empty for both anonymous (null subscriber) and
    subscriber-owned rows.
  - mechanism default and choices validation (SNOW-380).
  - inactive_at default and PushSubscription.objects.active() (SNOW-380).
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from accounts.models import PushSubscription
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
        assert subscriber.user.email in s

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


# ---------------------------------------------------------------------------
# SNOW-380 — mechanism + inactive_at fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPushSubscriptionMechanismField:
    """SNOW-380: mechanism default and choices validation."""

    def test_mechanism_defaults_to_sw(self) -> None:
        """A freshly created row defaults to the 'sw' mechanism."""
        sub = PushSubscriptionFactory.create()
        assert sub.mechanism == PushSubscription.Mechanism.SW

    def test_mechanism_accepts_declarative(self) -> None:
        """The 'declarative' choice is a valid, saveable value."""
        sub = PushSubscriptionFactory.create(
            mechanism=PushSubscription.Mechanism.DECLARATIVE
        )
        sub.refresh_from_db()
        assert sub.mechanism == PushSubscription.Mechanism.DECLARATIVE

    def test_mechanism_rejects_arbitrary_string(self) -> None:
        """full_clean() rejects a mechanism value outside the choices."""
        sub = PushSubscriptionFactory.create(mechanism="carrier-pigeon")
        with pytest.raises(ValidationError):
            sub.full_clean()


@pytest.mark.django_db
class TestPushSubscriptionInactiveAtField:
    """SNOW-380: inactive_at default and the active() queryset."""

    def test_inactive_at_defaults_to_none(self) -> None:
        """A freshly created row has inactive_at=None."""
        sub = PushSubscriptionFactory.create()
        assert sub.inactive_at is None

    def test_active_excludes_inactive_rows(self) -> None:
        """PushSubscription.objects.active() excludes rows with inactive_at set."""
        live = PushSubscriptionFactory.create()
        dead = PushSubscriptionFactory.create(inactive_at=timezone.now())
        active_pks = set(PushSubscription.objects.active().values_list("pk", flat=True))
        assert live.pk in active_pks
        assert dead.pk not in active_pks
