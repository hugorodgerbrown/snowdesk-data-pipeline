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

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import PasskeyCredential, PushSubscription, Subscriber, Subscription

logger = logging.getLogger(__name__)


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
class SubscriberAdmin(UserAdmin):
    """Admin view for Subscriber (custom user model)."""

    list_display = ["email", "status", "is_staff", "confirmed_at", "created_at"]
    list_filter = ["status", "is_staff", "is_superuser"]
    search_fields = ["email"]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Subscription", {"fields": ("status", "confirmed_at")}),
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

    readonly_fields = ["uuid", "created_at", "updated_at", "last_login", "confirmed_at"]
    inlines = [SubscriptionInline, PasskeyCredentialInline]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Admin view for Subscription."""

    list_display = ["subscriber", "region", "created_at"]
    list_select_related = ["subscriber", "region"]
    search_fields = ["subscriber__email", "region__region_id"]
    readonly_fields = ["uuid", "created_at", "updated_at"]


@admin.register(PasskeyCredential)
class PasskeyCredentialAdmin(admin.ModelAdmin):
    """Admin view for PasskeyCredential."""

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
    search_fields = ["subscriber__email", "name"]
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
