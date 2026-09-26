"""
tests/oauth/test_admin.py — Admin registration smoke test for the oauth app.

Verifies that the four OAuth models are registered and that no hash or
approval field is editable by staff.
"""

from __future__ import annotations

import pytest
from django.contrib import admin

from apps.oauth.admin import (
    AuthorizationCodeAdmin,
    OAuthClientAdmin,
    OAuthGrantAdmin,
    OAuthTokenAdmin,
)
from apps.oauth.models import AuthorizationCode, OAuthClient, OAuthGrant, OAuthToken


@pytest.mark.parametrize(
    ("model", "admin_class"),
    [
        (OAuthClient, OAuthClientAdmin),
        (OAuthGrant, OAuthGrantAdmin),
        (AuthorizationCode, AuthorizationCodeAdmin),
        (OAuthToken, OAuthTokenAdmin),
    ],
)
def test_model_is_registered(model: type, admin_class: type) -> None:
    """Each model is registered with its own admin class."""
    assert isinstance(admin.site._registry[model], admin_class)


def test_hashes_are_read_only() -> None:
    """Neither hash column is editable in the admin."""
    assert "code_hash" in admin.site._registry[AuthorizationCode].readonly_fields
    assert "token_hash" in admin.site._registry[OAuthToken].readonly_fields


def test_grant_approval_fields_are_read_only() -> None:
    """What a user approved is not staff-editable."""
    registered = admin.site._registry[OAuthGrant]
    for field in ("user", "client", "scope", "resource", "revoked_at"):
        assert field in registered.readonly_fields
