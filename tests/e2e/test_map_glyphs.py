"""
tests/e2e/test_map_glyphs.py — Playwright regression tests for SNOW-478
(overlay glyph 404s on non-openfreemap basemaps).

MapLibre resolves glyphs against the *active basemap style's* single
``glyphs`` URL, so an overlay symbol layer can only use a font that basemap's
glyph server serves. ``static/js/map.js`` used to hardcode
``text-font: ['Noto Sans Bold' | 'Noto Sans Regular']`` on its overlay
layers and drew the favourites pin as a ``★`` text glyph. On the swisstopo
basemap — whose glyph server serves a Frutiger family, not Noto Sans, and has
no ``★`` codepoint — every overlay label 404'd (the reported bug).

The fix (SNOW-478):
  * ``deriveOverlayTextFont`` picks a font stack the active basemap style
    itself declares, so overlay labels resolve against that basemap's glyph
    server and match its typography.
  * the favourites pin is an SDF ``icon-image`` (``favourite-star``), which
    never touches the glyph server.

Both tests are deterministic and network-independent: rather than load a real
external basemap (unreachable / flaky in the CI harness — see
``test_overlay_basemap_persistence.py`` and ``test_map_zoom_indicator.py``),
the first stubs the configured basemap style URL (``page.route``) with a
synthetic Frutiger-only style and asserts the boot-installed overlay label
adopts its font; the second asserts the favourites pin is icon-based with no
text glyph.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.conf import settings
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from tests.e2e.conftest import FavouritesPage
from tests.factories import FavouriteFactory


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _poll(page: Page, expr: str, *, timeout_ms: int = 20000) -> None:
    """Poll a JS boolean ``expr`` via ``page.evaluate`` until it is truthy.

    Used instead of ``page.wait_for_function`` for MapLibre source/layer
    predicates: on the strict-CSP map page ``wait_for_function``'s polling
    proved unreliable for these, whereas ``page.evaluate`` (CDP
    ``Runtime.evaluate``) reads them reliably.
    """
    waited = 0
    while waited < timeout_ms:
        if page.evaluate(expr):
            return
        page.wait_for_timeout(250)
        waited += 250
    raise AssertionError(f"condition never became true within {timeout_ms}ms: {expr}")


# A synthetic basemap style that declares only a Frutiger family (like
# swisstopo) — both as a plain literal stack and nested inside a ``match``
# expression, so the derivation must reach into the ``['literal', [...]]`` arms
# and must NOT misread ``['get','class']`` as a font stack. Fonts appear twice
# for "Frutiger Neue Condensed Regular" (top-level + the match default) and once
# for "…Medium", so the most-used stack — the correct derivation — is the former.
# Empty ``empty`` source keeps the label layers valid without rendering (so no
# glyph is ever fetched); the glyphs URL is same-origin to satisfy CSP.
def _frutiger_style(origin: str) -> dict[str, Any]:
    return {
        "version": 8,
        "glyphs": f"{origin}/static/fonts/{{fontstack}}/{{range}}.pbf",
        "sources": {
            "empty": {
                "type": "geojson",
                "data": {"type": "FeatureCollection", "features": []},
            }
        },
        "layers": [
            {
                "id": "fake-place-label",
                "type": "symbol",
                "source": "empty",
                "layout": {
                    "text-field": "x",
                    "text-font": ["Frutiger Neue Condensed Regular"],
                },
            },
            {
                "id": "fake-water-label",
                "type": "symbol",
                "source": "empty",
                "layout": {
                    "text-field": "y",
                    "text-font": [
                        "match",
                        ["get", "class"],
                        "town",
                        ["literal", ["Frutiger Neue Condensed Medium"]],
                        ["literal", ["Frutiger Neue Condensed Regular"]],
                    ],
                },
            },
        ],
    }


@pytest.mark.django_db(transaction=True)
def test_overlay_label_font_follows_active_basemap(
    live_server: LiveServer, page: Page
) -> None:
    """Overlay labels adopt the active basemap's own declared font.

    Regression coverage for SNOW-478: a hardcoded ``Noto Sans`` text-font 404'd
    on basemaps whose glyph server hosts a different family. The active basemap
    style is stubbed with a Frutiger-only style (``page.route`` — also keeps the
    test off the flaky external basemap servers), so the overlay ``regions-label``
    installed at boot must derive ``Frutiger Neue Condensed Regular``, never
    ``Noto Sans``.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # Serve the Frutiger style in place of the configured basemap — the initial
    # style then loads instantly and deterministically (no external fetch), and
    # deriveOverlayTextFont runs against a known non-Noto basemap.
    page.route(
        settings.BASEMAP_STYLE_URL,
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_frutiger_style(live_server.url)),
        ),
    )

    _navigate_home(page, live_server.url)

    # regions-label (L4) is installed by the async boot handler and is
    # default-visible; poll for it via page.evaluate.
    _poll(page, "() => !!MAP.getLayer('regions-label')")

    text_font = page.evaluate(
        "() => MAP.getLayoutProperty('regions-label', 'text-font')"
    )
    assert text_font == ["Frutiger Neue Condensed Regular"], (
        f"overlay label font should follow the basemap; got {text_font!r}"
    )
    assert page_errors == [], f"unexpected JS errors: {page_errors}"


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_favourites_pin_is_glyph_free_icon(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """The favourites pin is an SDF icon, not a ``★`` text glyph.

    Regression coverage for SNOW-478: the ``★`` glyph 404'd on basemaps whose
    font lacks U+2605. An icon image never touches the glyph server, so the pin
    always paints. Asserts the layer uses ``icon-image`` and declares no
    ``text-field``, and that the star image is registered.
    """
    with django_db_blocker.unblock():
        FavouriteFactory.create(user=favourites_page.subscriber.user, name="Star Test")

    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    # Boot fetches the eligible user's favourites and installs the pin layer.
    page.wait_for_function(
        """() => {
            if (!MAP.getLayer('favourites-pin')) return false;
            const src = MAP.getSource('favourites');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.name === 'Star Test',
            );
        }"""
    )

    icon_image = page.evaluate(
        "() => MAP.getLayoutProperty('favourites-pin', 'icon-image')"
    )
    text_field = page.evaluate(
        "() => MAP.getLayoutProperty('favourites-pin', 'text-field')"
    )
    has_star = page.evaluate("() => MAP.hasImage('favourite-star')")

    assert icon_image == "favourite-star", (
        f"favourites pin should be an icon; got icon-image={icon_image!r}"
    )
    assert not text_field, (
        f"favourites pin must not use a text glyph; got text-field={text_field!r}"
    )
    assert has_star, "the favourite-star icon image should be registered"
