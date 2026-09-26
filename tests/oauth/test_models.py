"""
tests/oauth/test_models.py — Tests for the oauth models (SNOW-1035).

Covers ``to_string`` on all four models, the custom querysets, the derived
properties the consent and settings pages read, the one-grant-per-(user,
client) constraint, and the cascade from a deleted user.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.oauth.models import AuthorizationCode, OAuthClient, OAuthGrant, OAuthToken
from tests.factories import (
    AuthorizationCodeFactory,
    OAuthClientFactory,
    OAuthGrantFactory,
    OAuthTokenFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


class TestOAuthClient:
    """OAuthClient string form, properties and queryset."""

    def test_to_string_names_client_and_kind(self) -> None:
        """to_string reads "{name} ({kind})" and __str__ delegates."""
        client = OAuthClientFactory.create(client_name="Claude")
        assert client.to_string() == "Claude (Dynamic registration)"
        assert str(client) == client.to_string()

    def test_redirect_host_is_first_uri_hostname(self) -> None:
        """redirect_host names where a code goes."""
        client = OAuthClientFactory.create(
            redirect_uris=["https://claude.ai/api/mcp/auth_callback"]
        )
        assert client.redirect_host == "claude.ai"

    def test_redirect_host_blank_without_uris(self) -> None:
        """A client with no redirect URIs has no host to name."""
        client = OAuthClientFactory.create(redirect_uris=[])
        assert client.redirect_host == ""

    def test_display_name_falls_back_to_host_then_id(self) -> None:
        """An unnamed client is named by its redirect host, then its id."""
        named_by_host = OAuthClientFactory.create(client_name="")
        assert named_by_host.display_name == "claude.ai"
        named_by_id = OAuthClientFactory.create(
            client_name="", redirect_uris=[], client_id="abc"
        )
        assert named_by_id.display_name == "abc"

    def test_of_kind_filters(self) -> None:
        """of_kind returns only clients of that kind."""
        dcr = OAuthClientFactory.create()
        OAuthClientFactory.create(kind=OAuthClient.KIND.LOCAL)
        assert list(OAuthClient.objects.of_kind(OAuthClient.KIND.DCR)) == [dcr]


class TestOAuthGrant:
    """OAuthGrant string form, queryset and constraints."""

    def test_to_string_marks_revoked(self) -> None:
        """A revoked grant says so."""
        grant = OAuthGrantFactory.create()
        assert "(revoked)" not in grant.to_string()
        grant.revoked_at = timezone.now()
        assert grant.to_string().endswith("(revoked)")
        assert str(grant) == grant.to_string()

    def test_for_user_and_active(self) -> None:
        """for_user scopes to the owner; active drops revoked grants."""
        user = UserFactory.create()
        mine = OAuthGrantFactory.create(user=user)
        OAuthGrantFactory.create(user=user, revoked_at=timezone.now())
        OAuthGrantFactory.create()
        assert set(OAuthGrant.objects.for_user(user)) >= {mine}
        assert list(OAuthGrant.objects.for_user(user).active()) == [mine]
        assert mine.is_active

    def test_one_grant_per_user_and_client(self) -> None:
        """A second grant for the same (user, client) is refused."""
        grant = OAuthGrantFactory.create()
        with pytest.raises(IntegrityError):
            OAuthGrantFactory.create(user=grant.user, client=grant.client)

    def test_user_delete_cascades_to_grants_codes_and_tokens(self) -> None:
        """Deleting a user removes every grant, code and token under it."""
        grant = OAuthGrantFactory.create()
        AuthorizationCodeFactory.create(grant=grant)
        OAuthTokenFactory.create(grant=grant)
        grant.user.delete()
        assert not OAuthGrant.objects.exists()
        assert not AuthorizationCode.objects.exists()
        assert not OAuthToken.objects.exists()
        # The client is not the user's, and survives.
        assert OAuthClient.objects.filter(pk=grant.client_id).exists()


class TestAuthorizationCode:
    """AuthorizationCode string form, expiry and queryset."""

    def test_to_string_marks_used(self) -> None:
        """A used code says so."""
        code = AuthorizationCodeFactory.create()
        assert code.to_string().startswith("code for ")
        code.used_at = timezone.now()
        assert code.to_string().endswith("(used)")
        assert str(code) == code.to_string()

    def test_is_expired_and_expired_before(self) -> None:
        """is_expired and expired_before agree on the lifetime."""
        now = timezone.now()
        live = AuthorizationCodeFactory.create()
        old = AuthorizationCodeFactory.create(expires_at=now - timedelta(days=8))
        assert not live.is_expired
        assert old.is_expired
        cutoff = now - timedelta(days=7)
        assert list(AuthorizationCode.objects.expired_before(cutoff)) == [old]


class TestOAuthToken:
    """OAuthToken string form, expiry and querysets."""

    def test_to_string_names_kind_and_marks_revoked(self) -> None:
        """to_string names the kind and marks a revoked token."""
        token = OAuthTokenFactory.create()
        assert token.to_string().startswith("Access token for ")
        token.revoked_at = timezone.now()
        assert token.to_string().endswith("(revoked)")
        assert str(token) == token.to_string()

    def test_kind_querysets(self) -> None:
        """access() and refresh() split the table by kind."""
        access = OAuthTokenFactory.create()
        refresh = OAuthTokenFactory.create(kind=OAuthToken.KIND.REFRESH)
        assert list(OAuthToken.objects.access()) == [access]
        assert list(OAuthToken.objects.refresh()) == [refresh]

    def test_unrevoked_and_spent_before(self) -> None:
        """spent_before catches long-expired and long-revoked tokens only."""
        now = timezone.now()
        live = OAuthTokenFactory.create()
        expired = OAuthTokenFactory.create(expires_at=now - timedelta(days=8))
        revoked = OAuthTokenFactory.create(revoked_at=now - timedelta(days=8))
        recent = OAuthTokenFactory.create(revoked_at=now - timedelta(days=1))
        cutoff = now - timedelta(days=7)
        assert set(OAuthToken.objects.spent_before(cutoff)) == {expired, revoked}
        assert set(OAuthToken.objects.unrevoked()) == {live, expired}
        assert recent.revoked_at is not None
        assert not live.is_expired
        assert expired.is_expired
