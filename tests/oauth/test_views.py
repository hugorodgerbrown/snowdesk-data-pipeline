"""
tests/oauth/test_views.py — HTTP tests for the OAuth endpoints (SNOW-1035).

Covers both discovery documents and the fields Claude requires of them,
Dynamic Client Registration, the token endpoint (form-encoded, each
``invalid_grant``), revocation, the authorize/consent flow, the settings
page's Disconnect, and one full round trip ending at the MCP endpoint.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from pytest_django.fixtures import Settings

from apps.oauth.models import OAuthClient, OAuthGrant
from apps.oauth.services.pkce import s256_challenge
from apps.oauth.services.tokens import hash_secret, issue_code, issue_token_pair
from tests.factories import (
    AccountFactory,
    OAuthClientFactory,
    OAuthGrantFactory,
    UserFactory,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.test.client import _MonkeyPatchedWSGIResponse as _Response

pytestmark = pytest.mark.django_db

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = s256_challenge(VERIFIER)
RESOURCE = "http://testserver/api/mcp/"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset rate-limit counters between tests."""
    cache.clear()


def _verified_user() -> User:
    """Return a user with a verified Account."""
    return AccountFactory.create().user


def _authorize_query(client: OAuthClient, **overrides: str) -> dict[str, str]:
    """Return a valid authorize query for ``client``."""
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": CALLBACK,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "xyz",
        "scope": "mcp offline_access",
        "resource": RESOURCE,
    }
    params.update(overrides)
    return params


def _authorize_url(client: OAuthClient, **overrides: str) -> str:
    """Return the authorize URL for ``client``."""
    return f"{reverse('oauth:authorize')}?{urlencode(_authorize_query(client, **overrides))}"


def _meta_refresh_target(response: _Response) -> str:
    """Return the URL the returning page's meta refresh points at."""
    html = response.content.decode()
    marker = 'http-equiv="refresh" content="0;url='
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)].replace("&amp;", "&")


def _post_token(client: Client, data: dict[str, str]) -> _Response:
    """POST a form-encoded token request."""
    return client.post(reverse("oauth:token"), data=data)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    """The two metadata documents."""

    def test_protected_resource_echoes_the_path_suffix(self, client: Client) -> None:
        """The resource equals origin + the requested suffix, slash and all."""
        with_slash = client.get("/.well-known/oauth-protected-resource/api/mcp/").json()
        without = client.get("/.well-known/oauth-protected-resource/api/mcp").json()
        assert with_slash["resource"] == "http://testserver/api/mcp/"
        assert without["resource"] == "http://testserver/api/mcp"
        assert with_slash["authorization_servers"] == ["http://testserver"]
        assert with_slash["scopes_supported"] == ["mcp"]
        assert with_slash["bearer_methods_supported"] == ["header"]

    def test_bare_protected_resource_names_the_mcp_url(self, client: Client) -> None:
        """Without a suffix the document describes /api/mcp/."""
        body = client.get("/.well-known/oauth-protected-resource").json()
        assert body["resource"] == RESOURCE

    def test_authorization_server_metadata(self, client: Client) -> None:
        """Every field Claude checks is present with the right value."""
        response = client.get("/.well-known/oauth-authorization-server")
        body = response.json()
        assert body["issuer"] == "http://testserver"
        assert body["authorization_endpoint"] == "http://testserver/oauth/authorize/"
        assert body["token_endpoint"] == "http://testserver/oauth/token/"
        assert body["registration_endpoint"] == "http://testserver/oauth/register/"
        assert body["revocation_endpoint"] == "http://testserver/oauth/revoke/"
        assert body["code_challenge_methods_supported"] == ["S256"]
        assert body["token_endpoint_auth_methods_supported"] == ["none"]
        assert body["client_id_metadata_document_supported"] is True
        assert "offline_access" in body["scopes_supported"]
        assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]
        assert response["Cache-Control"] == "public, max-age=3600"

    def test_metadata_is_get_only(self, client: Client) -> None:
        """POST to a discovery document is 405."""
        assert client.post("/.well-known/oauth-authorization-server").status_code == 405


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegister:
    """POST /oauth/register/."""

    URL = reverse("oauth:register")

    def test_valid_registration_returns_201(self, client: Client) -> None:
        """A JSON registration creates a public client."""
        response = client.post(
            self.URL,
            data=json.dumps({"client_name": "Claude", "redirect_uris": [CALLBACK]}),
            content_type="application/json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["token_endpoint_auth_method"] == "none"
        assert OAuthClient.objects.filter(client_id=body["client_id"]).exists()
        assert response["Cache-Control"] == "no-store"

    def test_bad_redirect_is_400(self, client: Client) -> None:
        """A non-https, non-loopback redirect is refused."""
        response = client.post(
            self.URL,
            data=json.dumps({"redirect_uris": ["http://evil.test/cb"]}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_redirect_uri"

    def test_non_json_is_400(self, client: Client) -> None:
        """A body that is not JSON is refused."""
        response = client.post(self.URL, data=b"nope", content_type="application/json")
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client_metadata"

    def test_rate_limit(self, client: Client, settings: Settings) -> None:
        """The eleventh registration in an hour from one IP is 429."""
        settings.RATELIMIT_ENABLE = True
        body = json.dumps({"redirect_uris": [CALLBACK]})
        with freeze_time("2026-09-26 10:05:00"):
            for _ in range(10):
                ok = client.post(self.URL, data=body, content_type="application/json")
                assert ok.status_code == 201
            limited = client.post(self.URL, data=body, content_type="application/json")
        assert limited.status_code == 429
        assert OAuthClient.objects.count() == 10


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------


def _code_for(grant: OAuthGrant) -> str:
    """Issue a code under ``grant`` bound to ``VERIFIER``."""
    return issue_code(
        grant,
        redirect_uri=CALLBACK,
        code_challenge=CHALLENGE,
        resource=RESOURCE,
        scope="mcp offline_access",
    )


class TestToken:
    """POST /oauth/token/."""

    def _exchange_data(self, grant: OAuthGrant, code: str) -> dict[str, str]:
        """Return a valid authorization_code request body."""
        return {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": grant.client.client_id,
            "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER,
            "resource": RESOURCE,
        }

    def test_authorization_code_happy_path(self, client: Client) -> None:
        """A form-encoded exchange returns a Bearer pair, no-store."""
        grant = OAuthGrantFactory.create()
        response = _post_token(client, self._exchange_data(grant, _code_for(grant)))
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 3600
        assert body["access_token"].startswith("sd_at_")
        assert body["refresh_token"].startswith("sd_rt_")
        assert body["scope"] == "mcp offline_access"
        assert response["Cache-Control"] == "no-store"

    @pytest.mark.parametrize(
        "override",
        [
            {"code": "sd_ac_unknown"},
            {"code_verifier": "w" * 64},
            {"redirect_uri": CALLBACK + "x"},
        ],
    )
    def test_invalid_grant_cases(self, client: Client, override: dict) -> None:
        """Unknown code, wrong verifier and wrong redirect are invalid_grant."""
        grant = OAuthGrantFactory.create()
        data = self._exchange_data(grant, _code_for(grant)) | override
        response = _post_token(client, data)
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"

    def test_reused_code_is_invalid_grant(self, client: Client) -> None:
        """A code redeemed twice fails the second time."""
        grant = OAuthGrantFactory.create()
        data = self._exchange_data(grant, _code_for(grant))
        assert _post_token(client, data).status_code == 200
        second = _post_token(client, data)
        assert second.json()["error"] == "invalid_grant"

    def test_expired_code_is_invalid_grant(self, client: Client) -> None:
        """A code older than five minutes fails."""
        grant = OAuthGrantFactory.create()
        data = self._exchange_data(grant, _code_for(grant))
        with freeze_time(timezone.now() + timedelta(minutes=10)):
            response = _post_token(client, data)
        assert response.json()["error"] == "invalid_grant"

    def test_refresh_rotates_and_bad_refresh_is_invalid_grant(
        self, client: Client
    ) -> None:
        """A refresh returns a new pair; an unknown refresh token fails."""
        grant = OAuthGrantFactory.create()
        first = _post_token(client, self._exchange_data(grant, _code_for(grant))).json()
        refreshed = _post_token(
            client,
            {
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": grant.client.client_id,
            },
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["refresh_token"] != first["refresh_token"]
        bad = _post_token(
            client,
            {
                "grant_type": "refresh_token",
                "refresh_token": "sd_rt_nope",
                "client_id": grant.client.client_id,
            },
        )
        assert bad.status_code == 400
        assert bad.json()["error"] == "invalid_grant"

    def test_unknown_client_is_401(self, client: Client) -> None:
        """An unknown client_id is invalid_client."""
        response = _post_token(client, {"grant_type": "authorization_code"})
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_client"

    def test_unsupported_grant_type(self, client: Client) -> None:
        """client_credentials is not supported."""
        grant = OAuthGrantFactory.create()
        response = _post_token(
            client,
            {"grant_type": "client_credentials", "client_id": grant.client.client_id},
        )
        assert response.json()["error"] == "unsupported_grant_type"

    def test_json_body_is_not_read(self, client: Client) -> None:
        """The endpoint reads form encoding; a JSON body has no client_id."""
        grant = OAuthGrantFactory.create()
        response = client.post(
            reverse("oauth:token"),
            data=json.dumps(self._exchange_data(grant, _code_for(grant))),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_get_is_405(self, client: Client) -> None:
        """The token endpoint is POST-only."""
        assert client.get(reverse("oauth:token")).status_code == 405

    def test_rate_limit(self, client: Client, settings: Settings) -> None:
        """The 61st token request in a minute from one IP is 429."""
        settings.RATELIMIT_ENABLE = True
        with freeze_time("2026-09-26 10:05:00"):
            for _ in range(60):
                _post_token(client, {"grant_type": "x"})
            limited = _post_token(client, {"grant_type": "x"})
        assert limited.status_code == 429


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


class TestRevoke:
    """POST /oauth/revoke/."""

    def test_revoking_a_refresh_token_disconnects(self, client: Client) -> None:
        """A revoked refresh token revokes the grant; the answer is 200."""
        grant = OAuthGrantFactory.create()
        pair, _ = issue_token_pair(grant, resource=RESOURCE, scope="mcp offline_access")
        response = client.post(
            reverse("oauth:revoke"),
            {"token": pair.refresh_token, "client_id": grant.client.client_id},
        )
        assert response.status_code == 200
        grant.refresh_from_db()
        assert grant.revoked_at is not None

    def test_unknown_token_is_still_200(self, client: Client) -> None:
        """RFC 7009: an unknown token is not an error."""
        response = client.post(reverse("oauth:revoke"), {"token": "sd_at_nope"})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Authorize + consent
# ---------------------------------------------------------------------------


class TestAuthorizeGet:
    """GET /oauth/authorize/."""

    def test_anonymous_is_sent_to_sign_in_with_encoded_next(
        self, client: Client
    ) -> None:
        """The whole authorize URL survives as ``next``."""
        oauth_client = OAuthClientFactory.create()
        url = _authorize_url(oauth_client)
        response = client.get(url)
        assert response.status_code == 302
        location = response["Location"]
        assert location.startswith(reverse("accounts:sign_in") + "?next=")
        next_value = parse_qs(urlsplit(location).query)["next"][0]
        assert next_value == url

    def test_sign_in_returns_to_authorize(self, client: Client) -> None:
        """Password sign-in with that ``next`` lands back on the consent page."""
        user = _verified_user()
        user.set_password("correct horse battery staple")
        user.save()
        url = _authorize_url(OAuthClientFactory.create())
        response = client.post(
            reverse("accounts:sign_in"),
            {
                "email": user.email,
                "password": "correct horse battery staple",
                "next": url,
            },
        )
        assert response.status_code == 302
        assert unquote(response["Location"]) == unquote(url)

    def test_unknown_client_renders_error_without_redirect(
        self, client: Client
    ) -> None:
        """An unknown client_id gets the error page, never a redirect."""
        response = client.get(
            f"{reverse('oauth:authorize')}?"
            + urlencode({"client_id": "nope", "redirect_uri": "https://evil.test/"})
        )
        assert response.status_code == 400
        assert "Location" not in response
        # The nav's sign-in link carries this page's own URL as ``next``, so
        # the host appears percent-encoded there; it must never be a target.
        assert b'href="https://evil.test' not in response.content
        assert b"url=https://evil.test" not in response.content

    def test_unregistered_redirect_renders_error(self, client: Client) -> None:
        """A redirect_uri the client never registered is not followed."""
        oauth_client = OAuthClientFactory.create()
        response = client.get(
            _authorize_url(oauth_client, redirect_uri="https://evil.test/cb")
        )
        assert response.status_code == 400
        assert "Location" not in response

    def test_consent_names_client_and_redirect_host(self, client: Client) -> None:
        """The consent page shows the app and where the code goes."""
        client.force_login(_verified_user())
        oauth_client = OAuthClientFactory.create(client_name="Claude")
        response = client.get(_authorize_url(oauth_client))
        assert response.status_code == 200
        html = response.content.decode()
        assert "Connect Claude to Snowdesk?" in html
        assert "claude.ai" in html
        assert 'data-testid="oauth-loopback-warning"' not in html
        assert 'name="csrfmiddlewaretoken"' in html
        assert response["Cache-Control"] == "no-store"

    def test_loopback_only_client_is_warned(self, client: Client) -> None:
        """A loopback-only client adds the warning callout."""
        client.force_login(_verified_user())
        oauth_client = OAuthClientFactory.create(
            redirect_uris=["http://localhost:1234/callback"]
        )
        response = client.get(
            _authorize_url(oauth_client, redirect_uri="http://localhost:5555/callback")
        )
        assert response.status_code == 200
        assert b'data-testid="oauth-loopback-warning"' in response.content

    def test_consent_page_is_not_shareable(self, client: Client) -> None:
        """The consent page renders no unfurl-card tags (sharing=False)."""
        client.force_login(_verified_user())
        response = client.get(_authorize_url(OAuthClientFactory.create()))
        for tag in (b'property="og:title"', b'property="og:description"'):
            assert tag not in response.content
        assert b'name="twitter:title"' not in response.content

    @pytest.mark.parametrize(
        ("override", "error"),
        [
            ({"response_type": "token"}, "unsupported_response_type"),
            ({"code_challenge_method": "plain"}, "invalid_request"),
            ({"code_challenge": "short"}, "invalid_request"),
            ({"scope": "mcp admin"}, "invalid_scope"),
            ({"resource": "https://evil.test/api/mcp/"}, "invalid_target"),
        ],
    )
    def test_bad_parameters_redirect_with_error(
        self, client: Client, override: dict, error: str
    ) -> None:
        """Parameter errors go back to the client with error and state."""
        client.force_login(_verified_user())
        response = client.get(_authorize_url(OAuthClientFactory.create(), **override))
        assert response.status_code == 302
        query = parse_qs(urlsplit(response["Location"]).query)
        assert response["Location"].startswith(CALLBACK)
        assert query["error"] == [error]
        assert query["state"] == ["xyz"]

    def test_missing_resource_defaults_to_the_mcp_url(self, client: Client) -> None:
        """A client that omits resource is treated as asking for /api/mcp/."""
        client.force_login(_verified_user())
        response = client.get(_authorize_url(OAuthClientFactory.create(), resource=""))
        assert response.status_code == 200

    def test_unverified_account_sees_verify_state(self, client: Client) -> None:
        """An unverified account is asked to verify, with no Allow button."""
        account = AccountFactory.create(is_verified=False)
        client.force_login(account.user)
        response = client.get(_authorize_url(OAuthClientFactory.create()))
        assert response.status_code == 200
        assert b"Verify your email first" in response.content
        assert b'data-testid="oauth-approve"' not in response.content

    def test_user_without_account_sees_verify_state(self, client: Client) -> None:
        """A bare auth.User (no Account profile) cannot approve either."""
        client.force_login(UserFactory.create())
        response = client.get(_authorize_url(OAuthClientFactory.create()))
        assert b"Verify your email first" in response.content


class TestAuthorizePost:
    """POST /oauth/authorize/ — the decision."""

    def test_approve_returns_code_and_state(self, client: Client) -> None:
        """Allow creates a grant and sends a code back with the state."""
        user = _verified_user()
        client.force_login(user)
        oauth_client = OAuthClientFactory.create()
        response = client.post(
            reverse("oauth:authorize"),
            _authorize_query(oauth_client) | {"decision": "approve"},
        )
        assert response.status_code == 200
        target = _meta_refresh_target(response)
        assert target.startswith(CALLBACK + "?")
        query = parse_qs(urlsplit(target).query)
        assert query["state"] == ["xyz"]
        assert query["code"][0].startswith("sd_ac_")
        grant = OAuthGrant.objects.get(user=user, client=oauth_client)
        assert grant.scope == "mcp offline_access"
        assert grant.codes.get().code_hash == hash_secret(query["code"][0])

    def test_approve_reactivates_a_revoked_grant(self, client: Client) -> None:
        """Approving again un-revokes the one grant row."""
        user = _verified_user()
        grant = OAuthGrantFactory.create(user=user, revoked_at=timezone.now())
        client.force_login(user)
        client.post(
            reverse("oauth:authorize"),
            _authorize_query(grant.client) | {"decision": "approve"},
        )
        grant.refresh_from_db()
        assert grant.revoked_at is None
        assert OAuthGrant.objects.count() == 1

    def test_deny_returns_access_denied(self, client: Client) -> None:
        """Deny sends access_denied back and creates nothing."""
        client.force_login(_verified_user())
        response = client.post(
            reverse("oauth:authorize"),
            _authorize_query(OAuthClientFactory.create()) | {"decision": "deny"},
        )
        query = parse_qs(urlsplit(_meta_refresh_target(response)).query)
        assert query["error"] == ["access_denied"]
        assert query["state"] == ["xyz"]
        assert not OAuthGrant.objects.exists()

    def test_post_rechecks_parameters(self, client: Client) -> None:
        """A tampered hidden field is caught on POST."""
        client.force_login(_verified_user())
        response = client.post(
            reverse("oauth:authorize"),
            _authorize_query(OAuthClientFactory.create(), code_challenge_method="plain")
            | {"decision": "approve"},
        )
        query = parse_qs(urlsplit(_meta_refresh_target(response)).query)
        assert query["error"] == ["invalid_request"]
        assert not OAuthGrant.objects.exists()

    def test_post_requires_csrf(self) -> None:
        """The consent POST is CSRF-protected."""
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(_verified_user())
        response = strict.post(
            reverse("oauth:authorize"),
            _authorize_query(OAuthClientFactory.create()) | {"decision": "approve"},
        )
        assert response.status_code == 403
        assert not OAuthGrant.objects.exists()

    def test_anonymous_post_is_refused(self, client: Client) -> None:
        """A POST after the session ended gets the error page."""
        response = client.post(
            reverse("oauth:authorize"),
            _authorize_query(OAuthClientFactory.create()) | {"decision": "approve"},
        )
        assert response.status_code == 403
        assert not OAuthGrant.objects.exists()

    def test_unverified_post_cannot_approve(self, client: Client) -> None:
        """An unverified account's POST creates no grant."""
        client.force_login(AccountFactory.create(is_verified=False).user)
        response = client.post(
            reverse("oauth:authorize"),
            _authorize_query(OAuthClientFactory.create()) | {"decision": "approve"},
        )
        assert response.status_code == 403
        assert not OAuthGrant.objects.exists()


# ---------------------------------------------------------------------------
# Settings-page Disconnect
# ---------------------------------------------------------------------------


class TestGrantRevoke:
    """POST /oauth/grants/<uuid>/revoke/."""

    def _url(self, grant: OAuthGrant) -> str:
        """Return the Disconnect URL for ``grant``."""
        return reverse("oauth:grant_revoke", kwargs={"grant_uuid": grant.uuid})

    def test_owner_disconnects(self, client: Client) -> None:
        """The owner's HTMX POST revokes the grant and returns an empty 200."""
        grant = OAuthGrantFactory.create()
        client.force_login(grant.user)
        response = client.post(self._url(grant), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert response.content == b""
        grant.refresh_from_db()
        assert grant.revoked_at is not None

    def test_other_users_grant_is_404(self, client: Client) -> None:
        """Another user's grant is not found, and survives."""
        grant = OAuthGrantFactory.create()
        client.force_login(UserFactory.create())
        response = client.post(self._url(grant), HTTP_HX_REQUEST="true")
        assert response.status_code == 404
        grant.refresh_from_db()
        assert grant.revoked_at is None

    def test_requires_htmx(self, client: Client) -> None:
        """A plain POST is 400."""
        grant = OAuthGrantFactory.create()
        client.force_login(grant.user)
        assert client.post(self._url(grant)).status_code == 400

    def test_anonymous_is_403(self, client: Client) -> None:
        """No session, no disconnect."""
        grant = OAuthGrantFactory.create()
        response = client.post(self._url(grant), HTTP_HX_REQUEST="true")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def _mcp_ping(client: Client, token: str) -> _Response:
    """POST a JSON-RPC ping to the MCP endpoint with ``token``."""
    return client.post(
        reverse("api:mcp:endpoint"),
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def test_full_round_trip(client: Client) -> None:
    """register → authorize → token → MCP → refresh → revoke → 401."""
    machine = Client()
    registered = machine.post(
        reverse("oauth:register"),
        data=json.dumps({"client_name": "Claude", "redirect_uris": [CALLBACK]}),
        content_type="application/json",
    ).json()
    oauth_client = OAuthClient.objects.get(client_id=registered["client_id"])

    client.force_login(_verified_user())
    assert client.get(_authorize_url(oauth_client)).status_code == 200
    approved = client.post(
        reverse("oauth:authorize"),
        _authorize_query(oauth_client) | {"decision": "approve"},
    )
    code = parse_qs(urlsplit(_meta_refresh_target(approved)).query)["code"][0]

    tokens = _post_token(
        machine,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": oauth_client.client_id,
            "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER,
            "resource": RESOURCE,
        },
    ).json()
    assert _mcp_ping(machine, tokens["access_token"]).status_code == 200

    refreshed = _post_token(
        machine,
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": oauth_client.client_id,
        },
    ).json()
    assert _mcp_ping(machine, refreshed["access_token"]).status_code == 200

    machine.post(
        reverse("oauth:revoke"),
        {"token": refreshed["refresh_token"], "client_id": oauth_client.client_id},
    )
    assert _mcp_ping(machine, refreshed["access_token"]).status_code == 401
