"""tests/public/test_pwa_apple_meta.py — iOS PWA support assertions (SNOW-118).

Without these head tags, "Add to Home Screen" on an iPhone produces a
screenshot icon and a non-standalone Safari window. The tags are emitted
from ``public/templates/public/base.html`` so every public page that
extends ``base.html`` carries them — these tests sample the home page
since every public route inherits the same ``<head>``.

Apple-specific because Safari ignores manifest-level icon and display
fields entirely; iOS still relies on the legacy ``apple-touch-icon`` +
``apple-mobile-web-app-*`` meta-tag contract.
"""

from __future__ import annotations

from django.test import Client
from django.urls import reverse


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
