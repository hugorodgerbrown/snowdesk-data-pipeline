"""
subscriptions/models.py — Database models for the subscriptions application.

Defines three concrete models:
  - Subscriber: the custom Django user model.  An email address that has opted
    in to receive avalanche bulletin notifications.  Extends AbstractBaseUser
    and PermissionsMixin so that a single identity covers both subscribers and
    staff — request.user always refers to a Subscriber (or AnonymousUser).
  - Subscription: links a Subscriber to a specific SLF Region so that
    notifications can be scoped to the regions the subscriber cares about.
  - PasskeyCredential: a WebAuthn platform passkey registered by a Subscriber,
    storing the FIDO2 public key and metadata needed to verify future sign-ins.

Keep business logic out of models — put it in subscriptions/services/ instead.
"""

from __future__ import annotations

import logging
import uuid
from typing import ClassVar

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.contrib.auth.password_validation import validate_password
from django.db import models

from subscriptions.aaguids import lookup as _aaguid_lookup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subscriber manager
# ---------------------------------------------------------------------------


class SubscriberQuerySet(models.QuerySet["Subscriber"]):
    """Custom queryset for Subscriber."""

    def active(self) -> SubscriberQuerySet:
        """Return only active subscribers."""
        return self.filter(status=Subscriber.Status.ACTIVE)

    def by_email(self, email: str) -> SubscriberQuerySet:
        """Return subscribers matching the given email (case-insensitive)."""
        return self.filter(email__iexact=email)


class SubscriberManager(BaseUserManager.from_queryset(SubscriberQuerySet)):  # type: ignore[misc]
    """Custom manager for Subscriber.

    Combines BaseUserManager with SubscriberQuerySet.
    """

    def create_user(
        self, email: str, password: str | None = None, **extra_fields: object
    ) -> Subscriber:
        """Create and return a subscriber with an unusable password by default."""
        email = self.normalize_email(email)
        user: Subscriber = self.model(email=email, **extra_fields)
        if password:
            validate_password(password, user=user)
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields: object
    ) -> Subscriber:
        """Create and return a superuser (staff=True, superuser=True, status=active)."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("status", "active")
        return self.create_user(email, password, **extra_fields)


# ---------------------------------------------------------------------------
# Subscriber
# ---------------------------------------------------------------------------


class Subscriber(AbstractBaseUser, PermissionsMixin):
    """
    The Snowdesk custom user model.

    An email address subscribed to avalanche bulletin notifications.
    Replaces Django's default User so that request.user is always a Subscriber
    instance (or AnonymousUser).  Staff and superusers are Subscribers with
    is_staff=True / is_superuser=True plus a usable password.

    Regular subscribers authenticate via magic-link email or passkeys;
    their password field is always set to an unusable hash.

    The ``status`` field tracks the subscription lifecycle:
    - ``pending``: address captured but not yet confirmed via a link click.
    - ``active``: confirmed at least once; is_active returns True.
    """

    class Status(models.TextChoices):
        """Lifecycle status for a Subscriber."""

        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"

    # Standard fields inlined from BaseModel (can't mix BaseModel + AbstractBaseUser
    # without complex MRO; explicit fields are clearer here).
    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Domain fields
    email = models.EmailField(unique=True, db_index=True)
    is_staff = models.BooleanField(default=False)
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

    objects = SubscriberManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        """Model metadata."""

        ordering = ["-created_at"]

    @property
    def is_active(self) -> bool:
        """Return True when the subscriber is confirmed (status=active)."""
        return self.status == self.Status.ACTIVE

    @is_active.setter
    def is_active(self, value: bool) -> None:
        """No-op setter — status field is the authoritative source of truth."""

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


class Subscription(models.Model):
    """
    Links a Subscriber to an SLF warning Region.

    A subscriber may have many subscriptions, one per region of interest.
    The unique_together constraint prevents duplicate subscriber/region pairs.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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

    objects: SubscriptionQuerySet = SubscriptionQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        """Model metadata."""

        unique_together = [("subscriber", "region")]
        ordering = ["region__region_id"]

    def to_string(self) -> str:
        """Return a human-readable representation."""
        return f"{self.subscriber.email} → {self.region.region_id}"

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


class PasskeyCredential(models.Model):
    """
    A WebAuthn platform passkey registered by a Subscriber.

    Stores the FIDO2 public key and metadata needed to verify future sign-ins.
    A subscriber may register multiple passkeys — one per device.

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

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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

    objects: PasskeyCredentialQuerySet = PasskeyCredentialQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
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
            return f"{provider} — {date_str}"
        return self.name

    def to_string(self) -> str:
        """Return a human-readable representation."""
        return f"{self.subscriber.email} — {self.display_name}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
