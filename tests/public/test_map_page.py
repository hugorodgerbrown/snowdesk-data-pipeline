"""
tests/public/test_map_page.py — Tests for the /map/ page view.

Narrow scope: the page resolves, the three API endpoint URLs are baked
into the markup via data-* attributes, and the static JS/CSS links are
present. The JavaScript itself is not exercised here (there is no JS
test runner in the project); the API endpoints it consumes have their
own integration tests in ``test_map_api.py``.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse


@pytest.mark.django_db
def test_map_page_renders():
    """GET /map/ returns 200 and contains the map container."""
    client = Client()
    response = client.get(reverse("public:map"))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="map"' in content
    assert 'id="sheet"' in content


@pytest.mark.django_db
def test_map_page_injects_api_urls():
    """
    The three API URLs resolve via ``{% url %}`` and are exposed to JS
    through data-* attributes on the #map element.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert f'data-regions-url="{reverse("api:regions_geojson")}"' in content
    assert f'data-summaries-url="{reverse("api:today_summaries")}"' in content
    assert f'data-resorts-url="{reverse("api:resorts_by_region")}"' in content


@pytest.mark.django_db
def test_map_page_loads_assets():
    """The page references the MapLibre library, the map CSS, and map JS."""
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert "maplibre-gl" in content
    assert "/static/css/map.css" in content
    assert "/static/js/map.js" in content


@pytest.mark.django_db
@override_settings(BASEMAP="swisstopo_winter")
def test_map_page_injects_default_basemap_key():
    """
    SNOW-58: ``settings.BASEMAP`` is rendered onto the #map element as
    ``data-default-basemap-key`` so the JS can fall back to the
    env-resolved default when localStorage is empty or names a basemap
    that has since been removed from the catalogue.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert 'data-default-basemap-key="swisstopo_winter"' in content


@pytest.mark.django_db
def test_map_page_renders_basemap_picker():
    """
    SNOW-58: the picker renders one ``menuitemradio`` button per entry
    in the ``basemaps`` context, each carrying ``data-basemap-key`` and
    ``data-basemap-url``. Order is curated server-side via
    ``_BASEMAP_LABELS``; verifying both keys are present is enough to
    pin the contract — JS resolves the active option at runtime.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert 'id="basemap-pill"' in content
    assert 'id="basemap-menu"' in content
    for key in ("openfreemap_liberty", "swisstopo_winter", "swisstopo_light"):
        assert f'data-basemap-key="{key}"' in content
        assert f'data-basemap-url="{settings.BASEMAP_STYLES[key]}"' in content


@pytest.mark.django_db
def test_map_view_passes_basemap_catalogue():
    """
    The view exposes ``basemaps`` and ``default_basemap_key`` in template
    context so the template can render the picker without re-deriving
    the catalogue from settings inline.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    ctx = response.context
    assert "basemaps" in ctx
    assert "default_basemap_key" in ctx
    keys = [bm["key"] for bm in ctx["basemaps"]]
    assert keys == ["openfreemap_liberty", "swisstopo_winter", "swisstopo_light"]
    assert all({"key", "label", "url"} <= set(bm) for bm in ctx["basemaps"])
    assert ctx["default_basemap_key"] == settings.BASEMAP


@pytest.mark.django_db
def test_map_page_accepts_date_query_param():
    """
    SNOW-47: ``/map/?d=YYYY-MM-DD`` still 200s. The selected date is
    consumed entirely by JS (which reads ``location.search`` after the
    page loads), so the only server-side guarantee is that the page
    doesn't reject or strip the query string. The scrubber data
    attributes that the JS needs to interpret ``?d=`` must still be
    present in the rendered markup.
    """
    client = Client()
    response = client.get(reverse("public:map") + "?d=2026-02-15")
    assert response.status_code == 200
    content = response.content.decode()
    assert "data-season-start=" in content
    assert "data-season-end=" in content
    assert "data-today=" in content


@pytest.mark.django_db
def test_map_page_renders_unified_time_controls():
    """
    The play button (#scrubber-play) and the always-visible date pill
    (#map-date-pill) must be rendered server-side so the JS only has to
    wire behaviour onto pre-existing DOM. Today's date is server-rendered
    into the pill so the first paint is correct without waiting on JS.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert 'id="scrubber-play"' in content
    assert 'id="map-date-pill"' in content
