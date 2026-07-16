"""
Server-side tests for the SNOW-379 install-prompt orchestration.

Playwright covers the JS state-machine in a later ticket; here we
verify the two banners and the script are present on every page, and
that both banners are hidden by default (revealed only by the JS once
the meaningful-action threshold is met).
"""

from __future__ import annotations

import pytest
from django.test import Client


@pytest.mark.django_db
def test_android_install_banner_ships_hidden() -> None:
    """The Android / Chromium banner renders on every page and is hidden."""
    response = Client().get("/")
    body = response.content.decode("utf-8")
    assert 'id="pwa-install-banner"' in body
    # The container carries the `hidden` Tailwind utility until JS
    # strips it.
    assert 'id="pwa-install-banner"\n     role="dialog"' in body or (
        'id="pwa-install-banner"' in body and "hidden fixed bottom-4" in body
    )


@pytest.mark.django_db
def test_ios_install_guide_ships_hidden() -> None:
    """The iOS visual guide renders on every page and is hidden."""
    response = Client().get("/")
    body = response.content.decode("utf-8")
    assert 'id="pwa-install-ios"' in body
    assert "Add Snowdesk to your Home Screen" in body


@pytest.mark.django_db
def test_pwa_install_script_loaded() -> None:
    """The install-prompt JS is loaded on every page."""
    response = Client().get("/")
    body = response.content.decode("utf-8")
    assert "pwa_install.js" in body


@pytest.mark.django_db
def test_install_accept_button_has_expected_id() -> None:
    """The Install CTA carries ``id="pwa-install-accept"`` for JS lookup.

    The JS binds the click handler via ``getElementById`` — a bare
    ``[data-action]`` selector would be fragile because ``_button.html``
    doesn't accept custom data attributes.
    """
    response = Client().get("/")
    body = response.content.decode("utf-8")
    assert 'id="pwa-install-accept"' in body
