"""
tests/public/test_robots_txt.py — Tests for the /robots.txt endpoint.

Snowdesk had no robots.txt at all; the agent-readiness audit flagged its
absence (it's basic web hygiene independent of agents). These tests guard
the contract: an open default policy with the functional / token-bearing
paths disallowed, and an absolute ``Sitemap:`` line derived from
``settings.SITE_BASE_URL`` so it resolves per environment.

Tests fetch the file over HTTP via the test ``Client`` so the assertions
cover the live response shape, headers, and templated URL substitution.
"""

from __future__ import annotations

from django.test import Client, override_settings


def _body() -> str:
    """Fetch ``/robots.txt`` via the test client and return the decoded body."""
    response = Client().get("/robots.txt")
    assert response.status_code == 200
    return response.content.decode("utf-8")


def test_robots_served_as_plain_text() -> None:
    """``/robots.txt`` returns 200 with a ``text/plain`` content type."""
    response = Client().get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")


def test_robots_allows_all_by_default() -> None:
    """The policy is open: a wildcard user-agent with ``Allow: /``."""
    body = _body()
    assert "User-agent: *" in body
    assert "Allow: /" in body


def test_robots_disallows_functional_paths() -> None:
    """Admin, subscription, edit-API, partials, share, and CSP paths are excluded."""
    body = _body()
    for path in (
        "Disallow: /admin/",
        "Disallow: /subscribe/",
        "Disallow: /api/edit/",
        "Disallow: /partials/",
        "Disallow: /s/",
        "Disallow: /csp/",
    ):
        assert path in body, f"missing directive: {path!r}"


def test_robots_does_not_block_public_api() -> None:
    """The public GeoJSON / ratings API stays crawlable (only /api/edit/ is blocked)."""
    body = _body()
    assert "Disallow: /api/\n" not in body
    assert "Disallow: /api/ratings/" not in body


def test_robots_references_absolute_sitemap() -> None:
    """A ``Sitemap:`` line points at the absolute sitemap URL."""
    with override_settings(SITE_BASE_URL="https://snowdesk.info"):
        body = _body()
    assert "Sitemap: https://snowdesk.info/sitemap.xml" in body


def test_robots_references_llms_txt() -> None:
    """The header advertises the absolute llms.txt index for agents."""
    with override_settings(SITE_BASE_URL="https://snowdesk.info"):
        body = _body()
    assert "https://snowdesk.info/llms.txt" in body


def test_robots_sitemap_url_derives_from_site_base_url() -> None:
    """The sitemap line is host-pinned per environment, not hard-coded."""
    with override_settings(SITE_BASE_URL="http://localhost:8000"):
        body = _body()
    assert "Sitemap: http://localhost:8000/sitemap.xml" in body


def test_robots_is_cacheable() -> None:
    """A short public Cache-Control header is set so crawlers can cache the body."""
    response = Client().get("/robots.txt")
    assert "max-age" in response["Cache-Control"]
