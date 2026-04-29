"""tests/public/test_pwa_manifest.py — PWA installability assertions (SNOW-79).

Browsers only show the install affordance once the manifest declares
real icons — every other PWA prereq was already in place. These tests
guard the icon contract so a regression doesn't silently kill
installability the way SNOW-9's empty ``icons: []`` did.

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


# Note: there is no test for the manifest's HTTP response. In dev the
# manifest is served by Django's runserver auto-mounted /static/
# handler; in production by WhiteNoise. Neither path is reachable from
# the bare ``Client()`` in tests without bringing up extra routing
# scaffolding that would exercise infrastructure rather than the SNOW-79
# contract. The on-disk-file check above covers the failure mode that
# matters: the manifest pointing at icons that don't ship.
