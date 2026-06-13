"""
subscriptions/admin.py — Django admin registrations for subscriptions models.

Provides list and detail views for Subscriber, Subscription, PasskeyCredential,
and PushSubscription records so that operators can inspect and manage newsletter
subscriptions, registered passkeys, and Web Push subscriptions without direct
database access.

User accounts are managed via the standard Django UserAdmin (registered below)
so that staff password management works.  SubscriberAdmin is a plain ModelAdmin
that surfaces the subscription lifecycle fields (status, confirmed_at) and
exposes the linked User's email as a read-only display field.
"""

import logging

from django.contrib import admin
from django.contrib.auth import get_user_model

from .models import PasskeyCredential, PushSubscription, Subscriber, Subscription

logger = logging.getLogger(__name__)

User = get_user_model()


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
    inlines = [SubscriptionInline, PasskeyCredentialInline]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Admin view for Subscription."""

    list_display = ["subscriber", "region", "subscribed_via", "created_at"]
    list_select_related = ["subscriber", "subscriber__user", "region", "subscribed_via"]
    search_fields = ["subscriber__user__email", "region__region_id"]
    readonly_fields = ["uuid", "created_at", "updated_at", "subscribed_via"]
    raw_id_fields = []  # subscribed_via is read-only, not editable here


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
    list_select_related = ["subscriber", "subscriber__user"]
    search_fields = ["subscriber__user__email", "name"]
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
        "created_at",
        "updated_at",
    ]
