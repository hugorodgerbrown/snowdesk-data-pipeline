"""
subscriptions/admin.py — Django admin registrations for subscriptions models.

Provides list and detail views for Subscriber and Subscription records so
that operators can inspect and manage newsletter subscriptions without
direct database access.
"""

import logging

from django.contrib import admin

from .models import Subscriber, Subscription

logger = logging.getLogger(__name__)


class SubscriptionInline(admin.TabularInline):
    """Inline display of subscriptions on the Subscriber admin page."""

    model = Subscription
    extra = 0
    readonly_fields = ["region", "created_at", "updated_at"]
    verbose_name = "Subscription"


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    """Admin view for Subscriber."""

    list_display = ["email", "is_active", "last_authenticated_at", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["email"]
    readonly_fields = ["uuid", "created_at", "updated_at", "last_authenticated_at"]
    inlines = [SubscriptionInline]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Admin view for Subscription."""

    list_display = ["subscriber", "region", "created_at"]
    list_select_related = ["subscriber", "region"]
    search_fields = ["subscriber__email", "region__region_id"]
    readonly_fields = ["uuid", "created_at", "updated_at"]
