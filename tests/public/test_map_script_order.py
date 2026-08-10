"""
tests/public/test_map_script_order.py — the map bundle's script load order.

SNOW-647, closing a gap SNOW-610 left open. Splitting `static/js/map.js` into
thirteen classic scripts created a cross-file ordering constraint that did not
exist while it was one file, and nothing enforced it — `_load_map_bundle.js`
carried a comment *asking* the reader to keep the lists in step, which is a
comment doing a test's job.

Why the order matters. Classic scripts share one global lexical scope, so a
top-level ``let``/``const`` in one file is readable from a later one as a bare
identifier — but it sits in the TEMPORAL DEAD ZONE until its own script has
run, and every IIFE in this set runs at parse time. Load `map.js` before
`map_state.js` and its boot IIFE reads ``MAP`` before the declaration is
initialised, throws a ReferenceError, and the map never mounts.

There is incidental coverage: that failure takes the map down, so much of the
e2e suite would go red. But the symptom points at the wrong place. These tests
name the cause.

Two machine-readable places have to agree, and this module compares them
against each other rather than introducing a third:

  - what `home.html` actually renders (the browser's real order);
  - `MAP_BUNDLE` in `tests/js/_load_map_bundle.js` (Vitest's boot order).

`map.js`'s own header lists the same order in prose. That is deliberately NOT
asserted here — it is documentation, and pinning a comment's contents to a test
buys brittleness rather than safety.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

# The three files that only declare — they must precede the boot IIFE.
DECLARATION_FILES = frozenset(
    {"map_state.js", "map_basemap_downloads.js", "map_shared.js"}
)

# The boot IIFE itself.
BOOT_FILE = "map.js"

_BUNDLE_SOURCE = Path(__file__).resolve().parents[1] / "js" / "_load_map_bundle.js"

# `'map_state.js',` — quoted entries inside the MAP_BUNDLE array literal.
_BUNDLE_ENTRY_RE = re.compile(r"'(map[a-z_]*\.js)'")

# `<script defer src="/static/js/map.js">` — captures the bare filename.
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="[^"]*?/static/js/([^"]+)"')


def _map_bundle() -> list[str]:
    """The MAP_BUNDLE array from `tests/js/_load_map_bundle.js`, in order.

    Parsed as text rather than imported: it is an ES module, and this is a
    Python test. The array literal is the only place in that file where
    ``map*.js`` filenames appear in single quotes, so the regex is
    unambiguous — and `test_bundle_list_is_parsed_not_empty` fails loudly if
    the shape ever changes enough to break that assumption.

    @returns The filenames, in declaration order.
    """
    return _BUNDLE_ENTRY_RE.findall(
        _BUNDLE_SOURCE.read_text(encoding="utf-8").split("export const MAP_BUNDLE")[1]
    )


def _rendered_script_filenames() -> list[str]:
    """Every `static/js/` script the homepage renders, in document order.

    Document order IS execution order for deferred classic scripts, which is
    what makes rendering the page the right source rather than reading the
    template: a tag moved into a different block or an included partial still
    lands here in the order the browser will run it.

    @returns Bare filenames, e.g. ``["htmx.min.js", "map_state.js", …]``.
    """
    response = Client().get(reverse("public:home"))
    assert response.status_code == 200
    return _SCRIPT_SRC_RE.findall(response.content.decode())


@pytest.mark.django_db
class TestMapScriptOrder:
    """The rendered page and MAP_BUNDLE must agree on the map bundle's order."""

    def test_bundle_list_is_parsed_not_empty(self) -> None:
        """Guard the regex itself — an empty parse would pass everything below.

        Every other test here filters the page's scripts *by* MAP_BUNDLE, so a
        parse that silently returned ``[]`` would make them all vacuously true.
        This is the assertion that stops that.
        """
        bundle = _map_bundle()
        assert len(bundle) == 13, f"expected 13 bundle entries, parsed {bundle}"
        assert BOOT_FILE in bundle
        assert DECLARATION_FILES <= set(bundle)

    def test_rendered_order_matches_the_vitest_bundle(self) -> None:
        """The page loads the bundle in exactly MAP_BUNDLE's order.

        Catches any reorder within the set, and any bundle entry whose script
        tag is missing altogether.
        """
        bundle = _map_bundle()
        rendered = [f for f in _rendered_script_filenames() if f in bundle]
        assert rendered == bundle, (
            "home.html's map script tags and MAP_BUNDLE "
            "(tests/js/_load_map_bundle.js) have drifted apart.\n"
            f"  rendered: {rendered}\n"
            f"  MAP_BUNDLE: {bundle}"
        )

    def test_bundle_scripts_are_contiguous(self) -> None:
        """No foreign script is interleaved into the bundle's run.

        This covers the direction the test above cannot. That one filters the
        page's scripts down to MAP_BUNDLE's members, so a NEW ``map_*.js`` tag
        added to the template but never registered in MAP_BUNDLE is silently
        filtered out and passes. Breaking contiguity is what makes that fail —
        and it does so without an allowlist of the nine ``map*.js`` files
        (map_sheet, map_help, map_edit_resorts, map_layer_sync_status,
        map_overlay_offline_cache, map_overlay_bounds, map_placement_focus,
        map_controls_collapse, map_downloads_manager) that legitimately sit
        outside the bundle.

        If a genuinely unrelated script ever has to be interleaved, this is the
        test to change — but it should be a deliberate edit, not a surprise.
        """
        bundle = set(_map_bundle())
        rendered = _rendered_script_filenames()
        positions = [i for i, name in enumerate(rendered) if name in bundle]
        span = rendered[positions[0] : positions[-1] + 1]
        intruders = [name for name in span if name not in bundle]
        assert not intruders, (
            "these scripts are interleaved into the map bundle's run: "
            f"{intruders}. If one is a new map module, add it to MAP_BUNDLE "
            "(tests/js/_load_map_bundle.js); if it genuinely belongs outside "
            "the bundle, move its tag above or below the run."
        )

    def test_declarations_precede_the_boot_iife(self) -> None:
        """map_state.js, map_basemap_downloads.js and map_shared.js load first.

        Redundant against `test_rendered_order_matches_the_vitest_bundle`, and
        deliberately so: that test reports "the lists differ", this one reports
        *why* the order is not arbitrary. Whoever trips it should learn the rule
        rather than reformat a list until CI goes quiet.
        """
        rendered = _rendered_script_filenames()
        boot_at = rendered.index(BOOT_FILE)
        for name in DECLARATION_FILES:
            assert rendered.index(name) < boot_at, (
                f"{name} loads after {BOOT_FILE}. It declares bindings that "
                f"{BOOT_FILE}'s boot IIFE reads at parse time; loading it later "
                "leaves those reads in the temporal dead zone, so the IIFE "
                "throws a ReferenceError and the map never mounts."
            )

    def test_surfaces_follow_the_boot_iife(self) -> None:
        """Every surface IIFE loads after map.js.

        Same reasoning in the other direction: the surfaces bind DOM that the
        boot IIFE is responsible for, and they held this position when they
        were the trailing IIFEs of the single file.
        """
        bundle = _map_bundle()
        rendered = _rendered_script_filenames()
        boot_at = rendered.index(BOOT_FILE)
        surfaces = [
            name
            for name in bundle
            if name != BOOT_FILE and name not in DECLARATION_FILES
        ]
        for name in surfaces:
            assert rendered.index(name) > boot_at, (
                f"{name} loads before {BOOT_FILE}, but it is a surface module: "
                "its IIFE runs at parse time and depends on the boot IIFE "
                "having already installed the map and its overlays."
            )
