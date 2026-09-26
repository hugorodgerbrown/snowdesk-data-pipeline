"""
tests/oauth/services/test_cimd.py — Tests for the CIMD fetch and its SSRF guard.

``socket.getaddrinfo`` and ``requests.get`` are both patched: no test
resolves a name or opens a connection.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest

from apps.oauth.services import cimd
from apps.oauth.services.cimd import CimdError, fetch_client_metadata

URL = "https://claude.ai/oauth/claude-code-client-metadata"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def _addrinfo(address: str) -> list[tuple[Any, ...]]:
    """Return a getaddrinfo-shaped answer for one address."""
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]


def _response(status: int = 200, body: bytes | None = None) -> mock.Mock:
    """Return a streamed-response stand-in."""
    if body is None:
        body = json.dumps(
            {"client_id": URL, "client_name": "Claude", "redirect_uris": [CALLBACK]}
        ).encode()
    response = mock.Mock()
    response.status_code = status
    response.iter_content.return_value = iter(
        [body[i : i + 8192] for i in range(0, len(body), 8192)] or [b""]
    )
    return response


@pytest.fixture
def public_dns() -> Iterator[mock.Mock]:
    """Resolve every host to a public address."""
    with mock.patch.object(
        cimd.socket, "getaddrinfo", return_value=_addrinfo("160.79.104.10")
    ) as patched:
        yield patched


def test_valid_document_is_accepted(public_dns: mock.Mock) -> None:
    """A matching document yields the name and redirect URIs."""
    with mock.patch.object(cimd.requests, "get", return_value=_response()) as get:
        metadata = fetch_client_metadata(URL)
    assert metadata.client_name == "Claude"
    assert metadata.redirect_uris == [CALLBACK]
    kwargs = get.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == 3
    assert kwargs["stream"] is True


def test_client_id_mismatch_is_refused(public_dns: mock.Mock) -> None:
    """A document naming another client_id is refused."""
    body = json.dumps({"client_id": URL + "x", "redirect_uris": [CALLBACK]}).encode()
    with mock.patch.object(cimd.requests, "get", return_value=_response(body=body)):
        with pytest.raises(CimdError, match="different client_id"):
            fetch_client_metadata(URL)


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "::1",
        "::ffff:10.0.0.1",
        "::",
        "224.0.0.1",
    ],
)
def test_non_public_address_is_refused(address: str) -> None:
    """Private, loopback, link-local, unspecified and multicast are refused."""
    with (
        mock.patch.object(cimd.socket, "getaddrinfo", return_value=_addrinfo(address)),
        mock.patch.object(cimd.requests, "get") as get,
    ):
        with pytest.raises(CimdError, match="non-public"):
            fetch_client_metadata(URL)
    get.assert_not_called()


def test_unresolvable_host_is_refused() -> None:
    """A name that does not resolve is refused before any fetch."""
    with mock.patch.object(cimd.socket, "getaddrinfo", side_effect=socket.gaierror):
        with pytest.raises(CimdError, match="does not resolve"):
            fetch_client_metadata(URL)


@pytest.mark.parametrize(
    "url",
    [
        "http://claude.ai/oauth/metadata",
        "https://claude.ai/",
        "https://claude.ai",
        "https://user@claude.ai/meta",
        "https://claude.ai:bad/meta",
    ],
)
def test_bad_urls_are_refused(url: str) -> None:
    """http, a root path, credentials and a bad port are all refused."""
    with mock.patch.object(cimd.requests, "get") as get:
        with pytest.raises(CimdError):
            fetch_client_metadata(url)
    get.assert_not_called()


def test_oversize_body_is_refused(public_dns: mock.Mock) -> None:
    """A body over 64 KB is refused."""
    body = b"{" + b" " * (cimd.MAX_DOCUMENT_BYTES + 10) + b"}"
    with mock.patch.object(cimd.requests, "get", return_value=_response(body=body)):
        with pytest.raises(CimdError, match="too large"):
            fetch_client_metadata(URL)


def test_redirect_is_not_followed(public_dns: mock.Mock) -> None:
    """A 3xx is a failure, not a hop."""
    with mock.patch.object(cimd.requests, "get", return_value=_response(status=302)):
        with pytest.raises(CimdError, match="HTTP 302"):
            fetch_client_metadata(URL)


def test_network_error_is_refused(public_dns: mock.Mock) -> None:
    """A timeout or connection error becomes a CimdError."""
    with mock.patch.object(
        cimd.requests, "get", side_effect=cimd.requests.Timeout("slow")
    ):
        with pytest.raises(CimdError, match="could not be fetched"):
            fetch_client_metadata(URL)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        json.dumps({"client_id": URL, "redirect_uris": []}).encode(),
        json.dumps(
            {"client_id": URL, "redirect_uris": ["http://evil.test/cb"]}
        ).encode(),
    ],
)
def test_invalid_documents_are_refused(public_dns: mock.Mock, body: bytes) -> None:
    """Non-JSON, non-object and unusable redirect_uris are refused."""
    with mock.patch.object(cimd.requests, "get", return_value=_response(body=body)):
        with pytest.raises(CimdError):
            fetch_client_metadata(URL)


def test_client_name_is_trimmed(public_dns: mock.Mock) -> None:
    """A long name is cut to 100 characters; a non-string one is dropped."""
    body = json.dumps(
        {"client_id": URL, "client_name": "x" * 300, "redirect_uris": [CALLBACK]}
    ).encode()
    with mock.patch.object(cimd.requests, "get", return_value=_response(body=body)):
        assert len(fetch_client_metadata(URL).client_name) == 100
    body = json.dumps(
        {"client_id": URL, "client_name": 7, "redirect_uris": [CALLBACK]}
    ).encode()
    with mock.patch.object(cimd.requests, "get", return_value=_response(body=body)):
        assert fetch_client_metadata(URL).client_name == ""
