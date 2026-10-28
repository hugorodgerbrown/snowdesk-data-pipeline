"""
tests/data_feed/test_views.py — Tests for apps.data_feed.views and auth (SNOW-1197).

Who may read the feed is the whole security story, so most of this file is
refusals: no token, a person's MCP token, a token without the scope, one
bound elsewhere or to nothing, and a token whose application is not a
service client. Then the happy path, the 400 for a bad parameter, the 405
for a write verb, and that nothing is cacheable.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from django.test import Client
from oauth2_provider.models import Application

from tests.data_feed.conftest import FEED
from tests.factories import (
    AccountFactory,
    LocationFactory,
    OAuthAccessTokenFactory,
    OAuthApplicationFactory,
    ResortLocationFactory,
    SourceWeatherFactory,
)

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as _Response

URL = "/api/data/v1/locations/"


def _get(client: Client, token: str | None, url: str = URL, **params: str) -> _Response:
    """GET ``url`` with ``token`` as the bearer, if given."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(url, params, headers=headers)


@pytest.mark.django_db
class TestRefusals:
    """Every credential that is not a service client's bound token is refused."""

    def test_no_token_is_a_401_naming_the_resource_metadata(
        self, client: Client
    ) -> None:
        """The challenge names this path's protected-resource metadata."""
        response = _get(client, None)
        assert response.status_code == 401
        assert (
            "/.well-known/oauth-protected-resource/api/data/v1/locations/"
            in response["WWW-Authenticate"]
        )

    def test_a_persons_mcp_token_is_refused(self, client: Client) -> None:
        """An authorization-code token, even with the data scope, is refused."""
        user = AccountFactory.create().user
        token = OAuthAccessTokenFactory.create(
            user=user, scope="data", resource=[FEED]
        ).raw_token
        assert _get(client, token).status_code == 401

    def test_registered_client_credentials_app_is_refused(self, client: Client) -> None:
        """A DCR-registered client-credentials app is not a service client."""
        app = OAuthApplicationFactory.create(
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
            registration_source=Application.RegistrationSource.DCR,
        )
        token = OAuthAccessTokenFactory.create(
            user=None, application=app, scope="data", resource=[FEED]
        ).raw_token
        assert _get(client, token).status_code == 401

    def test_token_without_the_data_scope_is_refused(
        self, client: Client, service_client: Application
    ) -> None:
        """A scope error is the 403 of mcp-auth's challenge."""
        token = OAuthAccessTokenFactory.create(
            user=None, application=service_client, scope="mcp", resource=[FEED]
        ).raw_token
        assert _get(client, token).status_code == 403

    def test_token_bound_to_nothing_is_refused(
        self, client: Client, service_client: Application
    ) -> None:
        """An unbound token would be good anywhere, so it is good nowhere here."""
        token = OAuthAccessTokenFactory.create(
            user=None, application=service_client, scope="data", resource=[]
        ).raw_token
        assert _get(client, token).status_code == 401

    def test_token_bound_elsewhere_is_refused(
        self, client: Client, service_client: Application
    ) -> None:
        """A token bound to the MCP endpoint does not open the feed."""
        token = OAuthAccessTokenFactory.create(
            user=None,
            application=service_client,
            scope="data",
            resource=["http://testserver/api/mcp/"],
        ).raw_token
        assert _get(client, token).status_code == 401

    def test_feed_token_does_not_open_the_mcp_endpoint(
        self, client: Client, feed_token: str
    ) -> None:
        """A service client's token is not an MCP reader's, wherever it is sent."""
        response = client.post(
            "/api/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {feed_token}"},
        )
        assert response.status_code in (401, 403)

    def test_userless_mcp_token_is_refused_by_the_mcp_endpoint(
        self, client: Client, service_client: Application
    ) -> None:
        """mcp-auth's connect rule refuses a token with no user, even one for /api/mcp/."""
        token = OAuthAccessTokenFactory.create(
            user=None,
            application=service_client,
            scope="mcp",
            resource=["http://testserver/api/mcp/"],
        ).raw_token
        response = client.post(
            "/api/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401


@pytest.mark.django_db
class TestAnswer:
    """What an admitted client gets."""

    @pytest.mark.parametrize("feed", ["bulletins", "weather", "locations", "resorts"])
    def test_every_feed_answers(
        self, client: Client, feed_token: str, feed: str
    ) -> None:
        """Each feed answers 200 with an empty first page on an empty database."""
        response = _get(client, feed_token, f"/api/data/v1/{feed}/")
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["feed"] == feed
        assert body["version"] == 1
        assert body["next_cursor"] is None
        assert isinstance(body["results"], list)

    def test_response_is_never_cached(self, client: Client, feed_token: str) -> None:
        """A per-client read of live data is no-store."""
        assert _get(client, feed_token)["Cache-Control"] == "no-store"

    def test_items_are_served(self, client: Client, feed_token: str) -> None:
        """A curated location reaches the feed; an unlinked one does not."""
        LocationFactory.create(name="Not linked to anything")
        link = ResortLocationFactory.create(location__name="Attelas")
        body = json.loads(_get(client, feed_token).content)
        assert body["count"] == 1
        assert [i["uuid"] for i in body["results"]] == [str(link.location.uuid)]

    @pytest.mark.parametrize(
        "params",
        [
            {"since": "yesterday"},
            {"since": "2026-10-01T00:00:00"},
            {"limit": "x"},
            {"limit": "0"},
            {"cursor": "!!!"},
        ],
    )
    def test_bad_parameter_is_a_400(
        self, client: Client, feed_token: str, params: dict[str, str]
    ) -> None:
        """A parameter the feed cannot use is a 400 naming it."""
        response = _get(client, feed_token, **params)
        assert response.status_code == 400
        error = json.loads(response.content)["error"]
        assert error.startswith(next(iter(params)))
        # A fixed message: the client's own input is never echoed back.
        assert next(iter(params.values())) not in error

    def test_post_is_a_405(self, client: Client, feed_token: str) -> None:
        """The feed is read-only."""
        response = client.post(URL, headers={"Authorization": f"Bearer {feed_token}"})
        assert response.status_code == 405

    def test_the_v2_weather_feed_answers_a_token_bound_to_v2(
        self, client: Client, service_client: Application
    ) -> None:
        """SNOW-1253: /api/data/v2/weather/ serves the service's rows as version 2."""
        row = SourceWeatherFactory.create()
        token = OAuthAccessTokenFactory.create(
            user=None,
            application=service_client,
            scope="data",
            resource=["http://testserver/api/data/v2/"],
        ).raw_token
        response = _get(client, token, "/api/data/v2/weather/")
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["version"] == 2
        assert [i["location"] for i in body["results"]] == [str(row.location.uuid)]

    def test_a_v1_token_does_not_open_v2(self, client: Client, feed_token: str) -> None:
        """A token is bound to its URL prefix, so v2 needs a v2 token."""
        assert _get(client, feed_token, "/api/data/v2/weather/").status_code == 401

    def test_v2_windows_a_feed_by_day(
        self, client: Client, service_client: Application
    ) -> None:
        """SNOW-1272: from/to narrow a dated feed, and a bad date is a 400."""
        token = OAuthAccessTokenFactory.create(
            user=None,
            application=service_client,
            scope="data",
            resource=["http://testserver/api/data/v2/"],
        ).raw_token
        url = "/api/data/v2/bulletins/?from=2026-02-09&to=2026-02-15"
        assert _get(client, token, url).status_code == 200
        bad = _get(client, token, "/api/data/v2/bulletins/?from=yesterday")
        assert bad.status_code == 400
        assert json.loads(bad.content)["error"] == "from must be an ISO 8601 date"
        undated = _get(client, token, "/api/data/v2/passes/?from=2026-02-09")
        assert undated.status_code == 400

    @pytest.mark.parametrize(
        "name",
        [
            "locations",
            "passes",
            "resorts",
            "day-ratings",
            "bulletins",
            "ski-network",
            "ski-tours",
        ],
    )
    def test_the_v2_reference_feeds_answer(
        self, client: Client, service_client: Application, name: str
    ) -> None:
        """SNOW-1271: places, passes and resorts are served from the service."""
        token = OAuthAccessTokenFactory.create(
            user=None,
            application=service_client,
            scope="data",
            resource=["http://testserver/api/data/v2/"],
        ).raw_token
        response = _get(client, token, f"/api/data/v2/{name}/")
        assert response.status_code == 200
        body = json.loads(response.content)
        assert (body["feed"], body["version"]) == (name, 2)

    def test_unknown_feed_is_a_404(self, client: Client, feed_token: str) -> None:
        """Only the four feeds are routed."""
        assert _get(client, feed_token, "/api/data/v1/users/").status_code == 404
