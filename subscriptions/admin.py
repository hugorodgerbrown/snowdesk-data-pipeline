"""
subscriptions/admin.py — Django admin registrations for subscriptions models.

Provides list and detail views for Subscriber, Subscription, and
PasskeyCredential records so that operators can inspect and manage newsletter
subscriptions and registered passkeys without direct database access.
"""

import logging

from django.contrib import admin

from .models import PasskeyCredential, Subscriber, Subscription

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
class SubscriberAdmin(admin.ModelAdmin):
    """Admin view for Subscriber."""

    list_display = ["email", "status", "confirmed_at", "created_at"]
    list_filter = ["status"]
    search_fields = ["email"]
    readonly_fields = ["uuid", "created_at", "updated_at", "confirmed_at"]
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
