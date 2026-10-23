"""
apps/data_feed/client.py — Reading the production data feed over HTTPS.

``FeedClient`` is the client side of ``/api/data/v1/``: it gets an access
token with the OAuth 2 client-credentials grant, then walks a feed page by
page, following ``next_cursor`` until the server says there is no more.

Three rules, each a refusal rather than a warning:

* **HTTPS only.** The feed carries a bearer token and the token request
  carries the client secret, so a plain-HTTP URL is refused — except for a
  loopback host, which is how the feed is exercised against a local
  ``runserver``.
* **The token is bound to the feed URL** (RFC 8707 ``resource``). The server
  refuses an unbound token, and a bound one opens nothing outside
  ``/api/data/v1/``.
* **One token per run, renewed once on a 401.** Tokens last an hour; a long
  backfill can outlive one, so a 401 mid-walk fetches a fresh token and
  retries that page once. A second 401 is a real refusal and raises.

Plain ``requests`` with a module-level timeout, as every other outbound
call in the project.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests

from apps.data_feed import contract

logger = logging.getLogger(__name__)

# Seconds. A page is at most MAX_LIMIT rows from one indexed query.
REQUEST_TIMEOUT = 60

# Hosts a plain-HTTP feed URL is allowed for: a local runserver.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})


class FeedError(Exception):
    """The feed could not be read: refused, unreachable, or malformed."""


@dataclass(frozen=True)
class FeedPage:
    """One page of a feed.

    Attributes:
        results: The page's items.
        count: How many items the whole walk returns — sent on the first
            page only, None on the rest.

    """

    results: list[dict[str, Any]]
    count: int | None


def check_feed_url(url: str) -> str:
    """Return ``url`` normalised to end in a slash, or raise if it is unusable.

    Args:
        url: The configured ``DATA_FEED_URL``.

    Returns:
        The URL with exactly one trailing slash.

    Raises:
        FeedError: It is empty, not absolute, or plain HTTP to a remote host.

    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        raise FeedError(f"DATA_FEED_URL {url!r} is not an absolute URL")
    if parts.scheme != "https" and not (
        parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS
    ):
        raise FeedError(
            f"DATA_FEED_URL {url!r} is not HTTPS. The feed carries a bearer "
            "token and the token request carries the client secret; plain "
            "HTTP is allowed only for a loopback host."
        )
    return url.rstrip("/") + "/"


class FeedClient:
    """A client-credentials session against one data feed.

    Args:
        base_url: The feed's base URL, e.g. ``https://snowdesk.app/api/data/v1/``.
        client_id: The service client's id.
        client_secret: The service client's secret.
        session: An optional ``requests.Session`` (tests pass a stub).

    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        session: requests.Session | None = None,
    ) -> None:
        """Validate the URL and hold the credentials; no request is made yet."""
        self.base_url = check_feed_url(base_url)
        parts = urlsplit(self.base_url)
        self.token_url = f"{parts.scheme}://{parts.netloc}/oauth/token/"
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session or requests.Session()
        self._token: str | None = None

    def _fetch_token(self) -> str:
        """Exchange the client credentials for a token bound to the feed.

        Returns:
            The access token.

        Raises:
            FeedError: The server refused the credentials or answered badly.

        """
        try:
            response = self._session.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "scope": contract.SCOPE,
                    "resource": self.base_url,
                },
                auth=(self._client_id, self._client_secret),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise FeedError(f"token request to {self.token_url} failed: {exc}") from exc
        if response.status_code != 200:
            raise FeedError(
                f"token request refused ({response.status_code}): {response.text[:200]}"
            )
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise FeedError("token response carried no access_token")
        logger.debug("data feed: token issued by %s", self.token_url)
        return token

    def _get(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """GET one page, renewing the token once if it has expired.

        Args:
            url: The feed URL.
            params: Query parameters.

        Returns:
            The decoded JSON body.

        Raises:
            FeedError: Refused twice, a non-200 answer, or a body that is not
                a JSON object.

        """
        for attempt in (1, 2):
            if self._token is None:
                self._token = self._fetch_token()
            try:
                response = self._session.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                raise FeedError(f"GET {url} failed: {exc}") from exc
            if response.status_code == 401 and attempt == 1:
                self._token = None
                continue
            if response.status_code != 200:
                raise FeedError(
                    f"GET {url} answered {response.status_code}: {response.text[:200]}"
                )
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise FeedError(f"GET {url} did not answer with a feed page")
            return body
        raise FeedError(f"GET {url} refused the token twice")  # pragma: no cover

    def pages(self, feed: str, *, since: str | None = None) -> Iterator[FeedPage]:
        """Walk ``feed`` from the start (or ``since``), one page at a time.

        Args:
            feed: The feed's name, one of ``contract.FEEDS``.
            since: Only rows updated at or after this ISO 8601 datetime.

        Yields:
            Each page, in order, until ``next_cursor`` is null.

        """
        url = f"{self.base_url}{feed}/"
        params: dict[str, str] = {"limit": str(contract.MAX_LIMIT)}
        if since:
            params["since"] = since
        while True:
            body = self._get(url, params)
            yield FeedPage(results=body["results"], count=body.get("count"))
            cursor = body.get("next_cursor")
            if not cursor:
                return
            params = {**params, "cursor": cursor}
