"""
tests/core/test_http.py — Tests for core.http.client_ip.

Covers the leftmost-XFF path, multi-hop XFF chains, whitespace trimming,
missing/empty header fallback to REMOTE_ADDR, and the empty-string fallback
when neither header is present.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from core.http import client_ip


class TestClientIp:
    """Tests for client_ip(request)."""

    @pytest.fixture()
    def rf(self) -> RequestFactory:
        """Return a Django RequestFactory for building synthetic requests."""
        return RequestFactory()

    def test_returns_remote_addr_when_no_xff(self, rf: RequestFactory) -> None:
        """Falls back to REMOTE_ADDR when X-Forwarded-For is absent."""
        request = rf.get("/", REMOTE_ADDR="203.0.113.5")
        assert client_ip(request) == "203.0.113.5"

    def test_prefers_leftmost_xff_entry(self, rf: RequestFactory) -> None:
        """Returns the leftmost (client) entry from X-Forwarded-For."""
        request = rf.get(
            "/",
            HTTP_X_FORWARDED_FOR="203.0.113.1, 10.0.0.1, 172.16.0.1",
            REMOTE_ADDR="10.0.0.1",
        )
        assert client_ip(request) == "203.0.113.1"

    def test_single_xff_entry(self, rf: RequestFactory) -> None:
        """A single X-Forwarded-For value is returned as-is."""
        request = rf.get(
            "/",
            HTTP_X_FORWARDED_FOR="203.0.113.42",
            REMOTE_ADDR="10.0.0.1",
        )
        assert client_ip(request) == "203.0.113.42"

    def test_xff_leading_whitespace_stripped(self, rf: RequestFactory) -> None:
        """Whitespace around entries in X-Forwarded-For is stripped."""
        request = rf.get(
            "/",
            HTTP_X_FORWARDED_FOR="  203.0.113.7  , 10.0.0.1",
            REMOTE_ADDR="10.0.0.1",
        )
        assert client_ip(request) == "203.0.113.7"

    def test_returns_empty_string_when_neither_header_present(
        self, rf: RequestFactory
    ) -> None:
        """Returns '' when both X-Forwarded-For and REMOTE_ADDR are absent."""
        request = rf.get("/")
        # Remove REMOTE_ADDR if RequestFactory set it.
        request.META.pop("REMOTE_ADDR", None)
        request.META.pop("HTTP_X_FORWARDED_FOR", None)
        assert client_ip(request) == ""

    def test_empty_xff_falls_back_to_remote_addr(self, rf: RequestFactory) -> None:
        """An empty X-Forwarded-For header falls back to REMOTE_ADDR."""
        request = rf.get(
            "/",
            HTTP_X_FORWARDED_FOR="",
            REMOTE_ADDR="203.0.113.9",
        )
        assert client_ip(request) == "203.0.113.9"
