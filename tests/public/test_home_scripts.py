"""
tests/public/test_home_scripts.py — Script-tag hygiene for the map homepage.

Covers:
  - ``static/js/place_picker.js`` is referenced exactly once on ``/``
    (C5, 2026-08-03 JS review). The file was loaded from both
    ``_report_surface.html`` and ``_favourites_surface.html``, which
    ``home.html`` includes back-to-back. Browsers do not deduplicate
    script elements by URL, so the IIFE ran twice and the second
    ``window.PlacePicker`` overwrote the first, orphaning its closure.
  - every ``*_core.js`` module a ``map*.js`` module dereferences is
    actually loaded on ``/`` (SNOW-573). See ``TestMapCoreModulesLoaded``
    for the bug that motivated it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
class TestHomeScriptTags:
    """The homepage must not load any shared module more than once."""

    def test_place_picker_loaded_once(self) -> None:
        """``place_picker.js`` appears in exactly one <script> src on ``/``."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert content.count("js/place_picker.js") == 1

    def test_htmx_loaded_once(self) -> None:
        """``htmx.min.js`` — the other module shared by both surfaces — stays single."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert content.count("js/htmx.min.js") == 1

    def test_place_picker_precedes_its_consumers(self) -> None:
        """The single ``place_picker.js`` tag stays ahead of both consumers.

        ``report.js`` and ``favourites.js`` both drive ``window.PlacePicker``
        and all three are ``defer``, which executes in document order — so
        hoisting the tag must not move it past the surfaces that use it.
        """
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        picker = content.index("js/place_picker.js")
        assert picker < content.index("js/report.js")
        assert picker < content.index("js/favourites.js")


@pytest.mark.django_db
class TestMapCoreModulesLoaded:
    """Every ``*_core.js`` a map module depends on must be on the page first.

    SNOW-573 shipped ``static/js/map_weather_core.js`` and the ``map.js``
    code that dereferences ``window.pwaWeatherCore``, but no ``<script>``
    tag for it — so the global was undefined in a real browser and
    ``installWeatherLayer`` threw before it could add its source. Nothing
    caught it: the Vitest suite imports the module directly, and
    ``js-globals-lint`` only asks whether *some* file in the tree assigns
    the name, not whether the page loads that file.

    Scoped to every ``map*.js`` consumer rather than ``map.js`` alone,
    because SNOW-610 split ``map.js`` eleven ways: the scrubber's
    ``window.pwaScrubberCore`` use now lives in ``map_scrubber.js``, so a
    map.js-only check silently stopped covering it — and would go on
    thinning out with each further extraction.

    Both the consumers and their dependencies are derived, not enumerated,
    so a new module or a new core dependency is covered the moment it lands.
    """

    @staticmethod
    def _core_globals() -> dict[str, str]:
        """Return ``{global_name: filename}`` for every ``*_core.js`` module."""
        js_dir = Path(settings.BASE_DIR) / "static" / "js"
        globals_by_name: dict[str, str] = {}
        for path in sorted(js_dir.glob("*_core.js")):
            match = re.search(r"(?:window|self)\.(pwa[A-Za-z]+)\s*=", path.read_text())
            if match:
                globals_by_name[match.group(1)] = path.name
        return globals_by_name

    @staticmethod
    def _code_of(path: Path) -> str:
        """Return ``path``'s source with comment lines stripped.

        Comment lines are excluded deliberately: ``map.js`` name-checks
        ``self.pwaBasemapCacheCore`` in prose while that module is loaded by
        ``sw.js`` via ``importScripts``, never by the page — a real
        dereference and a mention in a comment must not read the same here.
        """
        return "\n".join(
            line
            for line in path.read_text().splitlines()
            if not line.lstrip().startswith(("//", "*", "/*"))
        )

    @classmethod
    def _dependencies(cls) -> list[tuple[str, str, str]]:
        """Return ``(consumer, core_filename, global_name)`` triples.

        One per ``map*.js`` module that dereferences a ``*_core.js`` global
        outside a comment. Vendored ``maplibre-gl.min.js`` and the
        ``*_core.js`` modules themselves are not consumers.
        """
        js_dir = Path(settings.BASE_DIR) / "static" / "js"
        core_globals = cls._core_globals()
        found: list[tuple[str, str, str]] = []
        for consumer in sorted(js_dir.glob("map*.js")):
            if consumer.name.endswith("_core.js") or ".min." in consumer.name:
                continue
            source = cls._code_of(consumer)
            for global_name, core_filename in core_globals.items():
                if re.search(rf"(?:window|self)\.{global_name}\b", source):
                    found.append((consumer.name, core_filename, global_name))
        return found

    def test_dependency_discovery_finds_the_known_pairs(self) -> None:
        """The derivation itself works — guards against a silently empty set.

        A regex that stopped matching would make the assertions below
        vacuously pass, which is the failure mode this class exists to
        prevent. ``map_scrubber.js`` is named explicitly because it is the
        pair a ``map.js``-only check would miss.
        """
        pairs = {(consumer, core) for consumer, core, _ in self._dependencies()}
        assert ("map.js", "map_weather_core.js") in pairs
        assert ("map_scrubber.js", "scrubber_core.js") in pairs

    def test_every_core_dependency_is_loaded(self) -> None:
        """Each core module a map module uses is script-tagged on ``/``.

        Presence, deliberately not document order. The convention across
        these modules is that cross-file globals are read lazily — SNOW-610
        spells it out for the extracted blocks ("objects of arrow values, so
        every reference resolves when the user triggers a download, long
        after all files have run"), and the overlay path is the same: nothing
        dereferences ``window.pwaWeatherCore`` until the first toggle. Under
        that convention a core module loaded *after* its consumer still works,
        and ``map_downloads_manager.js`` / ``basemap_download_core.js`` is a
        live example of the pair sitting in that order today.

        What is never survivable is the file being absent altogether, which
        is exactly what SNOW-573 shipped. So that is what this asserts.

        A consumer the homepage does not load at all is skipped: it belongs
        to another page, and this test only speaks for ``/``.
        """
        client = Client()
        content = client.get(reverse("public:home")).content.decode()

        for consumer, core_filename, global_name in self._dependencies():
            if f"js/{consumer}" not in content:
                continue
            assert f"js/{core_filename}" in content, (
                f"static/js/{consumer} dereferences window.{global_name}, but "
                f"static/js/{core_filename} (which assigns it) is never "
                f"loaded on /"
            )
