"""
tests/public/test_map_page.py — Tests for the /map/ page view.

Narrow scope: the page resolves, the three API endpoint URLs are baked
into the markup via data-* attributes, and the static JS/CSS links are
present. The JavaScript itself is not exercised here (there is no JS
test runner in the project); the API endpoints it consumes have their
own integration tests in ``test_map_api.py``.
"""

from __future__ import annotations

import datetime

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse
from freezegun import freeze_time

from tests.factories import MicroRegionFactory, RegionDayRatingFactory


@pytest.mark.django_db
def test_map_page_renders():
    """GET /map/ returns 200 and contains the map container and popup endpoint."""
    client = Client()
    response = client.get(reverse("public:map"))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="map"' in content
    # SNOW-174: the region popup URL template must be baked into the markup
    # so that map.js can fetch tooltip HTML without hard-coding the path.
    assert "data-region-summary-url" in content


@pytest.mark.django_db
def test_map_page_injects_api_urls():
    """
    The four API URLs resolve via ``{% url %}`` and are exposed to JS
    through data-* attributes on the #map element. SNOW-78 added the
    resorts-geojson URL alongside the regions/summaries/resorts trio.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert f'data-regions-url="{reverse("api:regions_geojson")}"' in content
    assert f'data-summaries-url="{reverse("api:today_summaries")}"' in content
    assert f'data-resorts-url="{reverse("api:resorts_by_region")}"' in content
    assert f'data-resorts-geojson-url="{reverse("api:resorts_geojson")}"' in content


@pytest.mark.django_db
def test_map_page_renders_resorts_overlay_toggle():
    """
    SNOW-78: a Resorts checkbox sits in the basemap-picker overlays
    section so the user can toggle the geocoded-resort pin layer on/off.
    Default is ``aria-checked="false"`` — the layer opens hidden.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert 'data-overlay-key="resorts"' in content
    assert "Resorts" in content
    # The toggle starts unchecked so the map opens uncluttered; the JS
    # reads the persisted preference from localStorage on first paint.
    resorts_btn_idx = content.index('data-overlay-key="resorts"')
    aria_idx = content.find('aria-checked="false"', resorts_btn_idx)
    next_li_idx = content.find("<li ", resorts_btn_idx)
    assert 0 <= aria_idx < next_li_idx if next_li_idx > 0 else aria_idx >= 0


@pytest.mark.django_db
def test_map_page_renders_resorts_legend_entry():
    """SNOW-78: the danger-scale legend includes a Resorts entry."""
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert 'data-testid="map-legend-resorts"' in content
    assert "map-legend-pin" in content


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


@pytest.mark.django_db
def test_map_page_renders_timelapse_speed_button():
    """
    The timelapse speed cycler renders alongside the play button with the
    1× default. The JS rehydrates ``data-speed`` and the visible label
    from localStorage on first paint, but the markup contract is the
    default — verifying it pins the entry point the JS hooks into.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert 'id="scrubber-speed"' in content
    assert 'data-speed="1"' in content
    assert "aria-label='Timelapse speed'" in content


@pytest.mark.django_db
def test_map_page_no_offline_toggle_or_precache_url():
    """SNOW-79: the SNOW-9 "Save offline" button and its data-attribute are gone.

    The PWA shell SW now caches assets at runtime via stale-while-
    revalidate, so an explicit opt-in download UI is no longer needed.
    The previous ``#offline-toggle`` button + its
    ``data-offline-manifest-url`` attribute must not be in the rendered
    map page — keeping them around would re-introduce the "stuck on
    stale data" reports that motivated this rewrite.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert 'id="offline-toggle"' not in content
    assert "data-offline-manifest-url" not in content
    assert "offline.js" not in content


@pytest.mark.django_db
def test_map_page_inherits_pwa_manifest_link():
    """SNOW-79: every public page (incl. /map/) links the manifest from base.html."""
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert 'rel="manifest"' in content
    assert "manifest.webmanifest" in content
    assert "sw_register.js" in content


@pytest.mark.django_db
def test_map_page_loads_vendored_maplibre_assets() -> None:
    """map.html must reference the vendored maplibre-gl assets from /static/, not unpkg.

    SNOW-169 vendored maplibre-gl 4.7.1 JS and CSS into static/ so the page
    no longer depends on an external CDN at runtime or in the CSP allow-list.
    """
    client = Client()
    response = client.get(reverse("public:map"))
    content = response.content.decode()
    assert "maplibre-gl.min" in content
    assert "maplibre-gl.css" in content
    assert "unpkg.com" not in content


@pytest.mark.django_db
class TestMapPageDataDrivenSeasonBounds:
    """
    SNOW-173: data-season-start / data-season-end reflect the actual
    RegionDayRating min/max dates when rows exist for the season, rather
    than always using the calendar Nov 1 / May 31 boundaries.
    """

    @freeze_time("2026-02-15")
    def test_season_bounds_reflect_data_min_max(self) -> None:
        """
        When RegionDayRating rows exist for the current season, the map page
        renders data-season-start and data-season-end matching the earliest
        and latest dates in those rows — not the calendar-window boundaries.
        """
        region = MicroRegionFactory.create(region_id="CH-5500")
        # Season 2025/2026: rows spanning Dec 2025 – Mar 2026 (narrower than
        # the Nov 1 – May 31 calendar window)
        RegionDayRatingFactory.create(region=region, date=datetime.date(2025, 12, 10))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 1, 20))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 5))

        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()

        assert 'data-season-start="2025-12-10"' in content
        assert 'data-season-end="2026-03-05"' in content

    @freeze_time("2026-02-15")
    def test_season_bounds_fall_back_to_calendar_when_no_data(self) -> None:
        """
        When no RegionDayRating rows exist for the season, data-season-start
        and data-season-end fall back to the calendar Nov 1 / May 31 window.
        """
        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()

        # Calendar fallback for the 2025/2026 season
        assert 'data-season-start="2025-11-01"' in content
        assert 'data-season-end="2026-05-31"' in content
