"""
tests/public/test_map_region_pick.py — what the map page ships for
"Download a region" (SNOW-1414).

The task itself (static/js/map_region_pick.js) is covered in
``tests/js/test_map_region_pick.js``; this module covers what the server
renders for it: the bar and its strings, the Downloads sheet's second add
action, the shared add-button partial it is built from, the scripts' places
in the page, and the help topic's paragraph.

Scoped to the Django test client: rendering a template needs no browser.
"""

from __future__ import annotations

import re

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="[^"]*?/static/js/([^"]+)"')


def _map_page(client: Client) -> str:
    """Render the map page and return its HTML."""
    response = client.get(reverse("public:map"))
    assert response.status_code == 200
    return response.content.decode()


def _downloads_body(html: str) -> str:
    """The Downloads sheet's body ``<template>``, from the rendered page."""
    start = html.index('<template id="map-downloads-body-template">')
    return html[start : html.index("</template>", start)]


@pytest.mark.django_db
class TestThePanel:
    """The pick task's form panel and its strings template."""

    def test_the_panel_is_a_hidden_form_panel(self, client: Client) -> None:
        """A shared map sheet with no detents (design rule 6), shut until opened."""
        html = _map_page(client)
        sheet = re.search(r'<div[^>]*id="region-pick-sheet"[^>]*>', html)
        assert sheet is not None
        assert "hidden" in sheet.group(0)
        assert "data-overlay" in sheet.group(0)
        assert "data-detents" not in sheet.group(0)

    def test_the_body_template_has_every_part_the_task_writes(
        self, client: Client
    ) -> None:
        """Name, readout, Download and a dismissing Cancel, cloned in on open."""
        html = _map_page(client)
        start = html.index('<template id="map-region-pick-template">')
        body = html[start : html.index("</template>", start)]
        for hook in (
            "data-region-pick-body",
            "data-region-pick-name",
            "data-region-pick-readout",
        ):
            assert hook in body, hook
        assert 'id="region-pick-confirm"' in body
        cancel = re.search(r"<button[^>]*>\s*Cancel\s*</button>", body)
        assert cancel is not None
        assert 'data-action="dismiss"' in cancel.group(0)

    def test_every_string_the_task_reads_is_rendered(self, client: Client) -> None:
        """Each key the module reads has a server-rendered, translatable copy."""
        html = _map_page(client)
        start = html.index('<template id="map-region-pick-strings-template">')
        strings = html[start : html.index("</template>", start)]
        for key in (
            "choosing",
            "hint",
            "readout",
            "unavailable",
            "too-big",
            "busy",
            "already",
            "failed",
        ):
            assert f'data-string="{key}"' in strings, key


@pytest.mark.django_db
class TestTheDownloadsSheet:
    """The Downloads sheet's two add actions."""

    def test_it_offers_a_region_before_a_custom_area(self, client: Client) -> None:
        """Both triggers render, the cheaper one (a tap) first."""
        body = _downloads_body(_map_page(client))
        region_at = body.index("data-panel-add-region")
        custom_match = re.search(r"data-panel-add(?![-\w])", body)
        assert custom_match is not None
        custom_at = custom_match.start()
        assert region_at < custom_at
        assert "Download a region" in body
        assert "Download a custom area" in body

    def test_a_panel_without_a_foot_renders_one_add_button(self) -> None:
        """The extracted button leaves favourites and field reports unchanged."""
        html = render_to_string(
            "includes/_ugc_panel.html",
            {
                "title": "Favourites",
                "icon_template": "includes/_icon_favourite.html",
                "context_line": "Favourites are private and not shared.",
                "rows_template": "public/partials/_ugc_panel_demo_rows.html",
                "cta_label": "Add a favourite",
            },
        )
        assert html.count("data-panel-add") == 1
        assert "data-panel-add-region" not in html
        assert "Add a favourite" in html


@pytest.mark.django_db
def test_the_scripts_load_after_what_they_call(client: Client) -> None:
    """The core, then the task, both after map.js and the region download bridge."""
    rendered = _SCRIPT_SRC_RE.findall(_map_page(client))
    core_at = rendered.index("map_region_pick_core.js")
    task_at = rendered.index("map_region_pick.js")
    assert core_at < task_at
    assert rendered.index("map.js") < core_at
    assert rendered.index("map_region_download.js") < task_at


@pytest.mark.django_db
def test_the_help_topic_says_how_to_download_a_region(client: Client) -> None:
    """The downloads help topic names the new way in, Terrain included."""
    response = client.get(reverse("public:help"))
    assert response.status_code == 200
    html = response.content.decode()
    start = html.index('data-testid="help-downloads-kinds"')
    paragraph = html[start : html.index("</p>", start)]
    assert "Download a region" in paragraph
    assert "Terrain mode" in paragraph
