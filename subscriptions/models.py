"""
subscriptions/models.py — Database models for the subscriptions application.

Defines the following concrete models:

- Subscriber: a thin profile linked to Django's built-in User model via
  OneToOneField (related_name="subscriber").  Tracks the subscription
  lifecycle (pending / active) for an email address that has opted in to
  receive avalanche bulletin notifications.  Email is stored exclusively on
  User.email; ``Subscriber`` carries only domain-specific fields.
- Subscription: links a Subscriber to a specific MicroRegion so that
  notifications can be scoped to the regions the subscriber cares about.
- PasskeyCredential: a WebAuthn platform passkey registered by a Subscriber,
  storing the FIDO2 public key and metadata needed to verify future sign-ins.
- PushSubscription: a Web Push (VAPID) subscription for a Subscriber.

Keep business logic out of models — put it in subscriptions/services/ instead.
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models

from core.models import BaseModel
from subscriptions.aaguids import lookup as _aaguid_lookup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subscriber queryset / manager
# ---------------------------------------------------------------------------


class SubscriberQuerySet(models.QuerySet["Subscriber"]):
    """Custom queryset for Subscriber."""

    def active(self) -> SubscriberQuerySet:
        """Return only active subscribers."""
        return self.filter(status=Subscriber.Status.ACTIVE)

    def by_email(self, email: str) -> SubscriberQuerySet:
        """Return subscribers matching the given email (normalised to lowercase)."""
        return self.filter(user__email=email.lower())

    def get_or_create_for_email(
        self,
        email: str,
        *,
        defaults: dict | None = None,
    ) -> tuple[Subscriber, bool]:
        """Get or create a (User, Subscriber) pair for the given email address.

        Email is normalised to lowercase at this single entry point (Invariant 2).
        The User is created with ``username = email = email_lower`` so that
        ``auth.User``'s unique ``username`` field carries the uniqueness
        constraint.  If the User already exists, the Subscriber profile is
        looked up or created against that User.

        Args:
            email: Raw email address (may be mixed-case).
            defaults: Extra keyword arguments forwarded to the Subscriber
                get_or_create call (e.g. ``status``, ``acquisition_request``).

        Returns:
            ``(subscriber, created)`` — ``created`` is True when the Subscriber
            row was freshly created (the User may already have existed).

        """
        email_lower = email.strip().lower()
        User = get_user_model()  # noqa: N806 — conventional upper-case alias
        user, _user_created = User.objects.get_or_create(
            username=email_lower,
            defaults={"email": email_lower},
        )
        # Ensure email is always in sync even if the User pre-existed.
        if user.email != email_lower:
            user.email = email_lower
            user.save(update_fields=["email"])
        subscriber, created = Subscriber.objects.get_or_create(
            user=user,
            defaults=defaults or {},
        )
        return subscriber, created


class SubscriberManager(models.Manager["Subscriber"]):
    """Manager for Subscriber that exposes the SubscriberQuerySet methods."""

    def get_queryset(self) -> SubscriberQuerySet:
        """Return the custom queryset."""
        return SubscriberQuerySet(self.model, using=self._db)

    def active(self) -> SubscriberQuerySet:
        """Return only active subscribers."""
        return self.get_queryset().active()

    def by_email(self, email: str) -> SubscriberQuerySet:
        """Return subscribers matching the given email (normalised to lowercase)."""
        return self.get_queryset().by_email(email)

    def get_or_create_for_email(
        self,
        email: str,
        *,
        defaults: dict | None = None,
    ) -> tuple[Subscriber, bool]:
        """Get or create a (User, Subscriber) pair for the given email address.

        Delegates to SubscriberQuerySet.get_or_create_for_email.

        Args:
            email: Raw email address (may be mixed-case).
            defaults: Extra keyword arguments forwarded to the Subscriber
                get_or_create call.

        Returns:
            ``(subscriber, created)`` tuple.

        """
        return self.get_queryset().get_or_create_for_email(email, defaults=defaults)


# ---------------------------------------------------------------------------
# Subscriber
# ---------------------------------------------------------------------------


class Subscriber(BaseModel):
    """
    Profile model linked to Django's built-in User via OneToOneField.

    An email address subscribed to avalanche bulletin notifications.
    ``request.user`` is a plain ``auth.User``; the subscriber profile is
    accessed via ``request.user.subscriber`` (related_name="subscriber").

    Email is stored exclusively on ``User.email``.  Access it as
    ``subscriber.user.email``.  The ``status`` field tracks the subscription
    lifecycle:

    - ``pending``: address captured but not yet confirmed via a link click.
    - ``active``: confirmed at least once; ``is_active`` returns True.
    """

    class Status(models.TextChoices):
        """Lifecycle status for a Subscriber."""

        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriber",
    )
    acquisition_request = models.ForeignKey(
        "core.RequestLog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="acquired_subscribers",
        help_text=(
            "Request that first created this subscriber. "
            "First-observation wins; never overwritten."
        ),
    )
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

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    @property
    def is_active(self) -> bool:
        """Return True when the subscriber is confirmed (status=active)."""
        return self.status == self.Status.ACTIVE

    def to_string(self) -> str:
        """Return a human-readable representation."""
        return self.user.email

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
    subscribed_via = models.ForeignKey(
        "core.RequestLog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="initiated_subscriptions",
        help_text=(
            "Request that created this subscription. "
            "First-observation wins on the (subscriber, region) pair."
        ),
    )

    objects: SubscriptionQuerySet = SubscriptionQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        """Model metadata."""

        unique_together = [("subscriber", "region")]
        ordering = ["region__region_id"]

    def to_string(self) -> str:
        """Return a human-readable representation."""
        return f"{self.subscriber.user.email} → {self.region.region_id}"

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
        return f"{self.subscriber.user.email} — {self.display_name}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# PushSubscription (Web Push — spike)
# ---------------------------------------------------------------------------


class PushSubscriptionQuerySet(models.QuerySet["PushSubscription"]):
    """Custom queryset for PushSubscription."""

    def for_subscriber(self, subscriber: Subscriber) -> PushSubscriptionQuerySet:
        """Return all push subscriptions belonging to the given subscriber."""
        return self.filter(subscriber=subscriber)


class PushSubscription(models.Model):
    """
    A browser/device Web Push subscription registered for a Subscriber.

    Stores the three pieces returned by ``PushManager.subscribe()`` on the
    client: the endpoint URL (one of Apple/Mozilla/Google's push services),
    plus the P-256 ECDH ``p256dh`` key and HMAC ``auth`` secret used to
    encrypt the payload.

    For the spike we let ``subscriber`` be nullable so a staff tester on
    the ``/_push-demo/`` page (which uses ``staff_member_required``, not
    the regular subscriber session) can opt their device in without
    needing a regular subscriber account first.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
        null=True,
        blank=True,
    )
    endpoint = models.URLField(max_length=2048, unique=True)
    p256dh = models.CharField(max_length=128)
    auth = models.CharField(max_length=64)
    user_agent = models.CharField(max_length=512, blank=True, default="")
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects: PushSubscriptionQuerySet = PushSubscriptionQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        """Model metadata."""

        ordering = ["-created_at"]

    def to_dict(self) -> dict[str, object]:
        """Return the dict shape pywebpush expects as its first argument."""
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }

    def to_string(self) -> str:
        """Return a human-readable representation."""
        who = self.subscriber.user.email if self.subscriber else "anon"
        return f"{who} — {self.endpoint[:60]}…"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
