"""
tests/oauth/test_validators.py — Tests for apps.oauth.validators (SNOW-1197).

The client-credentials grant is issued only to a service client: a
confidential client-credentials application a member of staff created by
hand. mcp-auth enforces that at registration and at the token endpoint
(v0.1.8, covered for every adopter by its MCPAuthContract); these run the
real endpoints to pin the two cases Snowdesk's data feed depends on.
"""

from __future__ import annotations

import json

import pytest
from django.core.cache import cache
from django.test import Client
from oauth2_provider.models import AccessToken, Application

from apps.oauth.validators import is_service_client
from tests.factories import OAuthApplicationFactory

FEED = "http://testserver/api/data/v1/"


@pytest.fixture(autouse=True)
def clear_caches() -> None:
    """Clear the cache, where the token endpoint's rate-limit counters live."""
    cache.clear()


def _service_client(**overrides: object) -> tuple[Application, str]:
    """Create a service client and return it with its plain-text secret."""
    secret = "s3cret-for-tests-only"
    fields: dict[str, object] = {
        "name": "Staging",
        "client_type": Application.CLIENT_CONFIDENTIAL,
        "authorization_grant_type": Application.GRANT_CLIENT_CREDENTIALS,
        "redirect_uris": "",
        "client_secret": secret,
    }
    app = OAuthApplicationFactory.create(**(fields | overrides))
    return app, secret


def _token_request(client: Client, client_id: str, secret: str) -> dict:
    """POST a client-credentials token request and return the decoded body."""
    response = client.post(
        "/oauth/token/",
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
            "scope": "data",
            "resource": FEED,
        },
    )
    body: dict = json.loads(response.content)
    body["_status"] = response.status_code
    return body


@pytest.mark.django_db
class TestTokenEndpoint:
    """What /oauth/token/ issues for the client-credentials grant."""

    def test_service_client_gets_a_token_bound_to_the_feed(
        self, client: Client
    ) -> None:
        """A hand-made client-credentials app is issued a bound, userless token."""
        app, secret = _service_client()
        body = _token_request(client, app.client_id, secret)
        assert body["_status"] == 200
        assert body["scope"] == "data"
        token = AccessToken.objects.get(application=app)
        assert token.user is None
        assert token.resource == [FEED]

    def test_open_registration_for_client_credentials_is_refused(
        self, client: Client
    ) -> None:
        """A DCR registration asking for client_credentials is refused outright.

        Registration is open to any allowlisted callback, so it must never
        create a client that can mint a userless token for the feed.
        """
        response = client.post(
            "/oauth/register/",
            data=json.dumps(
                {
                    "client_name": "Anyone",
                    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                    "grant_types": ["client_credentials"],
                    "token_endpoint_auth_method": "client_secret_post",
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert json.loads(response.content)["error"] == "invalid_client_metadata"
        assert not Application.objects.exists()


@pytest.mark.django_db
class TestIsServiceClient:
    """The definition shared by the token endpoint and the feed."""

    def test_service_client(self) -> None:
        """Manual, confidential, client credentials: a service client."""
        app, _ = _service_client()
        assert is_service_client(app)

    def test_none(self) -> None:
        """No application is not a service client."""
        assert not is_service_client(None)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"registration_source": Application.RegistrationSource.DCR},
            {"registration_source": Application.RegistrationSource.CIMD},
            {"client_type": Application.CLIENT_PUBLIC},
        ],
    )
    def test_any_other_app_is_not(self, overrides: dict) -> None:
        """A registered, public or non-client-credentials app is not one."""
        app, _ = _service_client(**overrides)
        assert not is_service_client(app)

    def test_authorization_code_app_is_not(self) -> None:
        """A person's MCP connection is not a service client."""
        assert not is_service_client(OAuthApplicationFactory.create())
