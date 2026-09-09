"""tests/public/test_pwa_splash.py — iOS launch-screen assertions (SNOW-878).

Before SNOW-878 an installed Snowdesk launched to a plain white screen on
every iPhone and iPad. iOS does not generate a launch screen from the web
app manifest the way Chrome does, and it does not fall back to
``background_color``: with no ``apple-touch-startup-image`` whose media
query matches the device exactly, it paints white for the whole cold
boot. ``docs/offline-map.md`` recorded that omission as a deliberate
trade against a background the OS was never going to use.

These tests hold the two halves of the fix together. The device matrix
lives in ``bin/build-pwa-splash`` and nowhere else; the Django side reads
back the manifest that script writes. What can still go wrong is
everything at the seam — a manifest that stops being read, links that
stop being emitted, files that are linked but were never rendered, and
the background colours drifting from the token the page actually paints,
which is the one defect that would be invisible in every test that only
checks structure.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse

from apps.public.pwa_splash import MANIFEST_PATH, splash_links

# Every test here hits a rendered page, which resolves database-backed
# context (nav, feature flags).
pytestmark = pytest.mark.django_db

MAIN_CSS = Path(settings.BASE_DIR) / "src" / "css" / "main.css"


def _manifest() -> dict:
    """Read the checked-in splash manifest."""
    manifest: dict = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return manifest


def _home_body() -> str:
    """Render the home page and return its HTML."""
    response = Client().get(reverse("public:home"))
    assert response.status_code == 200
    return response.content.decode("utf-8")


def test_manifest_is_committed() -> None:
    """The build artefact is checked in, like the PWA icons.

    Nothing regenerates it at deploy time — ``bin/build-pwa-splash`` is
    run by hand and its output committed — so an absent manifest means
    every iOS launch is back to white.
    """
    assert MANIFEST_PATH.exists(), (
        f"{MANIFEST_PATH} is missing — run `npm run build:icons && npm run build:splash`"
    )


def test_every_linked_file_exists() -> None:
    """Each manifest entry names a PNG that was actually rendered.

    The failure this guards is silent by construction: iOS asks for a
    startup image, gets a 404, and falls back to white on that one device
    model. Nothing logs, nothing 500s, and it is only reproducible while
    holding the affected phone.
    """
    splash_dir = MANIFEST_PATH.parent
    missing = [
        entry["file"]
        for entry in _manifest()["entries"]
        if not (splash_dir / entry["file"]).exists()
    ]
    assert not missing, f"linked splash images were never rendered: {missing}"


def test_backgrounds_match_the_page_background_token() -> None:
    """The splash backgrounds are ``--color-bg`` in each scheme.

    This is the assertion the feature actually rests on. A launch screen
    that is a shade off the page it opens into produces a visible flash
    at handover — which is precisely the bug SNOW-878 also fixed in the
    web app manifest, where ``background_color`` had been ``#f4f1e8``
    (map.css's ``--paper``) against a page painting ``#f2f0ec``.

    Read out of ``src/css/main.css`` rather than hard-coded here so a
    rebrand fails this test instead of shipping the drift.
    """
    css = MAIN_CSS.read_text(encoding="utf-8")
    # Two definitions of the same token: the light one in @theme, the
    # dark one in the :root override further down the file.
    light, dark = re.findall(r"--color-bg:\s*(#[0-9a-fA-F]{6});", css)[:2]

    manifest = _manifest()
    assert manifest["background_light"] == light
    assert manifest["background_dark"] == dark


def test_home_emits_one_link_per_manifest_entry() -> None:
    """Every manifest entry reaches the page as a startup-image link."""
    body = _home_body()
    entries = _manifest()["entries"]
    assert body.count('rel="apple-touch-startup-image"') == len(entries)


def test_links_carry_both_colour_schemes() -> None:
    """Light and dark launch screens are both offered (SNOW-878).

    The manifest has no per-scheme ``background_color``, so iOS is the
    only platform where the launch screen can follow the app's theme —
    the whole reason the dark set is rendered. A link set that lost its
    ``prefers-color-scheme`` terms would send every dark-mode user
    through a full-screen cream flash on each launch.
    """
    body = _home_body()
    assert "(prefers-color-scheme: dark)" in body
    assert "(prefers-color-scheme: light)" in body


def test_media_queries_keep_device_dimensions_in_natural_orientation() -> None:
    """Landscape entries do not swap ``device-width`` / ``device-height``.

    ``device-width`` and ``device-height`` describe the device, not the
    current viewport, and do not swap on rotation — the ``orientation``
    term is what separates the two images. Swapping them is the classic
    error in a hand-written startup-image block: it produces a query no
    device ever matches, and the launch falls back to white with nothing
    to show for it.
    """
    by_orientation: dict[str, set[tuple[str, str]]] = {
        "portrait": set(),
        "landscape": set(),
    }
    for entry in _manifest()["entries"]:
        match = re.search(
            r"\(device-width: (\d+)px\) and \(device-height: (\d+)px\).*"
            r"\(orientation: (portrait|landscape)\)",
            entry["media"],
        )
        assert match, f"unparseable media query: {entry['media']}"
        width, height, orientation = match.groups()
        by_orientation[orientation].add((width, height))

    assert by_orientation["portrait"] == by_orientation["landscape"]


def test_landscape_canvas_is_the_transpose_of_portrait() -> None:
    """The rendered PNG, unlike the media query, does rotate."""
    for entry in _manifest()["entries"]:
        if "(orientation: landscape)" in entry["media"]:
            assert entry["width"] > entry["height"], entry["file"]
        else:
            assert entry["height"] > entry["width"], entry["file"]


@override_settings(SITE_ENVIRONMENT="staging")
def test_staging_links_point_at_the_production_splash_set() -> None:
    """Staging reuses the production launch screens (SNOW-878).

    Unlike the icons (SNOW-399), there is no amber splash set. The
    staging tell has to be legible where a tester CHOOSES the app — the
    home-screen tile and the status bar beside it — and rendering 68 more
    PNGs to tint a half-second launch screen is a poor trade. This test
    pins that decision so a future "make staging consistent" change is a
    deliberate one.
    """
    body = _home_body()
    assert "icons/pwa/splash/" in body
    assert "icons/pwa-staging/splash/" not in body


def test_missing_manifest_degrades_to_no_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable manifest costs the splash, not the site.

    Returning nothing puts iOS back on the white launch screen that
    predates this feature — bad, but every page still renders. Raising
    would 500 the entire public site because a build step was skipped,
    which is worse by a wide margin.
    """
    splash_links.cache_clear()
    monkeypatch.setattr(
        "apps.public.pwa_splash.MANIFEST_PATH",
        Path("/nonexistent/splash-manifest.json"),
    )
    try:
        assert splash_links() == ()
        assert 'rel="apple-touch-startup-image"' not in _home_body()
    finally:
        splash_links.cache_clear()
