"""Server-rendered integration contract for the opt-in map layer explainer."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
class TestMapExplainerIntegration:
    """The legend launches the demo without adding weight to normal map visits."""

    def test_normal_map_links_to_demo_without_loading_demo_assets(self) -> None:
        response = Client(SERVER_NAME="localhost").get(reverse("public:home"))
        content = response.content.decode()

        assert response.status_code == 200
        assert 'id="map-explainer-link"' in content
        assert 'href="/?layers=exploded"' in content
        assert "/static/css/map_exploded.css" not in content
        assert "/static/js/map_exploded.js" not in content

    def test_demo_loads_packaged_assets_without_manual_cache_keys(self) -> None:
        response = Client(SERVER_NAME="localhost").get(
            reverse("public:home"),
            {"layers": "exploded", "d": "2026-03-12"},
        )
        content = response.content.decode()

        assert response.status_code == 200
        assert content.count('/static/css/map_exploded.css"') == 1
        assert content.count('/static/js/map_exploded.js"') == 1
        assert 'id="map-explainer-strings"' in content
        assert 'href="/?layers=exploded&amp;d=2026-03-12"' in content
        assert "panel-redesign" not in content

    def test_other_layers_query_does_not_load_demo_assets(self) -> None:
        response = Client(SERVER_NAME="localhost").get(
            reverse("public:home"), {"layers": "other"}
        )
        content = response.content.decode()

        assert "/static/css/map_exploded.css" not in content
        assert "/static/js/map_exploded.js" not in content
