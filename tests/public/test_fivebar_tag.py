"""
tests/public/test_fivebar_tag.py — the fivebar tag in public/base.html (SNOW-1180).

The tag renders only where ``FIVEBAR_DOMAIN`` is set, carries that domain
and the CSP nonce, and is absent otherwise (dev, test, any unconfigured
deploy). The ``fivebar`` context processor is what exposes the setting.
"""

from __future__ import annotations

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import resolve, reverse

from apps.public.context_processors import fivebar

pytestmark = pytest.mark.django_db

_TAG_SRC = b'src="https://fiveb.ar/js/tally.js"'


class TestFivebarTag:
    """The tag follows FIVEBAR_DOMAIN."""

    @override_settings(FIVEBAR_DOMAIN="snowdesk.info")
    def test_tag_renders_with_the_domain_and_a_nonce(self) -> None:
        """A configured deploy renders the tag in <head>."""
        content = Client().get("/").content
        assert _TAG_SRC in content
        assert b'data-domain="snowdesk.info"' in content
        head = content.split(b"</head>")[0]
        tag = head.split(_TAG_SRC)[0].rsplit(b"<script", 1)[1]
        assert b"nonce=" in tag

    def test_no_tag_without_a_domain(self) -> None:
        """The test default (empty) renders nothing."""
        assert _TAG_SRC not in Client().get("/").content


class TestFivebarContextProcessor:
    """``fivebar`` exposes the trimmed setting."""

    @override_settings(FIVEBAR_DOMAIN=" snowdesk.info ")
    def test_value_is_trimmed(self) -> None:
        """Whitespace around the domain is removed."""
        assert fivebar(HttpRequest())["FIVEBAR_DOMAIN"] == "snowdesk.info"

    def test_empty_by_default_in_tests(self) -> None:
        """The autouse guard pins it empty."""
        assert fivebar(HttpRequest()) == {"FIVEBAR_DOMAIN": "", "FIVEBAR_PATH": ""}


class TestTokenRoutesCountWithoutTheToken:
    """A token-bearing page tells fivebar its pattern (Codex P1, #1158)."""

    @override_settings(FIVEBAR_DOMAIN="snowdesk.info")
    def test_account_access_page_sends_data_path(self) -> None:
        """The tag carries ``data-path`` with the token as ``:token``."""
        content = Client().get("/account/access/not-a-real-token/").content
        assert _TAG_SRC in content
        assert b'data-path="/account/access/:token/"' in content

    @override_settings(FIVEBAR_DOMAIN="snowdesk.info")
    def test_ordinary_page_has_no_data_path(self) -> None:
        """Elsewhere fivebar counts the real path."""
        assert b"data-path=" not in Client().get("/").content

    def test_route_without_a_token_argument_keeps_its_path(self) -> None:
        """Only arguments named for a token are replaced."""
        request = HttpRequest()
        request.resolver_match = resolve("/account/sign-in/")
        assert fivebar(request)["FIVEBAR_PATH"] == ""

    def test_trip_share_route_is_patterned(self) -> None:
        """Share links count as their pattern too."""
        request = HttpRequest()
        request.resolver_match = resolve(reverse("trips:share_page", args=["abc"]))
        assert fivebar(request)["FIVEBAR_PATH"].endswith("/s/:token/")
