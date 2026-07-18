"""
accounts/admin.py — Django admin registrations for subscriptions models.

Provides list and detail views for Account, Subscriber, Subscription,
PasskeyCredential, and PushSubscription records so that operators can inspect
and manage registered accounts, newsletter subscriptions, registered passkeys,
and Web Push subscriptions without direct database access.

``Account`` (SNOW-430) is the identity profile keyed to ``auth.User``; it
appears both as a standalone admin and as an inline on the custom User admin.

``PasskeyCredential`` has a FK to ``auth.User``, so its inline appears on a
custom ``UserAdmin`` (not on ``SubscriberAdmin``).  This module unregisters the
default ``User`` admin and registers a custom subclass with ``PasskeyCredentialInline``
attached.

``SubscriberAdmin`` retains the ``SubscriptionInline``; passkeys are now on
``UserAdmin``.
"""

import logging

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as _BaseUserAdmin

from .models import (
    Account,
    PasskeyCredential,
    PushSubscription,
    Subscriber,
    Subscription,
)

logger = logging.getLogger(__name__)

User = get_user_model()


class SubscriptionInline(admin.TabularInline):
    """Inline display of subscriptions on the Subscriber admin page."""

    model = Subscription
    extra = 0
    readonly_fields = ["region", "created_at", "updated_at"]
    verbose_name = "Subscription"


class AccountInline(admin.StackedInline):
    """Inline display of the Account identity profile on the User admin page."""

    model = Account
    extra = 0
    max_num = 1
    can_delete = False
    readonly_fields = ["is_verified", "verified_at", "created_at", "updated_at"]
    fields = ["display_name", "is_verified", "verified_at", "created_at", "updated_at"]
    verbose_name = "Account"
    verbose_name_plural = "Account"


class PasskeyCredentialInline(admin.TabularInline):
    """Inline display of registered passkeys on the User admin page."""

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
class SubscriberAdmin(admin.ModelAdmin):
    """Admin view for Subscriber (profile linked to auth.User)."""

    list_display = [
        "subscriber_email",
        "status",
        "confirmed_at",
        "acquisition_country",
        "created_at",
    ]
    list_filter = ["status"]
    list_select_related = ("user", "acquisition_request")
    search_fields = ["user__email"]
    ordering = ["-created_at"]

    @admin.display(description="Email")
    def subscriber_email(self, obj: Subscriber) -> str:
        """Return the linked User's email address."""
        return obj.user.email

    @admin.display(description="Country (acquisition)")
    def acquisition_country(self, obj: Subscriber) -> str:
        """Return the country code from the acquisition RequestLog, or '-'."""
        req = getattr(obj, "acquisition_request", None)
        if req is not None:
            return req.country_code or "—"
        return "—"

    fieldsets = (
        (None, {"fields": ("user",)}),
        ("Subscription", {"fields": ("status", "confirmed_at", "acquisition_request")}),
        (
            "Metadata",
            {"fields": ("uuid", "created_at", "updated_at")},
        ),
    )

    readonly_fields = [
        "user",
        "uuid",
        "created_at",
        "updated_at",
        "confirmed_at",
        "acquisition_request",
    ]
    inlines = [SubscriptionInline]


class SnowdeskUserAdmin(_BaseUserAdmin):
    """Custom UserAdmin that exposes registered passkeys as an inline.

    Passkeys are keyed to auth.User (not to Subscriber), so the inline belongs
    here rather than on SubscriberAdmin.  The Account identity profile
    (SNOW-430) is likewise keyed to auth.User and appears as an inline here.
    All other UserAdmin behaviour is inherited unchanged.
    """

    inlines = [AccountInline, PasskeyCredentialInline]


# Replace the default UserAdmin with our custom one so passkeys are visible.
admin.site.unregister(User)
admin.site.register(User, SnowdeskUserAdmin)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    """Admin view for Account (identity profile linked to auth.User)."""

    list_display = [
        "account_email",
        "display_name",
        "is_verified",
        "verified_at",
        "created_at",
    ]
    list_filter = ["is_verified"]
    list_select_related = ["user"]
    search_fields = ["user__email", "display_name"]
    ordering = ["-created_at"]
    readonly_fields = [
        "user",
        "uuid",
        "is_verified",
        "verified_at",
        "created_at",
        "updated_at",
    ]

    @admin.display(description="Email")
    def account_email(self, obj: Account) -> str:
        """Return the linked User's email address."""
        return obj.user.email


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Admin view for Subscription."""

    list_display = [
        "subscriber",
        "region",
        "acquisition_country",
        "geo_match_kind",
        "geo_matched_region",
        "created_at",
    ]
    list_filter = ["geo_match_kind"]
    list_select_related = [
        "subscriber",
        "subscriber__user",
        "region",
        "subscribed_via",
        "geo_matched_region",
    ]
    search_fields = ["subscriber__user__email", "region__region_id"]
    readonly_fields = [
        "uuid",
        "created_at",
        "updated_at",
        "subscribed_via",
        "geo_match_kind",
        "geo_matched_region",
    ]
    raw_id_fields = []  # subscribed_via is read-only, not editable here

    @admin.display(description="Country (acquisition)")
    def acquisition_country(self, obj: Subscription) -> str:
        """Return the country code from the subscribed_via RequestLog, or '—'."""
        req = getattr(obj, "subscribed_via", None)
        if req is not None:
            return req.country_code or "—"
        return "—"


@admin.register(PasskeyCredential)
class PasskeyCredentialAdmin(admin.ModelAdmin):
    """Admin view for PasskeyCredential."""

    list_display = [
        "user",
        "name",
        "device_type",
        "backed_up",
        "last_used_at",
        "created_at",
    ]
    list_filter = ["device_type", "backed_up"]
    list_select_related = ["user"]
    search_fields = ["user__email", "name"]
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

    list_display = ["endpoint", "subscriber", "mechanism", "created_at", "last_used_at"]
    list_filter = ["created_at", "inactive_at"]
    list_select_related = ["subscriber", "subscriber__user"]
    ordering = ["-created_at"]
    readonly_fields = [
        "uuid",
        "subscriber",
        "endpoint",
        "p256dh",
        "auth",
        "user_agent",
        "last_used_at",
        "mechanism",
        "inactive_at",
        "created_at",
        "updated_at",
    ]
