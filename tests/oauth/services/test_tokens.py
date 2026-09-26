"""
tests/oauth/services/test_tokens.py — Tests for apps.oauth.services.tokens.

Covers the code exchange and each way it refuses, refresh rotation and the
reuse rule that revokes a grant, revocation, and every rejection
``authenticate_bearer`` makes.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.http import HttpRequest
from django.test import RequestFactory
from django.utils import timezone
from freezegun import freeze_time
from pytest_django.fixtures import Settings

from apps.oauth.models import AuthorizationCode, OAuthGrant, OAuthToken
from apps.oauth.services.pkce import s256_challenge
from apps.oauth.services.tokens import (
    OAuthError,
    TokenPair,
    authenticate_bearer,
    exchange_code,
    hash_secret,
    issue_code,
    issue_token_pair,
    normalise_scope,
    refresh,
    revoke_grant,
    revoke_token,
)
from tests.factories import OAuthClientFactory, OAuthGrantFactory

pytestmark = pytest.mark.django_db

VERIFIER = "v" * 64
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "http://testserver/api/mcp/"


def _code(grant: OAuthGrant, scope: str = "mcp offline_access") -> str:
    """Issue a code under ``grant`` bound to ``VERIFIER``."""
    return issue_code(
        grant,
        redirect_uri=CALLBACK,
        code_challenge=s256_challenge(VERIFIER),
        resource=RESOURCE,
        scope=scope,
    )


def _exchange(grant: OAuthGrant, raw: str, **overrides: object) -> TokenPair:
    """Exchange ``raw`` with the happy-path arguments, overridable."""
    kwargs: dict = {
        "raw_code": raw,
        "client": grant.client,
        "redirect_uri": CALLBACK,
        "code_verifier": VERIFIER,
        "resource": RESOURCE,
    }
    kwargs.update(overrides)
    return exchange_code(**kwargs)


def _request() -> HttpRequest:
    """Return a request on the test origin."""
    return RequestFactory().post("/api/mcp/")


def _rt(pair: TokenPair) -> str:
    """Return a pair's refresh token, which these tests always expect."""
    assert pair.refresh_token is not None
    return pair.refresh_token


class TestScope:
    """normalise_scope."""

    def test_default_and_order(self) -> None:
        """Empty means mcp; mcp is always included; order is canonical."""
        assert normalise_scope(None) == "mcp"
        assert normalise_scope("offline_access") == "mcp offline_access"
        assert normalise_scope("offline_access mcp") == "mcp offline_access"

    def test_unknown_scope(self) -> None:
        """An unsupported scope is refused."""
        assert normalise_scope("mcp admin") is None


class TestExchangeCode:
    """The authorization_code grant."""

    def test_happy_path_issues_access_and_refresh(self) -> None:
        """A valid exchange returns a pair and marks the code used."""
        grant = OAuthGrantFactory.create()
        pair = _exchange(grant, _code(grant))
        assert pair.access_token.startswith("sd_at_")
        assert _rt(pair).startswith("sd_rt_")
        assert pair.expires_in == 3600
        assert AuthorizationCode.objects.get().used_at is not None
        # Only hashes are stored.
        assert OAuthToken.objects.filter(
            token_hash=hash_secret(pair.access_token)
        ).exists()
        assert not OAuthToken.objects.filter(token_hash=pair.access_token).exists()

    def test_no_refresh_without_offline_access(self) -> None:
        """Scope 'mcp' alone gets an access token only."""
        grant = OAuthGrantFactory.create()
        pair = _exchange(grant, _code(grant, scope="mcp"))
        assert pair.refresh_token is None
        assert "refresh_token" not in pair.as_response()

    def test_wrong_verifier(self) -> None:
        """PKCE failure is invalid_grant."""
        grant = OAuthGrantFactory.create()
        with pytest.raises(OAuthError, match="PKCE") as exc:
            _exchange(grant, _code(grant), code_verifier="w" * 64)
        assert exc.value.error == "invalid_grant"

    def test_reuse(self) -> None:
        """A code works once."""
        grant = OAuthGrantFactory.create()
        raw = _code(grant)
        _exchange(grant, raw)
        with pytest.raises(OAuthError, match="already used"):
            _exchange(grant, raw)

    def test_expiry(self) -> None:
        """A code older than five minutes is refused."""
        grant = OAuthGrantFactory.create()
        raw = _code(grant)
        with freeze_time(timezone.now() + timedelta(minutes=6)):
            with pytest.raises(OAuthError, match="expired"):
                _exchange(grant, raw)

    def test_redirect_mismatch(self) -> None:
        """The redirect_uri must equal the authorize request's."""
        grant = OAuthGrantFactory.create()
        with pytest.raises(OAuthError, match="redirect_uri"):
            _exchange(grant, _code(grant), redirect_uri=CALLBACK + "x")

    def test_resource_mismatch(self) -> None:
        """A different resource is invalid_target; the other spelling is fine."""
        grant = OAuthGrantFactory.create()
        with pytest.raises(OAuthError) as exc:
            _exchange(grant, _code(grant), resource="http://testserver/api/other/")
        assert exc.value.error == "invalid_target"
        assert _exchange(grant, _code(grant), resource="http://testserver/api/mcp")

    def test_client_mismatch(self) -> None:
        """Another client cannot redeem the code."""
        grant = OAuthGrantFactory.create()
        with pytest.raises(OAuthError, match="another client"):
            _exchange(grant, _code(grant), client=OAuthClientFactory.create())

    def test_unknown_code(self) -> None:
        """An unknown code is invalid_grant."""
        grant = OAuthGrantFactory.create()
        with pytest.raises(OAuthError, match="Unknown"):
            _exchange(grant, "sd_ac_nope")

    def test_revoked_grant(self) -> None:
        """A code under a revoked grant is refused."""
        grant = OAuthGrantFactory.create()
        raw = _code(grant)
        grant.revoked_at = timezone.now()
        grant.save()
        with pytest.raises(OAuthError, match="revoked"):
            _exchange(grant, raw)


class TestRefresh:
    """The refresh_token grant."""

    def _pair(self) -> tuple[OAuthGrant, TokenPair]:
        """Return a grant and its first token pair."""
        grant = OAuthGrantFactory.create()
        return grant, _exchange(grant, _code(grant))

    def test_rotation(self) -> None:
        """A refresh returns a new pair and retires the old refresh token."""
        grant, first = self._pair()
        second = refresh(
            raw_refresh=_rt(first),
            client=grant.client,
            resource=None,
            scope=None,
        )
        assert second.refresh_token != first.refresh_token
        old = OAuthToken.objects.get(token_hash=hash_secret(_rt(first)))
        new = OAuthToken.objects.get(token_hash=hash_secret(_rt(second)))
        assert old.replaced_by == new

    def test_replay_after_narrowing_still_revokes_the_grant(self) -> None:
        """A token spent on a narrowing refresh (no successor) is still reuse."""
        grant = OAuthGrantFactory.create()
        first = _exchange(grant, _code(grant))
        narrowed = refresh(
            raw_refresh=_rt(first), client=grant.client, resource=RESOURCE, scope="mcp"
        )
        with pytest.raises(OAuthError, match="already used"):
            refresh(
                raw_refresh=_rt(first),
                client=grant.client,
                resource=RESOURCE,
                scope=None,
            )
        grant.refresh_from_db()
        assert grant.revoked_at is not None
        new_access = OAuthToken.objects.get(
            token_hash=hash_secret(narrowed.access_token)
        )
        assert new_access.revoked_at is not None

    def test_reuse_revokes_the_grant(self) -> None:
        """Presenting a rotated token revokes the grant and every token."""
        grant, first = self._pair()
        second = refresh(
            raw_refresh=_rt(first),
            client=grant.client,
            resource=None,
            scope=None,
        )
        with pytest.raises(OAuthError, match="already used") as exc:
            refresh(
                raw_refresh=_rt(first),
                client=grant.client,
                resource=None,
                scope=None,
            )
        assert exc.value.error == "invalid_grant"
        grant.refresh_from_db()
        assert grant.revoked_at is not None
        assert not OAuthToken.objects.filter(revoked_at__isnull=True).exists()
        with pytest.raises(OAuthError):
            refresh(
                raw_refresh=_rt(second),
                client=grant.client,
                resource=None,
                scope=None,
            )

    def test_unknown_and_wrong_client(self) -> None:
        """An unknown token, or another client's, is invalid_grant."""
        grant, first = self._pair()
        with pytest.raises(OAuthError, match="Unknown"):
            refresh(
                raw_refresh="sd_rt_x", client=grant.client, resource=None, scope=None
            )
        with pytest.raises(OAuthError, match="Unknown"):
            refresh(
                raw_refresh=_rt(first),
                client=OAuthClientFactory.create(),
                resource=None,
                scope=None,
            )

    def test_expired(self) -> None:
        """A refresh token older than thirty days is refused."""
        grant, first = self._pair()
        with freeze_time(timezone.now() + timedelta(days=31)):
            with pytest.raises(OAuthError, match="expired"):
                refresh(
                    raw_refresh=_rt(first),
                    client=grant.client,
                    resource=None,
                    scope=None,
                )

    def test_revoked_grant(self) -> None:
        """After a disconnect, refresh fails."""
        grant, first = self._pair()
        revoke_grant(grant)
        with pytest.raises(OAuthError, match="revoked"):
            refresh(
                raw_refresh=_rt(first),
                client=grant.client,
                resource=None,
                scope=None,
            )

    def test_scope_may_narrow_not_widen(self) -> None:
        """Narrowing to 'mcp' drops the refresh token; widening is refused."""
        grant = OAuthGrantFactory.create()
        first = _exchange(grant, _code(grant))
        narrowed = refresh(
            raw_refresh=_rt(first),
            client=grant.client,
            resource=RESOURCE,
            scope="mcp",
        )
        assert narrowed.refresh_token is None
        assert narrowed.scope == "mcp"
        assert OAuthToken.objects.get(token_hash=hash_secret(_rt(first))).consumed_at

        other = OAuthGrantFactory.create()
        pair, _ = issue_token_pair(other, resource=RESOURCE, scope="mcp offline_access")
        OAuthToken.objects.filter(token_hash=hash_secret(_rt(pair))).update(
            scope="offline_access"
        )
        with pytest.raises(OAuthError) as exc:
            refresh(
                raw_refresh=_rt(pair),
                client=other.client,
                resource=None,
                scope="mcp offline_access",
            )
        assert exc.value.error == "invalid_scope"

    def test_resource_mismatch(self) -> None:
        """A different resource is invalid_target."""
        grant, first = self._pair()
        with pytest.raises(OAuthError) as exc:
            refresh(
                raw_refresh=_rt(first),
                client=grant.client,
                resource="https://evil.test/api/mcp/",
                scope=None,
            )
        assert exc.value.error == "invalid_target"


class TestRevokeToken:
    """RFC 7009 revocation."""

    def test_refresh_revokes_grant(self) -> None:
        """Revoking a refresh token disconnects the grant."""
        grant = OAuthGrantFactory.create()
        pair = _exchange(grant, _code(grant))
        revoke_token(_rt(pair), grant.client.client_id)
        grant.refresh_from_db()
        assert grant.revoked_at is not None

    def test_access_revokes_only_itself(self) -> None:
        """Revoking an access token leaves the grant active."""
        grant = OAuthGrantFactory.create()
        pair = _exchange(grant, _code(grant))
        revoke_token(pair.access_token)
        grant.refresh_from_db()
        assert grant.revoked_at is None
        assert OAuthToken.objects.get(
            token_hash=hash_secret(pair.access_token)
        ).revoked_at

    def test_unknown_and_foreign_are_ignored(self) -> None:
        """An unknown token, or another client's, changes nothing."""
        grant = OAuthGrantFactory.create()
        pair = _exchange(grant, _code(grant))
        revoke_token("sd_at_nope")
        revoke_token(_rt(pair), "someone-else")
        grant.refresh_from_db()
        assert grant.revoked_at is None


class TestAuthenticateBearer:
    """authenticate_bearer."""

    def _access(self, grant: OAuthGrant | None = None) -> tuple[OAuthGrant, str]:
        """Return a grant and a live access token under it."""
        grant = grant or OAuthGrantFactory.create()
        pair, _ = issue_token_pair(grant, resource=RESOURCE, scope="mcp")
        return grant, pair.access_token

    def test_valid_token_returns_user_and_touches_last_used(self) -> None:
        """A live token authenticates and stamps last_used_at."""
        grant, raw = self._access()
        assert authenticate_bearer(raw, _request()) == grant.user
        grant.refresh_from_db()
        assert grant.last_used_at is not None

    def test_last_used_is_touched_at_most_once_a_minute(self) -> None:
        """A second call inside a minute does not write."""
        grant, raw = self._access()
        authenticate_bearer(raw, _request())
        grant.refresh_from_db()
        first = grant.last_used_at
        authenticate_bearer(raw, _request())
        grant.refresh_from_db()
        assert grant.last_used_at == first
        with freeze_time(timezone.now() + timedelta(minutes=2)):
            authenticate_bearer(raw, _request())
        grant.refresh_from_db()
        assert grant.last_used_at is not None
        assert first is not None
        assert grant.last_used_at > first

    def test_expired(self) -> None:
        """An access token older than an hour is refused."""
        _, raw = self._access()
        with freeze_time(timezone.now() + timedelta(hours=2)):
            assert authenticate_bearer(raw, _request()) is None

    def test_revoked_token(self) -> None:
        """A revoked token is refused."""
        _, raw = self._access()
        OAuthToken.objects.update(revoked_at=timezone.now())
        assert authenticate_bearer(raw, _request()) is None

    def test_revoked_grant(self) -> None:
        """A token under a revoked grant is refused."""
        grant, raw = self._access()
        OAuthGrant.objects.filter(pk=grant.pk).update(revoked_at=timezone.now())
        assert authenticate_bearer(raw, _request()) is None

    def test_wrong_audience(self, settings: Settings) -> None:
        """A token for another origin is refused here."""
        settings.ALLOWED_HOSTS = ["*"]
        _, raw = self._access()
        request = RequestFactory().post("/api/mcp/", HTTP_HOST="other.test")
        assert authenticate_bearer(raw, request) is None

    def test_refresh_token_is_not_a_bearer(self) -> None:
        """A refresh token cannot call the MCP endpoint."""
        grant = OAuthGrantFactory.create()
        pair, _ = issue_token_pair(grant, resource=RESOURCE, scope="mcp offline_access")
        assert authenticate_bearer(_rt(pair), _request()) is None

    def test_inactive_user_and_blank(self) -> None:
        """An inactive user and an empty token are refused."""
        grant, raw = self._access()
        grant.user.is_active = False
        grant.user.save()
        assert authenticate_bearer(raw, _request()) is None
        assert authenticate_bearer("", _request()) is None
