"""
subscriptions/models.py — Database models for the subscriptions application.

Defines two concrete models:
  - Subscriber: an email address that has opted in to receive avalanche
    bulletin notifications. Tracks active status and last authentication time.
  - Subscription: links a Subscriber to a specific SLF Region so that
    notifications can be scoped to the regions the subscriber cares about.

Keep business logic out of models — put it in subscriptions/services/ instead.
"""

from __future__ import annotations

import logging

from django.db import models

from pipeline.models import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subscriber
# ---------------------------------------------------------------------------


class SubscriberQuerySet(models.QuerySet):
    """Custom queryset for Subscriber."""

    def active(self) -> SubscriberQuerySet:
        """Return only active subscribers."""
        return self.filter(is_active=True)

    def by_email(self, email: str) -> SubscriberQuerySet:
        """Return subscribers matching the given email (case-insensitive)."""
        return self.filter(email__iexact=email)


class Subscriber(BaseModel):
    """
    An email address subscribed to avalanche bulletin notifications.

    Each subscriber has a unique email address and may have zero or more
    Subscription records linking them to specific SLF warning regions.
    """

    email = models.EmailField(unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    last_authenticated_at = models.DateTimeField(null=True, blank=True)

    objects = SubscriberQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a human-readable representation."""
        return self.email

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


class SubscriptionQuerySet(models.QuerySet):
    """Custom queryset for Subscription."""

    def for_subscriber(self, subscriber: Subscriber) -> SubscriptionQuerySet:
        """Return all subscriptions belonging to the given subscriber."""
        return self.filter(subscriber=subscriber)

    def active(self) -> SubscriptionQuerySet:
        """Return subscriptions whose subscriber is active."""
        return self.filter(subscriber__is_active=True)


class Subscription(BaseModel):
    """
    Links a Subscriber to an SLF warning Region.

    A subscriber may have many subscriptions, one per region of interest.
    The unique_together constraint prevents duplicate subscriber/region pairs.
    """

    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    region = models.ForeignKey(
        "pipeline.Region",
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )

    objects = SubscriptionQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        unique_together = [("subscriber", "region")]
        ordering = ["region__region_id"]

    def to_string(self) -> str:
        """Return a human-readable representation."""
        return f"{self.subscriber.email} \u2192 {self.region.region_id}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
