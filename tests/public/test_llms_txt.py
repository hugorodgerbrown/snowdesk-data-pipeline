"""
tests/public/test_llms_txt.py — Tests for the /llms.txt endpoint.

``/llms.txt`` is a machine-readable Markdown index following the
llmstxt.org convention (H1, blockquote summary, ``##`` link sections). It
gives an LLM or agent a single low-token entry point describing the site
and where the structured data lives. These tests guard the document shape
and that every link is an absolute URL derived from
``settings.SITE_BASE_URL`` so it resolves per environment.

Tests fetch the file over HTTP via the test ``Client`` so the assertions
cover the live response shape, headers, and templated URL substitution.
"""

from __future__ import annotations

from django.test import Client, override_settings


def _body() -> str:
    """Fetch ``/llms.txt`` via the test client and return the decoded body."""
    response = Client().get("/llms.txt")
    assert response.status_code == 200
    return response.content.decode("utf-8")


def test_llms_served_as_markdown() -> None:
    """``/llms.txt`` returns 200 with a ``text/markdown`` content type."""
    response = Client().get("/llms.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/markdown")


def test_llms_starts_with_h1_project_name() -> None:
    """The document opens with the H1 project name, per the llmstxt.org spec."""
    body = _body()
    assert body.lstrip().startswith("# Snowdesk")


def test_llms_has_blockquote_summary() -> None:
    """A blockquote summary follows the H1."""
    body = _body()
    assert "\n> " in body


def test_llms_lists_core_pages() -> None:
    """The Pages section links the map and the how-to-read guide."""
    body = _body()
    assert "## Pages" in body
    assert "Avalanche map" in body
    assert "How to read a bulletin" in body


def test_llms_lists_data_endpoints() -> None:
    """The Data section surfaces the sitemap and the public JSON endpoints."""
    body = _body()
    assert "## Data" in body
    assert "/sitemap.xml" in body
    assert "/api/ratings/" in body
    assert "/api/regions.geojson" in body


def test_llms_lists_mcp_endpoint() -> None:
    """SNOW-391: the MCP server section links the JSON-RPC endpoint."""
    body = _body()
    assert "## MCP server" in body
    assert "/api/mcp/" in body
    assert "Model Context Protocol" in body


def test_llms_links_are_absolute_and_host_pinned() -> None:
    """Every Markdown link uses an absolute URL built from SITE_BASE_URL.

    SNOW-344: the avalanche-map bullet now links to / not /map/.
    """
    with override_settings(SITE_BASE_URL="https://snowdesk.info"):
        body = _body()
    assert "[Avalanche map](https://snowdesk.info/)" in body
    assert "https://snowdesk.info/api/ratings/" in body
    # No environment-relative paths leaked in as bare links.
    assert "](/" not in body


def test_llms_url_derives_from_site_base_url() -> None:
    """Links are host-pinned per environment, not hard-coded to production.

    SNOW-344: the avalanche-map bullet now links to / not /map/.
    """
    with override_settings(SITE_BASE_URL="http://localhost:8000"):
        body = _body()
    assert "[Avalanche map](http://localhost:8000/)" in body


def test_llms_is_cacheable() -> None:
    """A short public Cache-Control header is set so agents can cache the index."""
    response = Client().get("/llms.txt")
    assert "max-age" in response["Cache-Control"]


@override_settings(POSTHOG_API_KEY="phc_test")
def test_llms_no_cookie_vary_with_analytics_enabled() -> None:
    """SNOW-338 regression: Vary: Cookie absent on /llms.txt even when PostHog is active.

    When POSTHOG_API_KEY is non-empty, PosthogContextMiddleware would normally
    read request.user to tag identities, causing SessionMiddleware to append
    Vary: Cookie and defeat Cache-Control: public CDN caching.
    The _POSTHOG_EXEMPT_PATHS exemption in config/settings/base.py prevents
    that access for this endpoint.
    """
    response = Client().get("/llms.txt")
    assert "public" in response.get("Cache-Control", ""), (
        f"Expected public in Cache-Control; got: {response.get('Cache-Control', '')!r}"
    )
    vary = response.get("Vary", "")
    assert "Cookie" not in vary, (
        f"Vary: Cookie must not be set even with analytics enabled; got: {vary!r}"
    )
