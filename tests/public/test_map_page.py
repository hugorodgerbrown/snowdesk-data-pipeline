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
from django.test import Client
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
