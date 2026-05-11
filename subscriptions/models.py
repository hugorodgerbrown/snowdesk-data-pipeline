"""
subscriptions/models.py — Database models for the subscriptions application.

Defines three concrete models:
  - Subscriber: an email address that has opted in to receive avalanche
    bulletin notifications. Tracks subscription status and confirmation time.
  - Subscription: links a Subscriber to a specific SLF Region so that
    notifications can be scoped to the regions the subscriber cares about.
  - PasskeyCredential: a WebAuthn platform passkey registered by a Subscriber,
    storing the FIDO2 public key and metadata needed to verify future sign-ins.

Keep business logic out of models — put it in subscriptions/services/ instead.
"""

from __future__ import annotations

import logging

from django.db import models

from core.models import BaseModel
from subscriptions.aaguids import lookup as _aaguid_lookup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subscriber
# ---------------------------------------------------------------------------


class SubscriberQuerySet(models.QuerySet["Subscriber"]):
    """Custom queryset for Subscriber."""

    def active(self) -> SubscriberQuerySet:
        """Return only active subscribers."""
        return self.filter(status=Subscriber.Status.ACTIVE)

    def by_email(self, email: str) -> SubscriberQuerySet:
        """Return subscribers matching the given email (case-insensitive)."""
        return self.filter(email__iexact=email)


class Subscriber(BaseModel):
    """
    An email address subscribed to avalanche bulletin notifications.

    Each subscriber has a unique email address and may have zero or more
    Subscription records linking them to specific SLF warning regions.

    The ``status`` field tracks the lifecycle of the subscription:
    - ``pending``: address captured but not yet confirmed via a link click.
    - ``active``: confirmed at least once; receives bulletin emails.
    """

    class Status(models.TextChoices):
        """Lifecycle status for a Subscriber."""

        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"

    email = models.EmailField(unique=True, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of first account-link verification.",
    )

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

    def has_passkeys(self) -> bool:
        """Return True if this subscriber has at least one registered passkey."""
        return self.passkeys.exists()


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


class SubscriptionQuerySet(models.QuerySet["Subscription"]):
    """Custom queryset for Subscription."""

    def for_subscriber(self, subscriber: Subscriber) -> SubscriptionQuerySet:
        """Return all subscriptions belonging to the given subscriber."""
        return self.filter(subscriber=subscriber)

    def active(self) -> SubscriptionQuerySet:
        """Return subscriptions whose subscriber is active."""
        return self.filter(subscriber__status=Subscriber.Status.ACTIVE)


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
        "regions.MicroRegion",
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


# ---------------------------------------------------------------------------
# PasskeyCredential
# ---------------------------------------------------------------------------


class PasskeyCredentialQuerySet(models.QuerySet["PasskeyCredential"]):
    """Custom queryset for PasskeyCredential."""

    def for_subscriber(self, subscriber: Subscriber) -> PasskeyCredentialQuerySet:
        """Return all passkeys belonging to the given subscriber."""
        return self.filter(subscriber=subscriber)

    def by_credential_id(self, credential_id: str) -> PasskeyCredentialQuerySet:
        """Return passkeys matching the given base64url credential ID."""
        return self.filter(credential_id=credential_id)


class PasskeyCredential(BaseModel):
    """
    A WebAuthn platform passkey registered by a Subscriber.

    Stores the FIDO2 public key and metadata needed to verify future sign-ins.
    A subscriber may register multiple passkeys \u2014 one per device.

    ``credential_id`` is the base64url-encoded credential identifier returned
    by the browser's WebAuthn API and is used as the lookup key during
    authentication.

    ``public_key`` is the raw COSE-encoded public key bytes stored as binary.

    ``sign_count`` is incremented on every successful authentication; a
    decreasing counter signals a cloned authenticator.

    ``aaguid`` identifies the passkey provider (e.g. iCloud Keychain) and is
    stored for future display-name lookup; it is not used in v1.

    ``device_type`` is ``"platform"`` for Touch ID / Face ID / Windows Hello
    and ``"cross-platform"`` for roaming authenticators (hardware keys, etc.).
    """

    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.CASCADE,
        related_name="passkeys",
    )
    credential_id = models.TextField(unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveIntegerField(default=0)
    aaguid = models.UUIDField(
        null=True,
        blank=True,
        help_text="Reserved for future AAGUID provider name lookup.",
    )
    name = models.CharField(
        max_length=255,
        help_text="Human-readable label shown on the manage page (auto-generated).",
    )
    device_type = models.CharField(
        max_length=32,
        help_text='"platform" or "cross-platform".',
    )
    backed_up = models.BooleanField(
        default=False,
        help_text="True when the passkey is synced to the cloud.",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = PasskeyCredentialQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    @property
    def display_name(self) -> str:
        """Return the provider name from AAGUID lookup, or fall back to stored name.

        Retroactively corrects generic auto-generated names (e.g. "Synced passkey")
        for passkeys whose AAGUID has since been added to the lookup table.
        """
        provider = _aaguid_lookup(self.aaguid)
        if provider:
            date_str = self.created_at.strftime("%-d %b %Y")
            return f"{provider} \u2014 {date_str}"
        return self.name

    def to_string(self) -> str:
        """Return a human-readable representation."""
        return f"{self.subscriber.email} \u2014 {self.display_name}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
