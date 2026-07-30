"""tests/public/test_pwa_apple_meta.py — iOS PWA support assertions (SNOW-118).

Without these head tags, "Add to Home Screen" on an iPhone produces a
screenshot icon and a non-standalone Safari window. The tags are emitted
from ``apps/public/templates/public/base.html`` so every public page that
extends ``base.html`` carries them — these tests sample the home page
since every public route inherits the same ``<head>``.

Apple-specific because Safari ignores manifest-level icon and display
fields entirely; iOS still relies on the legacy ``apple-touch-icon`` +
``apple-mobile-web-app-*`` meta-tag contract.
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings
from django.urls import reverse

# Every test in this module hits ``Client().get(reverse("public:home"))``,
# which resolves database-backed context (e.g. nav, feature flags). Mark the
# whole module so pytest-django enables DB access rather than blocking with
# ``Database access not allowed``.
pytestmark = pytest.mark.django_db


def test_home_renders_apple_touch_icon_link() -> None:
    """``<link rel="apple-touch-icon" sizes="180x180">`` is emitted (SNOW-118).

    180×180 is the iOS-recommended size; smaller variants are upscaled
    automatically by iOS.
    """
    response = Client().get(reverse("public:home"))
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert 'rel="apple-touch-icon"' in body
    assert 'sizes="180x180"' in body
    assert "apple-touch-icon-180.png" in body


def test_home_declares_mobile_web_app_capable() -> None:
    """``mobile-web-app-capable=yes`` lets iOS launch in standalone (SNOW-118).

    The legacy ``apple-mobile-web-app-capable`` was deprecated upstream and
    removed; iOS now honours the unprefixed ``mobile-web-app-capable``
    spelling, which is what we emit.
    """
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert 'name="mobile-web-app-capable"' in body
    assert 'content="yes"' in body


def test_home_declares_apple_status_bar_style() -> None:
    """``apple-mobile-web-app-status-bar-style`` flows the page under the iOS bar (SNOW-118)."""
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert 'name="apple-mobile-web-app-status-bar-style"' in body
    assert 'content="black-translucent"' in body


def test_home_declares_apple_web_app_title() -> None:
    """``apple-mobile-web-app-title`` overrides the home-screen icon label (SNOW-118)."""
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert 'name="apple-mobile-web-app-title"' in body
    assert 'content="Snowdesk"' in body


def test_home_apple_touch_icon_defaults_to_production_directory() -> None:
    """``apple-touch-icon`` href points at ``/static/icons/pwa/`` on production (SNOW-399).

    iOS ignores the manifest ``icons`` array entirely — the legacy
    ``apple-touch-icon`` link is the only mechanism for the home-screen
    tile on iPhone. The production href must resolve to the checked-in
    production icon, not the staging one.
    """
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert "/static/icons/pwa/apple-touch-icon-180.png" in body


def test_home_theme_color_defaults_to_production_hex() -> None:
    """The ``theme-color`` meta tag matches the production manifest value on production (SNOW-399).

    The tag drives the browser chrome tint on an open tab, and (on iOS)
    the status-bar tint in a standalone install. Keeping the meta in
    sync with the manifest ``theme_color`` avoids a jarring colour flip
    between "opened in browser" and "opened as installed app".
    """
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert '<meta name="theme-color" content="#1a1a1a">' in body


@override_settings(SITE_ENVIRONMENT="staging")
def test_home_title_reflects_staging_environment() -> None:
    """``<title>`` on staging carries the "— Staging" suffix (SNOW-399).

    The suffix rides outside the ``{% block title %}`` block in
    ``base.html`` so page-specific titles (like the home page's
    "Snowdesk — Swiss avalanche bulletins") still get flagged as
    staging in the browser tab and history. Assertion checks the closing
    ``</title>`` boundary so the suffix isn't accidentally injected
    mid-title.
    """
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert " — Staging</title>" in body


def test_home_title_has_no_suffix_on_production() -> None:
    """The default production ``<title>`` has no environment suffix (SNOW-399)."""
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert " — Staging</title>" not in body


@override_settings(SITE_ENVIRONMENT="staging")
def test_home_apple_web_app_title_reflects_staging_environment() -> None:
    """``apple-mobile-web-app-title`` on iOS staging says "Snowdesk (Staging)" (SNOW-399).

    On iOS the manifest isn't consulted — this legacy tag is the only
    override for the home-screen icon label. Without the swap the iOS
    staging tile would read "Snowdesk" identical to the production tile.
    """
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert 'content="Snowdesk (Staging)"' in body


@override_settings(SITE_ENVIRONMENT="staging")
def test_home_apple_touch_icon_swaps_for_staging() -> None:
    """``apple-touch-icon`` href points at the staging icon set on staging (SNOW-399)."""
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert "/static/icons/pwa-staging/apple-touch-icon-180.png" in body


@override_settings(SITE_ENVIRONMENT="staging")
def test_home_theme_color_swaps_for_staging() -> None:
    """``theme-color`` meta tag renders amber on staging (SNOW-399).

    Matches the manifest ``theme_color`` swap so a staging install has
    a consistent amber tint across the browser chrome (open tab) and
    OS chrome (installed PWA).
    """
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert '<meta name="theme-color" content="#b45309">' in body


@override_settings(SITE_ENVIRONMENT="staging")
def test_home_og_site_name_reflects_staging_environment() -> None:
    """``og:site_name`` on staging says "Snowdesk (Staging)" (SNOW-399).

    A staging URL shared into Slack should link-preview as staging so
    a reader doesn't mistake it for production copy or data.
    """
    response = Client().get(reverse("public:home"))
    body = response.content.decode("utf-8")
    assert '<meta property="og:site_name" content="Snowdesk (Staging)">' in body
