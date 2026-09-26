"""
tests/oauth/services/test_clients.py — Tests for DCR and client resolution.

The CIMD fetch itself is covered in ``test_cimd.py``; here it is patched,
and the tests cover the 24-hour cache and the row it writes.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.oauth.models import OAuthClient
from apps.oauth.services import clients
from apps.oauth.services.cimd import CimdError, ClientMetadata
from apps.oauth.services.clients import (
    RegistrationError,
    register_client,
    registration_response,
    resolve_client,
)
from tests.factories import OAuthClientFactory

pytestmark = pytest.mark.django_db

URL = "https://claude.ai/oauth/claude-code-client-metadata"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
METADATA = ClientMetadata(client_name="Claude", redirect_uris=[CALLBACK])


class TestRegisterClient:
    """Dynamic Client Registration."""

    def test_valid_payload_creates_public_client(self) -> None:
        """A valid payload creates a DCR row answered as a public client."""
        client = register_client(
            {
                "client_name": "Claude",
                "redirect_uris": [CALLBACK],
                "token_endpoint_auth_method": "client_secret_post",
            }
        )
        assert client.kind == OAuthClient.KIND.DCR
        assert client.client_id.startswith("sd_client_")
        body = registration_response(client)
        assert body["token_endpoint_auth_method"] == "none"
        assert body["redirect_uris"] == [CALLBACK]
        assert "client_secret" not in body

    def test_name_is_trimmed(self) -> None:
        """client_name is cut to 100 characters."""
        client = register_client(
            {"client_name": "y" * 500, "redirect_uris": [CALLBACK]}
        )
        assert len(client.client_name) == 100

    @pytest.mark.parametrize(
        "payload",
        [
            {"redirect_uris": ["http://evil.test/cb"]},
            {"redirect_uris": []},
            {"redirect_uris": "https://claude.ai/cb"},
            {},
            {"redirect_uris": [f"https://x.test/{i}" for i in range(11)]},
        ],
    )
    def test_bad_redirect_uris_are_refused(self, payload: dict) -> None:
        """Unusable redirect_uris raise invalid_redirect_uri."""
        with pytest.raises(RegistrationError) as exc:
            register_client(payload)
        assert exc.value.error == "invalid_redirect_uri"
        assert not OAuthClient.objects.exists()

    def test_non_object_is_refused(self) -> None:
        """A JSON array is not a registration."""
        with pytest.raises(RegistrationError) as exc:
            register_client([CALLBACK])
        assert exc.value.error == "invalid_client_metadata"

    def test_unsupported_grant_type_is_refused(self) -> None:
        """Only authorization_code and refresh_token may be requested."""
        with pytest.raises(RegistrationError) as exc:
            register_client(
                {"redirect_uris": [CALLBACK], "grant_types": ["client_credentials"]}
            )
        assert exc.value.error == "invalid_client_metadata"


class TestResolveClient:
    """resolve_client for DCR, LOCAL and CIMD client ids."""

    def test_dcr_client_is_a_row_lookup(self) -> None:
        """A registered id returns its row; an unknown one returns None."""
        client = OAuthClientFactory.create()
        assert resolve_client(client.client_id) == client
        assert resolve_client("sd_client_nope") is None
        assert resolve_client("") is None

    def test_cimd_fetch_creates_a_cached_row(self) -> None:
        """The first resolution fetches and writes a CIMD row."""
        with mock.patch.object(
            clients, "fetch_client_metadata", return_value=METADATA
        ) as fetch:
            client = resolve_client(URL)
        fetch.assert_called_once_with(URL)
        assert client is not None
        assert client.kind == OAuthClient.KIND.CIMD
        assert client.redirect_uris == [CALLBACK]
        assert client.metadata_fetched_at is not None

    def test_cimd_cache_hit_within_24h(self) -> None:
        """A row fetched under 24 hours ago is used without a fetch."""
        cached = OAuthClientFactory.create(
            client_id=URL,
            kind=OAuthClient.KIND.CIMD,
            metadata_fetched_at=timezone.now() - timedelta(hours=23),
        )
        with mock.patch.object(clients, "fetch_client_metadata") as fetch:
            assert resolve_client(URL) == cached
        fetch.assert_not_called()

    def test_stale_cimd_row_is_refetched(self) -> None:
        """A row older than 24 hours is refreshed from the document."""
        OAuthClientFactory.create(
            client_id=URL,
            kind=OAuthClient.KIND.CIMD,
            client_name="Old",
            metadata_fetched_at=timezone.now() - timedelta(hours=25),
        )
        with mock.patch.object(clients, "fetch_client_metadata", return_value=METADATA):
            client = resolve_client(URL)
        assert client is not None
        assert client.client_name == "Claude"
        assert OAuthClient.objects.count() == 1

    def test_failed_fetch_returns_none(self) -> None:
        """An unusable document resolves to no client."""
        with mock.patch.object(
            clients, "fetch_client_metadata", side_effect=CimdError("no")
        ):
            assert resolve_client(URL) is None

    def test_failed_fetch_is_not_retried_for_five_minutes(self) -> None:
        """A failed fetch is remembered, so repeating the request fetches nothing."""
        cache.clear()
        with mock.patch.object(
            clients, "fetch_client_metadata", side_effect=CimdError("no")
        ) as fetch:
            assert resolve_client(URL) is None
            assert resolve_client(URL) is None
        fetch.assert_called_once_with(URL)
        cache.clear()

    def test_https_id_never_resolves_a_non_cimd_row(self) -> None:
        """A DCR row cannot be claimed by an https client_id, nor the reverse."""
        OAuthClientFactory.create(client_id=URL, kind=OAuthClient.KIND.DCR)
        with mock.patch.object(clients, "fetch_client_metadata") as fetch:
            assert resolve_client(URL) is None
        fetch.assert_not_called()
        cimd_row = OAuthClientFactory.create(
            client_id="plain-id", kind=OAuthClient.KIND.CIMD
        )
        assert resolve_client(cimd_row.client_id) is None
