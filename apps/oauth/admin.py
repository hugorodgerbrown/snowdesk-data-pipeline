"""
apps/oauth/admin.py — Django admin registration for the oauth app.

Read-mostly admin views for the four OAuth tables. Every row here is either
a client's self-description or a record of something a user approved, so
staff can search and delete but never edit a field. The code and token
tables show hashes only; the plaintext was never stored.
"""

import logging

from django.contrib import admin

from .models import AuthorizationCode, OAuthClient, OAuthGrant, OAuthToken

logger = logging.getLogger(__name__)


@admin.register(OAuthClient)
class OAuthClientAdmin(admin.ModelAdmin):
    """Read-mostly admin view for OAuthClient."""

    list_display = ["client_name", "kind", "client_id", "created_at"]
    list_filter = ["kind", "created_at"]
    search_fields = ["client_name", "client_id"]
    readonly_fields = [
        "client_id",
        "kind",
        "client_name",
        "redirect_uris",
        "metadata_fetched_at",
        "uuid",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]


@admin.register(OAuthGrant)
class OAuthGrantAdmin(admin.ModelAdmin):
    """Read-mostly admin view for OAuthGrant."""

    list_display = [
        "user",
        "client",
        "scope",
        "last_used_at",
        "revoked_at",
        "created_at",
    ]
    list_filter = ["created_at", "revoked_at"]
    search_fields = ["user__email", "client__client_name", "client__client_id"]
    list_select_related = ["user", "client"]
    readonly_fields = [
        "user",
        "client",
        "scope",
        "resource",
        "last_used_at",
        "revoked_at",
        "uuid",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]


@admin.register(AuthorizationCode)
class AuthorizationCodeAdmin(admin.ModelAdmin):
    """Read-mostly admin view for AuthorizationCode."""

    list_display = ["grant", "expires_at", "used_at", "created_at"]
    list_filter = ["created_at"]
    list_select_related = ["grant__user", "grant__client"]
    readonly_fields = [
        "grant",
        "code_hash",
        "redirect_uri",
        "code_challenge",
        "resource",
        "scope",
        "expires_at",
        "used_at",
        "uuid",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]


@admin.register(OAuthToken)
class OAuthTokenAdmin(admin.ModelAdmin):
    """Read-mostly admin view for OAuthToken."""

    list_display = ["grant", "kind", "expires_at", "revoked_at", "created_at"]
    list_filter = ["kind", "created_at"]
    list_select_related = ["grant__user", "grant__client"]
    readonly_fields = [
        "grant",
        "kind",
        "token_hash",
        "resource",
        "scope",
        "expires_at",
        "revoked_at",
        "replaced_by",
        "uuid",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]
