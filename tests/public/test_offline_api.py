"""
tests/public/test_offline_api.py — Tests for the offline-map endpoints.

Covers:

* ``api:offline_manifest_map`` — precache manifest JSON endpoint.
* ``service_worker`` (``/sw.js``) — SW script served from root path.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Sentinel matching the shape of OpenFreeMap's real TileJSON response. Kept
# deliberately minimal — the manifest endpoint only reads ``tiles[0]``.
_FAKE_TILEJSON_VERSION = "20260415_001001_pt"
_FAKE_TILE_TEMPLATE = f"https://tiles.openfreemap.org/planet/{_FAKE_TILEJSON_VERSION}/{{z}}/{{x}}/{{y}}.pbf"


@pytest.fixture(autouse=True)
def _stub_ofm_tilejson() -> Iterator[MagicMock]:
    """Stub the OpenFreeMap TileJSON fetch so tests never touch the network.

    The real endpoint returns a small JSON document with a versioned tile
    URL template; we reproduce just enough of that shape for the manifest
    view to build its URL list deterministically.
    """
    fake_response = MagicMock(spec=requests.Response)
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"tiles": [_FAKE_TILE_TEMPLATE]}

    with patch("public.api.requests.get", return_value=fake_response) as mock_get:
        yield mock_get


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


@pytest.mark.django_db
def test_offline_manifest_uses_versioned_vector_template_from_tilejson() -> None:
    """Vector-tile URLs include the version segment from OFM's TileJSON.

    This prevents the regression where the precache stores URLs MapLibre
    never requests (cache effectively empty for vector tiles offline).
    """
    client = Client()
    data = client.get(reverse("api:offline_manifest_map")).json()
    vector_tiles = [u for u in data["urls"] if u.endswith(".pbf") and "/planet/" in u]

    assert vector_tiles, "expected at least one vector tile URL"
    # Every vector tile URL must embed the version segment returned by TileJSON.
    assert all(_FAKE_TILEJSON_VERSION in u for u in vector_tiles)


@pytest.mark.django_db
def test_offline_manifest_falls_back_when_tilejson_unreachable(
    _stub_ofm_tilejson: MagicMock,
) -> None:
    """When OFM is unreachable, the manifest falls back to the unversioned template.

    The manifest endpoint must keep responding (200) so the user sees a
    clear "Save offline" failure rather than a 500 from the SW worker.
    """
    _stub_ofm_tilejson.side_effect = requests.ConnectionError("boom")

    client = Client()
    response = client.get(reverse("api:offline_manifest_map"))
    assert response.status_code == 200

    vector_tiles = [
        u for u in response.json()["urls"] if u.endswith(".pbf") and "/planet/" in u
    ]
    # Fallback keeps the unversioned template shape: ``/planet/{z}/{x}/{y}.pbf``.
    assert all(_FAKE_TILEJSON_VERSION not in u for u in vector_tiles)
    assert len(vector_tiles) == 193


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
