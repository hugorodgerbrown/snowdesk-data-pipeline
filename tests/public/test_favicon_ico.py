"""
tests/public/test_favicon_ico.py — Tests for the /favicon.ico redirect.

Tools and crawlers (Lighthouse, browser prefetch, social-media scrapers)
request ``/favicon.ico`` from the site root unconditionally. Snowdesk ships
only SVG favicons, so the ``/favicon.ico`` route is a 302 redirect to the
canonical ``favicon.svg`` staticfiles URL. These tests guard the contract:
both the bare path and the trailing-slash variant return a 302 directly
(no preceding 301 from Django's ``APPEND_SLASH`` middleware), and the
redirect target is an SVG at a URL containing ``favicon``.
"""

from __future__ import annotations

from django.test import Client


def test_favicon_ico_returns_302() -> None:
    """``GET /favicon.ico`` returns a 302 redirect without following it."""
    response = Client().get("/favicon.ico", follow=False)
    assert response.status_code == 302


def test_favicon_ico_trailing_slash_returns_302_directly() -> None:
    """``GET /favicon.ico/`` returns a 302, not a 301 from ``APPEND_SLASH``."""
    response = Client().get("/favicon.ico/", follow=False)
    assert response.status_code == 302


def test_favicon_ico_redirects_to_svg() -> None:
    """The redirect target contains ``favicon`` and ends with ``.svg``."""
    response = Client().get("/favicon.ico", follow=False)
    location = response["Location"]
    assert "favicon" in location
    assert location.endswith(".svg")


def test_favicon_ico_has_cache_control_header() -> None:
    """The redirect carries a one-day ``Cache-Control`` header."""
    response = Client().get("/favicon.ico", follow=False)
    assert response["Cache-Control"] == "public, max-age=86400"
