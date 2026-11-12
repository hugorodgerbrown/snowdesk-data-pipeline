"""
tests/public/test_map_hold_bar.py — the map page's hold bar (SNOW-1485).

Covers:
  - The map page renders #map-hold-bar as an overlay sheet with no detents
    (a form panel: no handle, no drag-to-close), hidden until a hold.
  - Its body, includes/_map_hold_bar.html, is in a ``<template>`` for
    static/js/map_hold.js to clone: the line, the ×, Save pin and Report.
  - Both scripts load on the page, the core before the binding, and both
    after place_picker.js and before report.js and favourites.js.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import UserFactory


def _template(content: str, template_id: str) -> str:
    """Return the body of the ``<template id=…>`` element in ``content``."""
    opener = f'<template id="{template_id}">'
    start = content.index(opener) + len(opener)
    return content[start : content.index("</template>", start)]


def _map_page(client: Client) -> str:
    """Return the rendered map page."""
    response = client.get(reverse("public:map"), follow=True)
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("signed_in", [True, False], ids=["signed-in", "signed-out"])
def test_the_map_renders_the_bar_and_its_body(client: Client, signed_in: bool) -> None:
    """Every reader gets the bar: signed out, Save pin and Report gate there."""
    if signed_in:
        client.force_login(UserFactory.create())
    content = _map_page(client)

    sheet = re.search(r'<div\s+id="map-hold-bar"[^>]*>', content)
    assert sheet, "no #map-hold-bar on the map page"
    assert " hidden" in sheet.group(0)
    assert "data-overlay" in sheet.group(0)
    assert "data-detents" not in sheet.group(0)

    body = _template(content, "map-hold-bar-template")
    assert "Move the map to fine-tune." in body
    assert 'data-action="dismiss"' in body
    assert re.findall(r'data-hold-action="([\w-]+)"', body) == ["save-pin", "report"]


@pytest.mark.django_db
def test_the_scripts_load_between_the_picker_and_the_sheets(client: Client) -> None:
    """The binding attaches with MapSheet at parse time, so order matters."""
    content = _map_page(client)
    scripts = re.findall(
        r'<script[^>]+src="[^"]*/js/([\w.]+?)(?:\.[0-9a-f]+)?\.js"', content
    )
    for name in ("place_picker", "map_sheet", "map_hold_core", "map_hold", "report"):
        assert name in scripts, name
    assert (
        scripts.index("place_picker")
        < scripts.index("map_sheet")
        < scripts.index("map_hold_core")
        < scripts.index("map_hold")
        < scripts.index("report")
        < scripts.index("favourites")
    )
