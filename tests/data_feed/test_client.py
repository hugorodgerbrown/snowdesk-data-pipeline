"""
tests/data_feed/test_client.py — Tests for apps.data_feed.client (SNOW-1197).

The client's three refusals (plain HTTP to a remote host, a refused token,
a refused or malformed page), its walk to the last page, and the one token
renewal on a 401 — against a stub session, so nothing leaves the process.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from apps.data_feed.client import FeedClient, FeedError, check_feed_url

BASE = "https://prod.example/api/data/v1/"


class _Response:
    """The slice of ``requests.Response`` the client reads."""

    def __init__(self, status: int, body: Any) -> None:
        """Hold a status and a JSON body."""
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self) -> Any:
        """Return the body."""
        return self._body


class _Session:
    """A stub session answering from queues, recording every call."""

    def __init__(self, posts: list[_Response], gets: list[_Response]) -> None:
        """Queue the answers to POST (token) and GET (page) calls."""
        self.posts = posts
        self.gets = gets
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        """Answer a token request."""
        self.calls.append(("POST", url, kwargs))
        return self.posts.pop(0)

    def get(self, url: str, **kwargs: Any) -> _Response:
        """Answer a page request."""
        self.calls.append(("GET", url, kwargs))
        return self.gets.pop(0)


def _token(value: str = "t1") -> _Response:
    """A token endpoint's 200."""
    return _Response(200, {"access_token": value, "token_type": "Bearer"})


def _page(
    results: list, cursor: str | None = None, count: int | None = None
) -> _Response:
    """A feed page's 200."""
    body: dict[str, Any] = {"results": results, "next_cursor": cursor}
    if count is not None:
        body["count"] = count
    return _Response(200, body)


def _client(session: _Session, url: str = BASE) -> FeedClient:
    """A client on the stub session."""
    return FeedClient(url, "id", "secret", session=session)  # type: ignore[arg-type]


class TestFeedUrl:
    """check_feed_url."""

    def test_https_is_normalised(self) -> None:
        """A trailing slash is ensured."""
        assert check_feed_url("https://prod.example/api/data/v1") == BASE

    @pytest.mark.parametrize(
        "url", ["http://localhost:8000/api/data/v1/", "http://127.0.0.1/x/"]
    )
    def test_loopback_http_is_allowed(self, url: str) -> None:
        """A local runserver may be plain HTTP."""
        assert check_feed_url(url).startswith("http://")

    @pytest.mark.parametrize(
        "url", ["http://prod.example/api/data/v1/", "prod.example", ""]
    )
    def test_anything_else_is_refused(self, url: str) -> None:
        """Plain HTTP to a remote host, or no URL at all, is refused."""
        with pytest.raises(FeedError):
            check_feed_url(url)


class TestWalk:
    """FeedClient.pages."""

    def test_walks_to_the_last_page_with_one_token(self) -> None:
        """Two pages, one token request bound to the feed, cursor followed."""
        session = _Session([_token()], [_page([1, 2], "c1", count=3), _page([3])])
        pages = list(
            _client(session).pages("bulletins", since="2026-10-01T00:00:00+00:00")
        )
        assert [p.results for p in pages] == [[1, 2], [3]]
        assert pages[0].count == 3
        assert pages[1].count is None

        token_call, first, second = session.calls
        assert token_call[1] == "https://prod.example/oauth/token/"
        assert token_call[2]["data"] == {
            "grant_type": "client_credentials",
            "scope": "data",
            "resource": BASE,
        }
        assert first[1] == f"{BASE}bulletins/"
        assert first[2]["params"]["since"] == "2026-10-01T00:00:00+00:00"
        assert first[2]["headers"] == {"Authorization": "Bearer t1"}
        assert second[2]["params"]["cursor"] == "c1"

    def test_a_401_renews_the_token_once(self) -> None:
        """An expired token is replaced and the page retried."""
        session = _Session(
            [_token("t1"), _token("t2")], [_Response(401, {}), _page([1])]
        )
        (page,) = _client(session).pages("weather")
        assert page.results == [1]
        assert session.calls[-1][2]["headers"] == {"Authorization": "Bearer t2"}

    def test_a_second_401_raises(self) -> None:
        """A token refused twice is a real refusal."""
        session = _Session(
            [_token(), _token()], [_Response(401, {}), _Response(401, {})]
        )
        with pytest.raises(FeedError, match="401"):
            list(_client(session).pages("weather"))

    def test_a_refused_token_request_raises(self) -> None:
        """Bad credentials stop the walk before any page."""
        session = _Session([_Response(400, {"error": "invalid_client"})], [])
        with pytest.raises(FeedError, match="token request refused"):
            list(_client(session).pages("weather"))

    def test_a_token_response_without_a_token_raises(self) -> None:
        """A 200 that carries no access_token is malformed."""
        session = _Session([_Response(200, {})], [])
        with pytest.raises(FeedError, match="no access_token"):
            list(_client(session).pages("weather"))

    def test_a_page_that_is_not_a_page_raises(self) -> None:
        """A body with no results list is not a feed page."""
        session = _Session([_token()], [_Response(200, {"oops": True})])
        with pytest.raises(FeedError, match="did not answer"):
            list(_client(session).pages("weather"))

    def test_a_server_error_raises(self) -> None:
        """Any other status is reported with its code."""
        session = _Session([_token()], [_Response(500, "boom")])
        with pytest.raises(FeedError, match="500"):
            list(_client(session).pages("weather"))

    def test_network_failures_raise_feed_errors(self) -> None:
        """A connection failure on either request is a FeedError."""

        class _Down(_Session):
            def post(self, url: str, **kwargs: Any) -> _Response:
                raise requests.ConnectionError("down")

        with pytest.raises(FeedError, match="token request"):
            list(_client(_Down([], [])).pages("weather"))

        class _PageDown(_Session):
            def get(self, url: str, **kwargs: Any) -> _Response:
                raise requests.ConnectionError("down")

        with pytest.raises(FeedError, match="GET"):
            list(_client(_PageDown([_token()], [])).pages("weather"))
