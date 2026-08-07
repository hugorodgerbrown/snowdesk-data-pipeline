"""
tests/public/test_home_scripts.py — Script-tag hygiene for the map homepage.

Covers:
  - ``static/js/place_picker.js`` is referenced exactly once on ``/``
    (C5, 2026-08-03 JS review). The file was loaded from both
    ``_report_surface.html`` and ``_favourites_surface.html``, which
    ``home.html`` includes back-to-back. Browsers do not deduplicate
    script elements by URL, so the IIFE ran twice and the second
    ``window.PlacePicker`` overwrote the first, orphaning its closure.
  - every ``*_core.js`` module ``map.js`` dereferences is actually loaded
    on ``/``, ahead of ``map.js`` (SNOW-573). See
    ``TestMapCoreModulesLoaded`` for the bug that motivated it.
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
    """Every ``*_core.js`` map.js depends on must actually be on the page.

    SNOW-573 shipped ``static/js/map_weather_core.js`` and the ``map.js``
    code that dereferences ``window.pwaWeatherCore``, but no ``<script>``
    tag for it — so the global was undefined in a real browser and
    ``installWeatherLayer`` threw before it could add its source. Nothing
    caught it: the Vitest suite imports the module directly, and
    ``js-globals-lint`` only asks whether *some* file in the tree assigns
    the name, not whether the page loads that file.

    The dependency set is derived, not enumerated, so a future core module
    is covered the moment map.js starts using it.
    """

    @staticmethod
    def _map_core_dependencies() -> list[tuple[str, str]]:
        """Return ``(filename, global_name)`` for each core module map.js uses.

        Reads ``static/js/*_core.js`` for the ``window``/``self`` global each
        assigns, then keeps the ones ``map.js`` actually mentions.
        """
        js_dir = Path(settings.BASE_DIR) / "static" / "js"
        # Comment lines are excluded deliberately: map.js name-checks
        # ``self.pwaBasemapCacheCore`` in prose while that module is loaded by
        # sw.js via importScripts, never by the page — a real dereference and
        # a mention in a comment must not read the same here.
        code_lines = [
            line
            for line in (js_dir / "map.js").read_text().splitlines()
            if not line.lstrip().startswith(("//", "*", "/*"))
        ]
        map_source = "\n".join(code_lines)
        deps: list[tuple[str, str]] = []
        for path in sorted(js_dir.glob("*_core.js")):
            match = re.search(r"(?:window|self)\.(pwa[A-Za-z]+)\s*=", path.read_text())
            if match and re.search(rf"(?:window|self)\.{match.group(1)}\b", map_source):
                deps.append((path.name, match.group(1)))
        return deps

    def test_dependency_discovery_finds_the_known_modules(self) -> None:
        """The derivation itself works — guards against a silently empty set.

        A regex that stopped matching would make every assertion below
        vacuously pass, which is the failure mode this class exists to
        prevent.
        """
        names = [name for name, _ in self._map_core_dependencies()]
        assert "map_weather_core.js" in names
        # SNOW-610 split the scrubber IIFE (and its window.pwaScrubberCore
        # use) out of map.js into map_scrubber.js, so map.js itself no
        # longer dereferences scrubber_core.js — search_core.js is one of
        # the *_core.js modules map.js's own (unsplit) search box still
        # uses directly.
        assert "search_core.js" in names
        assert "choropleth_core.js" in names

    def test_every_map_core_dependency_is_loaded_before_map_js(self) -> None:
        """Each core module map.js uses is script-tagged ahead of map.js.

        Every tag is ``defer``, so document order is execution order — a
        module loaded after map.js is as absent as one not loaded at all
        for anything map.js touches during its own initialisation.
        """
        client = Client()
        content = client.get(reverse("public:home")).content.decode()
        map_js = content.index("js/map.js")

        for filename, global_name in self._map_core_dependencies():
            assert f"js/{filename}" in content, (
                f"map.js dereferences window.{global_name}, but "
                f"static/js/{filename} (which assigns it) is never loaded on /"
            )
            assert content.index(f"js/{filename}") < map_js, (
                f"static/js/{filename} must load before map.js — both are "
                f"defer, so a later tag leaves window.{global_name} undefined "
                f"while map.js initialises"
            )
