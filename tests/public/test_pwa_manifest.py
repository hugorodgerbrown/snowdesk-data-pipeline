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
rich install dialog and app-listing metadata.

Only static-file-level facts are asserted here: shape of the JSON,
presence of the size + purpose entries, and that the icon paths are
served by Django at the URLs the manifest declares. Runtime SW
behaviour (cache hits, version bumps) is not unit-testable in pytest
— see ``docs/offline-map.md`` for the manual verification steps.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings

_MANIFEST_PATH = Path(settings.BASE_DIR) / "static" / "manifest.webmanifest"


def _load_manifest() -> dict:
    """Read ``static/manifest.webmanifest`` from disk and parse as JSON."""
    parsed: dict = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    return parsed


def test_manifest_start_url_is_site_root() -> None:
    """``start_url`` is ``/`` so the installed app opens the home page (SNOW-87)."""
    manifest = _load_manifest()
    assert manifest.get("start_url") == "/"


def test_manifest_scope_is_site_root() -> None:
    """``scope`` is ``/`` so every public path stays in the standalone window (SNOW-87).

    Without an explicit ``scope``, the W3C default is the directory of
    ``start_url`` — which on the previous ``/map/`` setting meant any
    in-app link to ``/``, ``/region/<id>/``, ``/subscribe/``, etc.
    escaped the standalone window into a regular browser tab.
    """
    manifest = _load_manifest()
    assert manifest.get("scope") == "/"


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


def test_manifest_includes_id() -> None:
    """``id`` pins canonical app identity to the origin root (SNOW-118).

    The W3C manifest spec recommends an explicit ``id`` so the browser
    can match the installed app across changes to ``start_url`` (e.g. a
    later i18n redirect to ``/en/``).
    """
    manifest = _load_manifest()
    assert manifest.get("id") == "/"


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


# Note: there is no test for the manifest's HTTP response. In dev the
# manifest is served by Django's runserver auto-mounted /static/
# handler; in production by WhiteNoise. Neither path is reachable from
# the bare ``Client()`` in tests without bringing up extra routing
# scaffolding that would exercise infrastructure rather than the SNOW-79
# contract. The on-disk-file check above covers the failure mode that
# matters: the manifest pointing at icons that don't ship.
