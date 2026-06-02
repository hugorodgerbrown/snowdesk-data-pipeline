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

import pytest
from django.test import Client

# Every test in this module hits the live URL via Django's test client. The
# request passes through the project middleware stack, and CSP's per-request
# nonce setup touches the DB on a cold worker. Without ``django_db`` the
# tests fail in CI under pytest-xdist whenever a worker hasn't already warmed
# its cache from a prior test. Locally tests can pass without the mark
# because xdist worker scheduling happens to share warm workers — relying on
# that is flaky. Apply the mark module-wide.
pytestmark = pytest.mark.django_db


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
