"""
tests/oauth/services/test_resource.py — Tests for apps.oauth.services.resource.

Covers the canonical form (trailing slash, case, query) and that both
mounted spellings of the MCP URL resolve to one audience for the request's
own origin.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from apps.oauth.services.resource import (
    canonical_resource,
    is_mcp_resource,
    mcp_resource_url,
    mcp_resources,
    request_origin,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://snowdesk.info/api/mcp/", "https://snowdesk.info/api/mcp"),
        ("https://snowdesk.info/api/mcp", "https://snowdesk.info/api/mcp"),
        ("HTTPS://SnowDesk.Info/api/mcp/", "https://snowdesk.info/api/mcp"),
        ("https://snowdesk.info/api/mcp/?x=1#f", "https://snowdesk.info/api/mcp"),
        ("not a url", ""),
        ("", ""),
    ],
)
def test_canonical_resource(raw: str, expected: str) -> None:
    """Scheme and host lowercase, trailing slash, query and fragment go."""
    assert canonical_resource(raw) == expected


def test_path_case_is_preserved() -> None:
    """Only the scheme and host are case-insensitive."""
    assert canonical_resource("https://x.test/API/MCP") == "https://x.test/API/MCP"


def test_origin_and_resources_follow_the_request_host() -> None:
    """A tunnel host gets its own origin and audience."""
    request = RequestFactory().get("/", HTTP_HOST="testserver")
    assert request_origin(request) == "http://testserver"
    assert mcp_resource_url(request) == "http://testserver/api/mcp/"
    assert mcp_resources(request) == frozenset({"http://testserver/api/mcp"})


def test_is_mcp_resource_accepts_both_spellings_only() -> None:
    """Both spellings pass; another path or host does not."""
    request = RequestFactory().get("/")
    assert is_mcp_resource(request, "http://testserver/api/mcp/")
    assert is_mcp_resource(request, "http://testserver/api/mcp")
    assert not is_mcp_resource(request, "http://testserver/api/")
    assert not is_mcp_resource(request, "http://evil.test/api/mcp/")
