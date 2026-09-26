"""
apps/oauth/services/cimd.py — Fetch a Client ID Metadata Document, safely.

A CIMD client identifies itself by an HTTPS URL, and the authorization
server fetches that URL to learn the client's name and redirect URIs
(draft-ietf-oauth-client-id-metadata-document). Claude prefers this to
Dynamic Client Registration when the AS metadata advertises it.

The URL is chosen by whoever starts an authorize request, so the fetch is a
server-side request to an attacker-chosen address. The guard:

* Only ``https`` URLs with a non-root path, no credentials, no fragment.
* The host is resolved with ``socket.getaddrinfo`` and refused if any
  address is private, loopback, link-local, reserved, multicast or
  unspecified.
* ``requests.get`` with a 3-second timeout, redirects NOT followed, and the
  body streamed and capped at 64 KB.
* The body must be a JSON object whose ``client_id`` equals the URL and
  whose ``redirect_uris`` is a non-empty list of registrable URIs.

**Residual risk: DNS rebinding.** ``requests`` resolves the host again when
it connects, so a name that answers with a public address to the check and
a private one to the connection passes the guard. Closing that needs the
connection pinned to the checked address; the exposure is a GET whose body
must parse as a matching metadata document before anything is kept, and the
response is never shown to the requester.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from apps.oauth.services.redirects import is_registrable

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS: int = 3
MAX_DOCUMENT_BYTES: int = 64 * 1024
CLIENT_NAME_MAX: int = 100


class CimdError(Exception):
    """A client ID metadata document could not be fetched or was invalid."""


@dataclass(frozen=True)
class ClientMetadata:
    """The two facts Snowdesk keeps from a metadata document."""

    client_name: str
    redirect_uris: list[str]


def trimmed_client_name(raw: object) -> str:
    """Return a client-supplied name cut to ``CLIENT_NAME_MAX``, or ``""``.

    Args:
        raw: The ``client_name`` value from a document or payload.

    Returns:
        The stripped, truncated name; ``""`` for a non-string.

    """
    return raw.strip()[:CLIENT_NAME_MAX] if isinstance(raw, str) else ""


def _validate_url(url: str) -> tuple[str, int]:
    """Return the (host, port) of a fetchable client_id URL, or raise.

    Args:
        url: The candidate client_id.

    Returns:
        The hostname and port to resolve.

    Raises:
        CimdError: For anything but an https URL with a path.

    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or 443
    except ValueError as exc:
        raise CimdError("client_id is not a valid URL") from exc
    if parts.scheme != "https":
        raise CimdError("client_id must be an https URL")
    if not host or parts.username or parts.password or parts.fragment:
        raise CimdError("client_id must name a host, with no credentials or fragment")
    if parts.path in ("", "/"):
        raise CimdError("client_id must have a path")
    return host, port


def _is_forbidden_address(address: str) -> bool:
    """Return True for an address a server-side fetch must not reach.

    Args:
        address: An IPv4 or IPv6 address as ``getaddrinfo`` returns it.

    Returns:
        True for private, loopback, link-local, reserved, multicast or
        unspecified addresses, and for anything that does not parse.

    """
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _assert_public_host(host: str, port: int) -> None:
    """Raise unless every address ``host`` resolves to is public.

    Args:
        host: The hostname to resolve.
        port: The port, passed to ``getaddrinfo``.

    Raises:
        CimdError: When resolution fails or any address is forbidden.

    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise CimdError("client_id host does not resolve") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses or any(_is_forbidden_address(str(a)) for a in addresses):
        raise CimdError("client_id host resolves to a non-public address")


def _read_capped(response: requests.Response) -> bytes:
    """Read a streamed body, refusing one larger than the cap.

    Args:
        response: A streamed response.

    Returns:
        The body bytes.

    Raises:
        CimdError: When the body exceeds ``MAX_DOCUMENT_BYTES``.

    """
    body = b""
    for chunk in response.iter_content(chunk_size=8192):
        body += chunk
        if len(body) > MAX_DOCUMENT_BYTES:
            raise CimdError("client metadata document is too large")
    return body


def fetch_client_metadata(url: str) -> ClientMetadata:
    """Fetch and validate the client ID metadata document at ``url``.

    Args:
        url: The client_id, which is the document's URL.

    Returns:
        The client's name and redirect URIs.

    Raises:
        CimdError: For a refused URL or host, a failed or redirected fetch,
            an oversize or non-JSON body, or a document that does not
            describe this client_id.

    """
    host, port = _validate_url(url)
    _assert_public_host(host, port)

    try:
        response = requests.get(
            url,
            timeout=FETCH_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
            headers={"Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise CimdError("client metadata document could not be fetched") from exc

    try:
        if response.status_code != 200:
            raise CimdError(
                f"client metadata document returned HTTP {response.status_code}"
            )
        body = _read_capped(response)
    finally:
        response.close()

    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CimdError("client metadata document is not JSON") from exc
    if not isinstance(document, dict):
        raise CimdError("client metadata document is not a JSON object")
    if document.get("client_id") != url:
        raise CimdError("client metadata document names a different client_id")

    redirect_uris = document.get("redirect_uris")
    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or not all(is_registrable(u) for u in redirect_uris)
    ):
        raise CimdError("client metadata document has no usable redirect_uris")

    return ClientMetadata(
        client_name=trimmed_client_name(document.get("client_name")),
        redirect_uris=list(redirect_uris),
    )
