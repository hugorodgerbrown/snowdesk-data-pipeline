"""
subscriptions/admin.py — Django admin registrations for subscriptions models.

Provides list and detail views for Subscriber, Subscription, PasskeyCredential,
and PushSubscription records so that operators can inspect and manage newsletter
subscriptions, registered passkeys, and Web Push subscriptions without direct
database access.

Subscriber uses UserAdmin as the base so password management works for staff
accounts.  Fieldsets are customised to expose subscription-specific fields
(status, confirmed_at) and remove irrelevant auth fields (first_name, etc.).
"""

import logging
from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import QuerySet
from django.http import HttpRequest

from .fields import encrypt_value
from .models import PasskeyCredential, PushSubscription, Subscriber, Subscription

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Encrypted-email search mixin
# ---------------------------------------------------------------------------


class EncryptedEmailSearchMixin:
    """Mixin that adds ``email:`` token-based search for encrypted email fields.

    Because the email column is stored as AES-SIV ciphertext, Django's default
    ``icontains`` search cannot match partial email strings.  This mixin intercepts
    the admin search when the operator uses the ``email:<address>`` token: it
    encrypts the provided address and performs an exact-match filter against the
    encrypted column.

    For any other search term the mixin falls through to ``super()``, so the
    remaining ``search_fields`` (e.g. ``region__region_id``, ``name``) work as
    normal.

    Subclasses must set ``encrypted_email_lookup`` to the ORM lookup path for
    the encrypted email field (e.g. ``"email"`` or ``"subscriber__email"``).
    """

    encrypted_email_lookup: str = ""

    def get_search_results(
        self,
        request: HttpRequest,
        queryset: QuerySet[Any],
        search_term: str,
    ) -> tuple[QuerySet[Any], bool]:
        """Override admin search to handle the ``email:`` token.

        Args:
            request: The incoming HTTP request.
            queryset: The base queryset to filter.
            search_term: The raw string entered in the admin search box.

        Returns:
            A ``(queryset, use_distinct)`` tuple.  When the ``email:`` token is
            used the queryset is filtered by exact encrypted match and
            ``use_distinct`` is ``False``.  For all other terms the result of
            ``super().get_search_results()`` is returned unchanged.

        """
        if search_term.startswith("email:"):
            raw = search_term[len("email:") :].strip().lower()
            if raw:
                encrypted = encrypt_value(raw)
                return (
                    queryset.filter(**{self.encrypted_email_lookup: encrypted}),
                    False,
                )
            # Empty "email:" prefix — return empty result set.
            return queryset.none(), False
        result: tuple[QuerySet[Any], bool] = super().get_search_results(  # type: ignore[misc]
            request, queryset, search_term
        )
        return result


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class SubscriptionInline(admin.TabularInline):
    """Inline display of subscriptions on the Subscriber admin page."""

    model = Subscription
    extra = 0
    readonly_fields = ["region", "created_at", "updated_at"]
    verbose_name = "Subscription"


class PasskeyCredentialInline(admin.TabularInline):
    """Inline display of registered passkeys on the Subscriber admin page."""

    model = PasskeyCredential
    extra = 0
    readonly_fields = [
        "name",
        "device_type",
        "backed_up",
        "aaguid",
        "last_used_at",
        "created_at",
    ]
    fields = [
        "name",
        "device_type",
        "backed_up",
        "aaguid",
        "last_used_at",
        "created_at",
    ]
    verbose_name = "Passkey"
    can_delete = True


@admin.register(Subscriber)
class SubscriberAdmin(EncryptedEmailSearchMixin, UserAdmin):
    """Admin view for Subscriber (custom user model).

    Email search is available via the ``email:`` token (e.g. ``email:alice@…``).
    The field is encrypted at rest so plain-text partial-match search is not supported.
    """

    encrypted_email_lookup = "email"

    list_display = [
        "email",
        "status",
        "is_staff",
        "confirmed_at",
        "acquisition_country",
        "created_at",
    ]
    list_filter = ["status", "is_staff", "is_superuser"]
    list_select_related = ("acquisition_request",)
    # email is encrypted at rest — plain-text search is replaced by the
    # EncryptedEmailSearchMixin's email: token. search_fields remains an
    # empty tuple so the search box is still rendered.
    search_fields = ()
    ordering = ["-created_at"]

    @admin.display(description="Country (acquisition)")
    def acquisition_country(self, obj: object) -> str:
        """Return the country code from the acquisition RequestLog, or '-'."""
        req = getattr(obj, "acquisition_request", None)
        if req is not None:
            return req.country_code or "—"
        return "—"

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Subscription", {"fields": ("status", "confirmed_at", "acquisition_request")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Metadata",
            {"fields": ("uuid", "last_login", "created_at", "updated_at")},
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "is_staff"),
            },
        ),
    )

    readonly_fields = [
        "uuid",
        "created_at",
        "updated_at",
        "last_login",
        "confirmed_at",
        "acquisition_request",
    ]
    raw_id_fields = []  # acquisition_request is read-only, not editable here
    inlines = [SubscriptionInline, PasskeyCredentialInline]


@admin.register(Subscription)
class SubscriptionAdmin(EncryptedEmailSearchMixin, admin.ModelAdmin):
    """Admin view for Subscription.

    Subscriber email search is available via the ``email:`` token
    (e.g. ``email:alice@example.com``).  Region ID search works as normal.
    """

    encrypted_email_lookup = "subscriber__email"

    list_display = ["subscriber", "region", "subscribed_via", "created_at"]
    list_select_related = ["subscriber", "region", "subscribed_via"]
    # subscriber__email is encrypted; use email: token for address lookup.
    search_fields = ["region__region_id"]
    readonly_fields = ["uuid", "created_at", "updated_at", "subscribed_via"]
    raw_id_fields = []  # subscribed_via is read-only, not editable here


@admin.register(PasskeyCredential)
class PasskeyCredentialAdmin(EncryptedEmailSearchMixin, admin.ModelAdmin):
    """Admin view for PasskeyCredential.

    Subscriber email search is available via the ``email:`` token
    (e.g. ``email:alice@example.com``).  Passkey name search works as normal.
    """

    encrypted_email_lookup = "subscriber__email"

    list_display = [
        "subscriber",
        "name",
        "device_type",
        "backed_up",
        "last_used_at",
        "created_at",
    ]
    list_filter = ["device_type", "backed_up"]
    list_select_related = ["subscriber"]
    # subscriber__email is encrypted; use email: token for address lookup.
    search_fields = ["name"]
    readonly_fields = [
        "uuid",
        "credential_id",
        "public_key",
        "aaguid",
        "created_at",
        "updated_at",
    ]


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    """Admin view for PushSubscription (Web Push).

    Read-only inspection view — rows are created by the JS register flow on
    the ``/_push-demo/`` staff page, not via the admin add form. All fields
    are readonly so the admin is purely a diagnostic surface.
    """

    list_display = ["endpoint", "subscriber", "created_at", "last_used_at"]
    list_filter = ["created_at"]
    list_select_related = ["subscriber"]
    ordering = ["-created_at"]
    readonly_fields = [
        "uuid",
        "subscriber",
        "endpoint",
        "p256dh",
        "auth",
        "user_agent",
        "last_used_at",
        "created_at",
        "updated_at",
    ]
