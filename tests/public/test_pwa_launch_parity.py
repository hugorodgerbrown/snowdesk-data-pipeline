"""tests/public/test_pwa_launch_parity.py — the splash and the first paint agree (SNOW-1112).

An installed launch is two surfaces drawn by two owners: Android's
generated splash (from the manifest) and the launch shell the page paints
over it (includes/_pwa_launch_shell.html). The handoff is invisible only if
they agree on every value, per environment:

- the manifest ``theme_color`` is the ``<meta name="theme-color">``, and both
  are the manifest ``background_color`` — otherwise the status bar changes
  colour at the handoff;
- the manifest ``background_color`` is the colour the shell paints, which is
  ``--color-bg`` in the stylesheet;
- the shell's Android mark is the manifest's maskable icon — the file
  Android's splash shows;
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


def _shell_background(body: str) -> str:
    """Return the background the launch shell's inline critical CSS paints."""
    match = re.search(
        r"\.pwa-launch-shell \{[^}]*?background-color:\s*([^;]+);", body, re.DOTALL
    )
    assert match, "launch shell inline background-color not found"
    return match.group(1).strip()


def _shell_image(body: str, css_class: str) -> str:
    """Return the ``data-src`` of the launch shell image with ``css_class``.

    Reads the attribute from the ``<img>`` tag as a whole, so the test does
    not depend on the order the template writes its attributes in.
    """
    for tag in re.findall(r"<img\b[^>]*>", body):
        if f'class="{css_class}"' in tag:
            match = re.search(r'data-src="([^"]+)"', tag)
            assert match, tag
            return match.group(1)
    raise AssertionError(f"{css_class} not found")


def _colour_bg_tokens() -> tuple[str, str]:
    """Return the light and dark ``--color-bg`` values from main.css.

    Two definitions of the token: the light one in ``@theme``, the dark one
    in the ``.dark`` override further down the file.
    """
    css = (Path(settings.BASE_DIR) / "src" / "css" / "main.css").read_text()
    light, dark = re.findall(r"--color-bg:\s*(#[0-9a-fA-F]{6});", css)[:2]
    return light.lower(), dark.lower()


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
def test_first_paint_background_is_the_splash_background(environment: str) -> None:
    """The shell paints the manifest ``background_color``, which is ``--color-bg``."""
    with override_settings(SITE_ENVIRONMENT=environment):
        manifest, body = _manifest(), _page()
    assert _shell_background(body) == manifest["background_color"]
    assert manifest["background_color"].lower() == _colour_bg_tokens()[0]


def test_ios_dark_first_paint_is_the_dark_startup_image_background() -> None:
    """A dark-scheme iPhone hands over from a dark startup image onto dark.

    ``bin/build-pwa-splash`` ships both schemes, chosen by
    ``prefers-color-scheme``; the shell's iOS branch follows the same query
    with the dark ``--color-bg``.
    """
    body = _page()
    match = re.search(
        r"@media \(prefers-color-scheme: dark\) \{\s*"
        r"html\.pwa-launching--ios \.pwa-launch-shell \{\s*background-color: ([^;]+);",
        body,
    )
    assert match, "iOS dark-scheme shell background not found"
    assert match.group(1).strip().lower() == _colour_bg_tokens()[1]


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_first_paint_mark_is_the_icon_androids_splash_shows(environment: str) -> None:
    """The shell's Android mark is the manifest's maskable icon, from this environment."""
    with override_settings(SITE_ENVIRONMENT=environment):
        manifest, body = _manifest(), _page()
    maskable = [i["src"] for i in manifest["icons"] if i["purpose"] == "maskable"]
    assert maskable == [_shell_image(body, "pwa-launch-shell__mark")]
    own = ENVIRONMENTS[environment]["own"]
    assert f"/static/{own}" in _shell_image(body, "pwa-launch-shell__tile")


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
    """Manifest icons and the shell marks carry the storage's content hash.

    Chrome re-mints an installed WebAPK only when a manifest icon URL
    changes, so a hand-built ``/static/…`` URL meant a redrawn icon never
    reached an existing install.
    """
    manifest, body = _manifest(), _page()
    for icon in manifest["icons"]:
        assert icon["src"].endswith(".0123abcd.png"), icon
    for shot in manifest["screenshots"]:
        assert shot["src"].endswith(".0123abcd.png"), shot
    assert _shell_image(body, "pwa-launch-shell__mark").endswith(".0123abcd.png")
    assert _shell_image(body, "pwa-launch-shell__tile").endswith(".0123abcd.png")
    assert 'apple-touch-icon-180.0123abcd.png"' in body


def test_offline_page_keeps_the_launch_status_bar_colour() -> None:
    """``static/offline.html`` uses the same theme-color as every other page.

    It is a plain static file outside the template system, so it cannot
    read the identity; this pins its literal to the shared value.
    """
    from apps.public.site_environment import LAUNCH_BACKGROUND

    html = (Path(settings.BASE_DIR) / "static" / "offline.html").read_text()
    assert _meta_theme_color(html) == LAUNCH_BACKGROUND
