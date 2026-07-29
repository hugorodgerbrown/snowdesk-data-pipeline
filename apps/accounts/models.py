"""
apps/accounts/models.py — Database models for the accounts application.

Defines the following concrete models:

- Account: the single public-user identity, a thin profile linked to
  Django's built-in User model via OneToOneField (related_name="account").
  Auto-created at every public entry point (subscribe, sign-in, register).
  ``is_verified`` is the sole "email proven reachable" gate, set by every
  email-proving link.  Email is stored exclusively on User.email; ``Account``
  carries only domain-specific fields.
- Subscription: links an Account to a specific MicroRegion so that
  notifications can be scoped to the regions the account cares about.
- PasskeyCredential: a WebAuthn platform passkey registered by a User,
  storing the FIDO2 public key and metadata needed to verify future sign-ins.
- PushSubscription: a Web Push (VAPID) subscription for an Account.

Keep business logic out of models — put it in apps/accounts/services/ instead.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models

from apps.accounts.aaguids import lookup as _aaguid_lookup
from apps.core.models import BaseModel

if TYPE_CHECKING:
    from datetime import datetime

    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Account queryset / manager
# ---------------------------------------------------------------------------


class AccountQuerySet(models.QuerySet["Account"]):
    """Custom queryset for Account."""

    def verified(self) -> AccountQuerySet:
        """Return only accounts whose current email has been verified."""
        return self.filter(is_verified=True)

    def by_email(self, email: str) -> AccountQuerySet:
        """Return accounts matching the given email (normalised to lowercase)."""
        return self.filter(user__email=email.lower())

    def get_or_create_for_email(
        self,
        email: str,
        *,
        defaults: dict | None = None,
    ) -> tuple[Account, bool]:
        """Get or create a (User, Account) pair for the given email address.

        The email is normalised to lowercase (Invariant 2) and the User is
        created with ``username = email = email_lower`` so ``auth.User``'s
        unique ``username`` field carries the uniqueness constraint.  If the
        User already exists, the Account profile is looked up or created
        against that User — the single public-user identity, auto-created at
        every public entry point (subscribe, sign-in, register).

        Args:
            email: Raw email address (may be mixed-case).
            defaults: Extra keyword arguments forwarded to the Account
                get_or_create call.

        Returns:
            ``(account, created)`` — ``created`` is True when the Account row
            was freshly created (the User may already have existed, e.g. via
            the /account/ subscribe flow).

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
        account, created = Account.objects.get_or_create(
            user=user,
            defaults=defaults or {},
        )
        return account, created


class AccountManager(models.Manager["Account"]):
    """Manager for Account that exposes the AccountQuerySet methods."""

    def get_queryset(self) -> AccountQuerySet:
        """Return the custom queryset."""
        return AccountQuerySet(self.model, using=self._db)

    def verified(self) -> AccountQuerySet:
        """Return only accounts whose current email has been verified."""
        return self.get_queryset().verified()

    def by_email(self, email: str) -> AccountQuerySet:
        """Return accounts matching the given email (normalised to lowercase)."""
        return self.get_queryset().by_email(email)

    def get_or_create_for_email(
        self,
        email: str,
        *,
        defaults: dict | None = None,
    ) -> tuple[Account, bool]:
        """Get or create a (User, Account) pair for the given email address.

        Delegates to ``AccountQuerySet.get_or_create_for_email``.

        Args:
            email: Raw email address (may be mixed-case).
            defaults: Extra keyword arguments forwarded to the Account
                get_or_create call.

        Returns:
            ``(account, created)`` tuple.

        """
        return self.get_queryset().get_or_create_for_email(email, defaults=defaults)


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class Account(BaseModel):
    """
    Identity profile linked to Django's built-in User via OneToOneField.

    Registration (SNOW-430) treats users as first-class objects independent of
    any bulletin subscription: an ``Account`` may exist with no
    ``Subscription`` rows.  Access it via ``request.user.account``
    (related_name="account").

    ``is_verified`` records that the *current* email address has been proven
    reachable via a verification link.  It is deliberately distinct from
    ``User.is_active`` — ``is_active`` is the account kill switch, while
    ``is_verified`` gates actions that require a confirmed email (submitting
    field reports, and later favourites).  Email is stored exclusively on
    ``User.email``; access it as ``account.user.email``.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account",
    )
    acquisition_request = models.ForeignKey(
        "core.RequestLog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="acquired_accounts",
        help_text=(
            "Request that first created this account. "
            "First-observation wins; never overwritten."
        ),
    )
    is_verified = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True once the current email address has been verified.",
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of first successful email verification.",
    )
    display_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Optional display name captured at registration.",
    )
    pending_email = models.EmailField(
        null=True,
        blank=True,
        help_text=(
            "A new email address awaiting verification (SNOW-433). The account "
            "keeps its current (verified) address until this one is confirmed."
        ),
    )
    pending_email_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the pending email change was requested.",
    )

    objects = AccountManager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a human-readable representation."""
        return self.user.email

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()

    def mark_verified(self, now: datetime) -> Account:
        """Mark this account's current email as verified (in-memory only).

        Sets ``is_verified`` True and stamps ``verified_at`` on first
        verification (idempotent — a re-verification does not overwrite the
        original timestamp).  Mutates and returns ``self`` without saving;
        the caller (a service or view) owns the ``save()`` and the login
        side-effect, per the house model/service split.

        Args:
            now: Timezone-aware "now" used to stamp ``verified_at``.

        Returns:
            ``self`` (for chaining).

        """
        self.is_verified = True
        if self.verified_at is None:
            self.verified_at = now
        return self

    def request_email_change(self, new_email: str, now: datetime) -> Account:
        """Record a pending email change (in-memory only); the caller saves.

        The pending address is not applied to ``User.username`` / ``User.email``
        until it is confirmed via the emailed link (SNOW-433).

        Args:
            new_email: The requested new address (stored lowercased).
            now: Timezone-aware "now" stamped on the request.

        Returns:
            ``self`` (for chaining).

        """
        self.pending_email = new_email.strip().lower()
        self.pending_email_requested_at = now
        return self

    def clear_pending_email(self) -> Account:
        """Clear the pending email slot (in-memory only); the caller saves."""
        self.pending_email = None
        self.pending_email_requested_at = None
        return self


def user_is_verified(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Return True when ``user`` has a verified ``Account`` profile.

    Verified-only actions — submitting a field observation (SNOW-430), and
    favourites later — require a confirmed email address.  An anonymous user,
    an authenticated user with no ``Account`` row, and an unverified account
    all return False.

    This is the single definition shared by the server-side gate in
    ``apps/observations/views.py`` (``_auth_gate``) and the client-eligibility flag
    built in ``apps/public/views.py`` (``_report_context``).  Keeping one function
    stops the two from drifting — the drift between them was the root cause of
    SNOW-477, where an authenticated-but-unverified user was marked eligible
    client-side but 403'd server-side.

    Args:
        user: The ``request.user`` to check.

    Returns:
        True only when ``user`` is authenticated and its ``Account`` has
        ``is_verified=True``.

    """
    if not user.is_authenticated:
        return False
    try:
        account = user.account  # type: ignore[attr-defined]
    except Account.DoesNotExist:
        return False
    return bool(account.is_verified)


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


class SubscriptionQuerySet(models.QuerySet["Subscription"]):
    """Custom queryset for Subscription."""


class Subscription(models.Model):
    """
    Links an Account to an SLF warning Region.

    An account may have many subscriptions, one per region of interest.
    The unique_together constraint prevents duplicate account/region pairs.

    ``geo_match_kind`` and ``geo_matched_region`` record how the account's
    geolocation (read from ``subscribed_via``) relates to the subscribed region
    at the moment of sign-up.  These fields are frozen on INSERT; they are
    never updated after the row is created.  Raw geo and language fields live
    on ``subscribed_via`` (a ``RequestLog``); only the region-relative
    classification is stored here because ``RequestLog`` is region-agnostic.
    """

    class GeoMatchKind(models.TextChoices):
        """Region-relative classification of the account's geolocation.

        Literal values must stay in sync with the constants in
        ``apps.regions.services.point_match``.  A unit test in
        ``tests/regions/services/test_point_match.py`` guards against drift.
        """

        IN_REGION = "in_region", "In region"
        IN_NEIGHBOUR = "in_neighbour", "In neighbouring region"
        ELSEWHERE = "elsewhere", "Elsewhere"
        UNKNOWN = "unknown", "Unknown"

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    account = models.ForeignKey(
        Account,
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
            "First-observation wins on the (account, region) pair."
        ),
    )
    geo_match_kind = models.CharField(
        max_length=16,
        choices=GeoMatchKind.choices,
        default=GeoMatchKind.UNKNOWN,
        db_index=True,
        help_text=(
            "How the account's geolocation (from subscribed_via) relates to "
            "the subscribed region at sign-up time. Frozen on INSERT; never "
            "updated. Raw geo fields live on subscribed_via (RequestLog)."
        ),
    )
    geo_matched_region = models.ForeignKey(
        "regions.MicroRegion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=(
            "The specific MicroRegion the account's geolocation fell inside "
            "(target region itself, or the first matching neighbour). Null when "
            "geo_match_kind is elsewhere or unknown. Analytics-only; not surfaced "
            "on the public region API."
        ),
    )

    objects: SubscriptionQuerySet = SubscriptionQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        """Model metadata."""

        unique_together = [("account", "region")]
        ordering = ["region__region_id"]

    def to_string(self) -> str:
        """Return a human-readable representation."""
        return f"{self.account.user.email} → {self.region.region_id}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# PasskeyCredential
# ---------------------------------------------------------------------------


class PasskeyCredentialQuerySet(models.QuerySet["PasskeyCredential"]):
    """Custom queryset for PasskeyCredential."""

    def for_user(self, user: "User") -> PasskeyCredentialQuerySet:
        """Return all passkeys belonging to the given auth.User."""
        return self.filter(user=user)

    def by_credential_id(self, credential_id: str) -> PasskeyCredentialQuerySet:
        """Return passkeys matching the given base64url credential ID."""
        return self.filter(credential_id=credential_id)


class PasskeyCredential(models.Model):
    """
    A WebAuthn platform passkey registered by a User (auth.User).

    Stores the FIDO2 public key and metadata needed to verify future sign-ins.
    A user may register multiple passkeys — one per device.

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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
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
        return f"{self.user.email} — {self.display_name}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# PushSubscription (Web Push — spike)
# ---------------------------------------------------------------------------


class PushSubscriptionQuerySet(models.QuerySet["PushSubscription"]):
    """Custom queryset for PushSubscription."""

    def active(self) -> PushSubscriptionQuerySet:
        """Return only subscriptions that have not been marked inactive.

        A row is marked inactive (``inactive_at`` set) when the push service
        returns 410 Gone — the subscription is soft-deleted rather than
        dropped so the client-side re-verification loop (SNOW-380) has a
        record to reconcile against.
        """
        return self.filter(inactive_at__isnull=True)


class PushSubscription(models.Model):
    """
    A browser/device Web Push subscription registered for an Account.

    Stores the three pieces returned by ``PushManager.subscribe()`` on the
    client: the endpoint URL (one of Apple/Mozilla/Google's push services),
    plus the P-256 ECDH ``p256dh`` key and HMAC ``auth`` secret used to
    encrypt the payload.

    For the spike we let ``account`` be nullable so a staff tester on
    the ``/_push-demo/`` page (which uses ``staff_member_required``, not
    the regular account session) can opt their device in without
    needing a regular account first.

    ``mechanism`` records which browser API produced the subscription: the
    ``sw`` (service-worker parsed) path used by every browser today, or the
    ``declarative`` (Apple's Declarative Web Push, iOS 18.4+) path where the
    OS itself renders the notification from a fixed payload shape without
    running JS. ``dispatch_push`` branches on this field to build the
    correct payload.

    ``inactive_at`` is set when the push service reports the subscription
    is gone (410) rather than deleting the row outright — see
    ``apps/accounts/push_service.py::dispatch_push``. A 404 (rarer, usually
    a transport-layer error) still hard-deletes the row.
    """

    class Mechanism(models.TextChoices):
        """Which browser API produced this subscription."""

        SW = "sw", "Service worker"
        DECLARATIVE = "declarative", "Declarative Web Push"

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    account = models.ForeignKey(
        Account,
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
    mechanism = models.CharField(
        max_length=16,
        choices=Mechanism.choices,
        default=Mechanism.SW,
        help_text="Which browser API produced this subscription (sw or declarative).",
    )
    inactive_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the push service reports 410 Gone. Null while live.",
    )

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
        who = self.account.user.email if self.account else "anon"
        return f"{who} — {self.endpoint[:60]}…"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
