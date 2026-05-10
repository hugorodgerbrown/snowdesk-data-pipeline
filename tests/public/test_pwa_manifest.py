"""tests/public/test_pwa_manifest.py — PWA installability assertions (SNOW-79).

Browsers only show the install affordance once the manifest declares
real icons — every other PWA prereq was already in place. These tests
guard the icon contract so a regression doesn't silently kill
installability the way SNOW-9's empty ``icons: []`` did. SNOW-87 added
``start_url`` and ``scope`` assertions so the installed app launches on
the home page and keeps every public path inside the standalone window
(rather than escaping to a browser tab when the user navigates outside
``/map/``). SNOW-118 added the manifest-polish fields (``id``, ``lang``,
``description``, ``categories``, ``screenshots``) that drive Chrome's
rich install dialog and app-listing metadata, and migrated the manifest
from a static file to ``public.views.serve_manifest`` so identity URLs
can be rendered absolute against ``settings.SITE_BASE_URL``.

Tests fetch the manifest over HTTP via the test ``Client`` so the
assertions cover the live response shape, headers, and the templated
URL substitution — not just the on-disk JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.test import Client, override_settings


def _load_manifest() -> dict:
    """Fetch ``/manifest.webmanifest`` via the test client and parse as JSON."""
    response = Client().get("/manifest.webmanifest")
    assert response.status_code == 200
    parsed: dict = json.loads(response.content.decode("utf-8"))
    return parsed


def test_manifest_served_with_correct_content_type() -> None:
    """``/manifest.webmanifest`` returns ``application/manifest+json`` (SNOW-118).

    The W3C manifest spec specifies this MIME type; Chromium honours it
    strictly and falls back to a less-rich install affordance when the
    type is generic ``application/json``.
    """
    response = Client().get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/manifest+json"


def test_manifest_start_url_is_absolute_site_root() -> None:
    """``start_url`` is the absolute canonical site URL (SNOW-87 / SNOW-118).

    SNOW-87 set ``start_url`` to the site root; SNOW-118 made it
    absolute via ``settings.SITE_BASE_URL`` so it survives any future
    move to a different manifest path or hostname migration.
    """
    manifest = _load_manifest()
    base = settings.SITE_BASE_URL.rstrip("/")
    assert manifest.get("start_url") == f"{base}/"


def test_manifest_scope_is_absolute_site_root() -> None:
    """``scope`` is the absolute canonical site URL so every public path stays
    inside the standalone window (SNOW-87 / SNOW-118).

    Without an explicit ``scope``, the W3C default is the directory of
    ``start_url`` — which on the previous ``/map/`` setting meant any
    in-app link to ``/``, ``/region/<id>/``, ``/subscribe/``, etc.
    escaped the standalone window into a regular browser tab.
    """
    manifest = _load_manifest()
    base = settings.SITE_BASE_URL.rstrip("/")
    assert manifest.get("scope") == f"{base}/"


def test_manifest_declares_icons() -> None:
    """The manifest's ``icons`` array is non-empty (SNOW-79 prereq for installability)."""
    manifest = _load_manifest()
    assert isinstance(manifest.get("icons"), list)
    assert len(manifest["icons"]) >= 2


def test_manifest_includes_192_and_512_sizes() -> None:
    """Both 192×192 and 512×512 PNGs are listed — minimum browsers require."""
    manifest = _load_manifest()
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert "192x192" in sizes
    assert "512x512" in sizes


def test_manifest_includes_a_maskable_icon() -> None:
    """At least one icon declares ``purpose: maskable`` for Android adaptive shapes."""
    manifest = _load_manifest()
    purposes = {icon.get("purpose") for icon in manifest["icons"]}
    assert "maskable" in purposes


def test_manifest_icon_files_exist_on_disk() -> None:
    """Every icon ``src`` resolves to a real file under ``static/``."""
    manifest = _load_manifest()
    for icon in manifest["icons"]:
        # ``src`` is served from STATIC_URL (``/static/``); strip the prefix
        # so the path is relative to the static source directory.
        assert icon["src"].startswith("/static/"), icon
        relative = icon["src"][len("/static/") :]
        path = Path(settings.BASE_DIR) / "static" / relative
        assert path.exists(), f"manifest icon {icon['src']} missing on disk"


def test_manifest_id_is_absolute_url() -> None:
    """``id`` is the absolute canonical app identity URL (SNOW-118).

    The W3C manifest spec resolves ``id`` as a URL relative to the
    manifest URL — ``/`` would technically work — but the spec
    recommends an explicit absolute URL so the install identity stays
    stable across changes to ``start_url`` or the manifest's own URL.
    """
    manifest = _load_manifest()
    base = settings.SITE_BASE_URL.rstrip("/")
    assert manifest.get("id") == f"{base}/"


def test_manifest_id_changes_with_site_base_url() -> None:
    """``id`` derives from ``settings.SITE_BASE_URL`` per environment (SNOW-118).

    Production sets ``SITE_BASE_URL=https://snowdesk.info``; dev defaults
    to ``http://localhost:8000``. Each environment must resolve to a
    distinct, hostname-pinned identity URL — otherwise a dev install
    and a prod install would share the same install slot.
    """
    with override_settings(SITE_BASE_URL="https://snowdesk.info"):
        prod = _load_manifest()
    with override_settings(SITE_BASE_URL="http://localhost:8000"):
        dev = _load_manifest()
    assert prod["id"] == "https://snowdesk.info/"
    assert dev["id"] == "http://localhost:8000/"
    assert prod["start_url"] == "https://snowdesk.info/"
    assert dev["start_url"] == "http://localhost:8000/"


def test_manifest_includes_lang() -> None:
    """``lang`` matches the English-only pre-launch policy (SNOW-118)."""
    manifest = _load_manifest()
    assert manifest.get("lang") == "en"


def test_manifest_includes_description() -> None:
    """A non-empty ``description`` populates Chrome's rich install dialog (SNOW-118)."""
    manifest = _load_manifest()
    description = manifest.get("description")
    assert isinstance(description, str)
    assert len(description) > 0


def test_manifest_includes_categories() -> None:
    """``categories`` is a list of strings used for app-listing metadata (SNOW-118)."""
    manifest = _load_manifest()
    categories = manifest.get("categories")
    assert isinstance(categories, list)
    assert len(categories) > 0
    for entry in categories:
        assert isinstance(entry, str) and entry


def test_manifest_includes_screenshots() -> None:
    """At least one wide and one narrow screenshot are declared (SNOW-118).

    Chrome's "rich install dialog" on Android requires both form factors;
    without them it falls back to the small dialog and the screenshots
    never render at all.
    """
    manifest = _load_manifest()
    screenshots = manifest.get("screenshots")
    assert isinstance(screenshots, list)
    assert len(screenshots) >= 2
    form_factors = {shot.get("form_factor") for shot in screenshots}
    assert "wide" in form_factors
    assert "narrow" in form_factors


def test_manifest_screenshot_files_exist_on_disk() -> None:
    """Every screenshot ``src`` resolves to a real file under ``static/``."""
    manifest = _load_manifest()
    for shot in manifest.get("screenshots", []):
        assert shot["src"].startswith("/static/"), shot
        relative = shot["src"][len("/static/") :]
        path = Path(settings.BASE_DIR) / "static" / relative
        assert path.exists(), f"manifest screenshot {shot['src']} missing on disk"


def test_maskable_icon_is_opaque() -> None:
    """The maskable PWA icon PNG has no alpha channel (SNOW-150).

    Android's adaptive-icon system applies a mask (circle, squircle, etc.)
    to the icon. If the PNG retains an alpha channel (PNG color_type 6,
    RGBA), the transparent pixels outside the rounded SVG corners leak
    through the mask as white or black depending on the device launcher,
    making the logo appear shrunken on a coloured background.

    The fix is to write the maskable variant as opaque RGB (color_type 2).
    This test reads the IHDR color_type byte directly from the on-disk PNG
    (offset 25: 8-byte signature + 4-byte length + 4-byte type + 4-byte
    width + 4-byte height + 1-byte bit-depth = offset 25) and asserts it
    equals 2 (RGB). Any other value — including 0 (greyscale) or 6 (RGBA)
    — would indicate the build script is not producing a plain-RGB PNG.
    """
    icon_path = (
        Path(settings.BASE_DIR) / "static" / "icons" / "pwa" / "icon-maskable-512.png"
    )
    assert icon_path.exists(), f"maskable icon not found at {icon_path}"
    data = icon_path.read_bytes()
    color_type = data[25]
    assert color_type == 2, (
        f"icon-maskable-512.png has color_type={color_type}, expected 2 (RGB). "
        "It must be opaque RGB so Android's adaptive-icon mask doesn't leak "
        "transparency at the masked corners. "
        "Regenerate via `npm run build:icons` after fixing bin/build-pwa-icons."
    )
