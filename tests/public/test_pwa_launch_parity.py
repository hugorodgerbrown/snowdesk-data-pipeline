"""tests/public/test_pwa_launch_parity.py — the splash and the first paint agree (SNOW-1112).

An installed launch shows Android's generated splash (from the manifest)
until the page paints. The handoff is clean only if they agree, per
environment:

- the manifest ``theme_color`` is the ``<meta name="theme-color">``, and both
  are the manifest ``background_color`` — otherwise the status bar changes
  colour at the handoff;
- the manifest ``background_color`` is ``--color-bg``, the page's own
  background;
- neither the page nor the manifest names the other environment's icons,
  which is how staging used to paint the production logo.

Each assertion reads both sides off the rendered responses rather than off
``PWAEnvironmentIdentity``, so a template or view that stops reading the
identity fails here even while the identity itself is right.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.test import Client, override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

ENVIRONMENTS = {
    "production": {"own": "icons/pwa/", "other": "icons/pwa-staging/"},
    "staging": {"own": "icons/pwa-staging/", "other": "icons/pwa/"},
}


def _manifest() -> dict:
    """Fetch and parse ``/manifest.webmanifest``."""
    response = Client().get("/manifest.webmanifest")
    assert response.status_code == 200
    parsed: dict = json.loads(response.content.decode("utf-8"))
    return parsed


def _page() -> str:
    """Render the map page — the manifest ``start_url``, so the launch page."""
    response = Client().get(reverse("public:map"))
    assert response.status_code == 200
    return response.content.decode("utf-8")


def _meta_theme_color(body: str) -> str:
    """Return the ``content`` of the page's single ``theme-color`` meta tag."""
    found = re.findall(r'<meta name="theme-color" content="([^"]+)">', body)
    assert len(found) == 1, found
    return str(found[0])


def _colour_bg_token() -> str:
    """Return the light ``--color-bg`` value, the first one in main.css's ``@theme``."""
    css = (Path(settings.BASE_DIR) / "src" / "css" / "main.css").read_text()
    light: str = re.findall(r"--color-bg:\s*(#[0-9a-fA-F]{6});", css)[0]
    return light.lower()


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_status_bar_colour_is_one_value_from_splash_to_first_paint(
    environment: str,
) -> None:
    """Manifest ``theme_color`` == meta theme-color == ``background_color``."""
    with override_settings(SITE_ENVIRONMENT=environment):
        manifest, body = _manifest(), _page()
    assert manifest["theme_color"] == _meta_theme_color(body)
    assert manifest["theme_color"] == manifest["background_color"]


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_splash_background_is_the_page_background(environment: str) -> None:
    """The manifest ``background_color`` is ``--color-bg``."""
    with override_settings(SITE_ENVIRONMENT=environment):
        manifest = _manifest()
    assert manifest["background_color"].lower() == _colour_bg_token()


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_no_icon_from_the_other_environment(environment: str) -> None:
    """Neither the page nor the manifest names the other environment's icons.

    The iOS startup images (``icons/pwa/splash/``) are the one documented
    exception: there is no staging splash set (SNOW-878), and per-environment
    iOS launch images are a separate ticket. Screenshots are shared too, but
    they are not icons and live under ``icons/pwa/screenshots/``.
    """
    other = ENVIRONMENTS[environment]["other"]
    with override_settings(SITE_ENVIRONMENT=environment):
        manifest, body = _manifest(), _page()
    page_hits = [
        m
        for m in re.findall(rf"/static/{re.escape(other)}[^\"' ]+", body)
        if "/splash/" not in m
    ]
    assert page_hits == []
    for icon in manifest["icons"]:
        assert f"/static/{other}" not in icon["src"], icon


class _HashingStorage(StaticFilesStorage):
    """Stand-in for ``CompressedManifestStaticFilesStorage``'s hashed URLs."""

    def url(self, name: str | None) -> str:
        """Return the plain URL with a fake content hash before the extension."""
        return super().url(name).replace(".png", ".0123abcd.png")


HASHED_STORAGES = {
    **settings.STORAGES,
    "staticfiles": {"BACKEND": f"{__name__}._HashingStorage"},
}


@override_settings(STORAGES=HASHED_STORAGES)
def test_icon_urls_go_through_the_static_storage() -> None:
    """Manifest icons carry the storage's content hash.

    Chrome re-mints an installed WebAPK only when a manifest icon URL
    changes, so a hand-built ``/static/…`` URL meant a redrawn icon never
    reached an existing install.
    """
    manifest, body = _manifest(), _page()
    for icon in manifest["icons"]:
        assert icon["src"].endswith(".0123abcd.png"), icon
    for shot in manifest["screenshots"]:
        assert shot["src"].endswith(".0123abcd.png"), shot
    assert 'apple-touch-icon-180.0123abcd.png"' in body


def test_offline_page_keeps_the_launch_status_bar_colour() -> None:
    """``static/offline.html`` uses the same theme-color as every other page.

    It is a plain static file outside the template system, so it cannot
    read the identity; this pins its literal to the shared value.
    """
    from apps.public.site_environment import LAUNCH_BACKGROUND

    html = (Path(settings.BASE_DIR) / "static" / "offline.html").read_text()
    assert _meta_theme_color(html) == LAUNCH_BACKGROUND
