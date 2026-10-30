"""
tests/public/test_offline_journey.py — the per-area panels on /offline/
and on the offline fallback page (SNOW-1308).

One panel per downloaded area, above the offline report, replacing its
"Map downloads" section. What is pinned here is everything the panels'
halves rely on each other for and no linter checks:

  * ``/offline/`` renders the host above the report and loads the three
    scripts after ``offline_audit.js``, whose collection they paint from.
  * The report no longer renders "Map downloads".
  * The module clones its DOM from ``<template>``s, so the partial and
    ``static/offline.html``'s inline copy must carry the same templates
    with the same ``data-journey-*`` slots — a slot missing from one host
    is a panel that silently paints half its answer there.
  * The strings ``<template>`` id is the one the module asks for. Whether
    its KEYS agree with the module's FALLBACKS is
    tests/test_js_strings_are_translatable.py's job, for every surface.
  * ``sw.js`` precaches the three scripts for the fallback page.

The panels' logic is tested in tests/js/test_offline_journey_core.js and
their DOM in tests/js/test_offline_journey.js.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

PARTIAL = "includes/_offline_journey.html"
MODULE = Path(settings.BASE_DIR) / "static" / "js" / "offline_journey.js"
OFFLINE_PAGE = Path(settings.BASE_DIR) / "static" / "offline.html"
SERVICE_WORKER = Path(settings.BASE_DIR) / "static" / "js" / "sw.js"
SCRIPTS = (
    "basemap_area_removal.js",
    "offline_journey_core.js",
    "offline_journey.js",
)

TEMPLATE_RE = re.compile(r'<template id="(?P<id>[^"]+)">(?P<body>.*?)</template>', re.S)
SLOT_RE = re.compile(r"\b(data-journey-[a-z-]+)")


def _templates(html: str) -> dict[str, set[str]]:
    """Map each DOM template id to the ``data-journey-*`` slots it holds.

    Args:
        html: A page or partial.

    Returns:
        Template id to its slot attribute names, strings template excluded.

    """
    return {
        match.group("id"): set(SLOT_RE.findall(match.group("body")))
        for match in TEMPLATE_RE.finditer(html)
        if match.group("id").startswith("offline-journey-")
        and not match.group("id").endswith("-strings-template")
    }


@pytest.mark.django_db
class TestOfflinePage:
    """The host on /offline/."""

    def test_the_container_renders_above_the_report(self, client: Client) -> None:
        """One host, before the audit panel, carrying its two links."""
        html = client.get(reverse("public:offline_page")).content.decode()

        journey = html.find("data-offline-journey")
        assert journey != -1
        assert journey < html.find('data-testid="offline-audit-panel"')
        assert 'data-map-downloads-url="/map/?panel=downloads"' in html
        assert 'data-offline-url="/offline/"' in html

    def test_the_scripts_load_after_the_audit(self, client: Client) -> None:
        """The panels paint from offline_audit.js's collection."""
        html = client.get(reverse("public:offline_page")).content.decode()

        audit = html.find("js/offline_audit.js")
        assert audit != -1
        for name in SCRIPTS:
            at = html.find(f"js/{name}")
            assert at != -1, name
            assert audit < at, name

    def test_the_report_no_longer_renders_map_downloads(self, client: Client) -> None:
        """The section moved to the panels whole (SNOW-1308)."""
        html = client.get(reverse("public:offline_page")).content.decode()

        assert "Map downloads" not in html
        assert 'data-string="section-downloads"' not in html


class TestTemplates:
    """The module clones these; both hosts must carry the same ones."""

    def test_the_module_asks_for_every_template_the_partial_holds(self) -> None:
        """Each DOM template id is one the module reads, and vice versa."""
        partial = _templates(render_to_string(PARTIAL, {}))
        source = MODULE.read_text()

        assert set(partial) == {
            "offline-journey-panel-template",
            "offline-journey-stage-template",
            "offline-journey-thing-template",
            "offline-journey-fix-button-template",
            "offline-journey-fix-link-template",
        }
        for template_id in partial:
            assert f"'{template_id}'" in source, template_id

    def test_the_fallback_page_carries_the_same_templates(self) -> None:
        """Same ids, same slots — without the classes it cannot load."""
        partial = _templates(render_to_string(PARTIAL, {}))
        fallback = _templates(OFFLINE_PAGE.read_text())

        assert fallback == partial

    def test_the_fallback_host_ships_hidden_behind_its_scripts(self) -> None:
        """A panel bound to nothing is worse than none on a recovery page."""
        html = OFFLINE_PAGE.read_text()

        assert re.search(r'id="offline-journey-panel"[^>]*\shidden', html, re.S)
        assert "window.pwaOfflineJourney" in html
        assert 'data-map-downloads-url="/map/?panel=downloads"' in html

    def test_the_strings_template_id_matches_the_module(self) -> None:
        """The id the module passes to ``pwaStrings.read``."""
        html = render_to_string(PARTIAL, {})

        assert 'id="offline-journey-strings-template"' in html
        assert "offline-journey-strings-template" in MODULE.read_text()

    def test_the_verdicts_are_the_agreed_words(self) -> None:
        """The three verdicts, and the tile gap's own wording."""
        html = render_to_string(PARTIAL, {})

        for words in (
            "Ready for offline",
            "Needs a refresh — tap to fix",
            "Needs a refresh — open the map",
        ):
            assert words in html, words


class TestServiceWorkerPrecache:
    """The worker carries the panels onto the fallback page."""

    def test_precaches_the_three_scripts(self) -> None:
        """By the unhashed paths ``offline.html`` requests."""
        source = SERVICE_WORKER.read_text()
        for name in SCRIPTS:
            assert f"'/static/js/{name}'" in source, name

    def test_they_exist_on_disk(self) -> None:
        """``cache.add`` on a 404 fails."""
        for name in SCRIPTS:
            assert (Path(settings.BASE_DIR) / "static" / "js" / name).is_file(), name
