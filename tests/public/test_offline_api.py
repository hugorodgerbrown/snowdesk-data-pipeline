"""
tests/public/test_offline_api.py — Tests for the offline-map endpoints.

Covers:

* ``api:offline_manifest_map`` — precache manifest JSON endpoint.
* ``service_worker`` (``/sw.js``) — SW script served from root path.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

# ---------------------------------------------------------------------------
# offline_manifest_map
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_offline_manifest_returns_200_with_version() -> None:
    """The manifest endpoint returns 200 JSON with the expected version string."""
    client = Client()
    response = client.get(reverse("api:offline_manifest_map"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    data = response.json()
    assert data["version"] == "map-shell-v1"


@pytest.mark.django_db
def test_offline_manifest_urls_contains_shell_assets() -> None:
    """The manifest URL list includes the Django-served shell assets."""
    client = Client()
    data = client.get(reverse("api:offline_manifest_map")).json()
    urls: list[str] = data["urls"]

    assert "/map/" in urls
    assert any("output.css" in u for u in urls)
    assert any("map.css" in u for u in urls)
    assert any("map.js" in u for u in urls)
    assert any("offline.js" in u for u in urls)
    assert any("favicon.svg" in u for u in urls)


@pytest.mark.django_db
def test_offline_manifest_urls_contains_api_endpoints() -> None:
    """The manifest includes the three existing JSON API paths."""
    client = Client()
    data = client.get(reverse("api:offline_manifest_map")).json()
    urls: list[str] = data["urls"]

    assert reverse("api:regions_geojson") in urls
    assert reverse("api:today_summaries") in urls
    assert reverse("api:resorts_by_region") in urls


@pytest.mark.django_db
def test_offline_manifest_urls_contains_maplibre() -> None:
    """The manifest includes at least one MapLibre CDN URL."""
    client = Client()
    data = client.get(reverse("api:offline_manifest_map")).json()
    urls: list[str] = data["urls"]

    assert any("maplibre-gl" in u for u in urls)


@pytest.mark.django_db
def test_offline_manifest_tile_count_in_expected_range() -> None:
    """
    The manifest contains exactly the expected number of tile URLs.

    Expected counts:
    * 193 vector tiles  — ``/planet/{z}/{x}/{y}.pbf`` at z5–z10 for the Swiss bbox.
    * 2   raster tiles  — ``/ne2sr/{z}/{x}/{y}.png``  at z5–z6 for the Swiss bbox.
    * 6   glyph PBFs    — 3 fontstacks × 2 Unicode ranges.
    """
    client = Client()
    data = client.get(reverse("api:offline_manifest_map")).json()
    urls: list[str] = data["urls"]

    vector_tiles = [u for u in urls if u.endswith(".pbf") and "/planet/" in u]
    raster_tiles = [u for u in urls if u.endswith(".png") and "/ne2sr/" in u]
    glyph_pbfs = [u for u in urls if "/fonts/" in u and u.endswith(".pbf")]

    assert len(vector_tiles) == 193
    assert len(raster_tiles) == 2
    assert len(glyph_pbfs) == 6


@pytest.mark.django_db
def test_offline_manifest_zero_db_queries() -> None:
    """The manifest endpoint makes zero database queries."""
    client = Client()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("api:offline_manifest_map"))
    assert response.status_code == 200
    assert len(ctx.captured_queries) == 0


# ---------------------------------------------------------------------------
# serve_sw  (/sw.js)
# ---------------------------------------------------------------------------


def test_serve_sw_returns_200_with_correct_headers() -> None:
    """``/sw.js`` returns 200 with the required service-worker headers."""
    client = Client()
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert response["Service-Worker-Allowed"] == "/"
    assert response["Cache-Control"] == "no-cache"


def test_serve_sw_contains_service_worker_code() -> None:
    """The SW script body contains ``addEventListener`` (proves it is not empty)."""
    client = Client()
    response = client.get("/sw.js")
    assert b"addEventListener" in response.content
