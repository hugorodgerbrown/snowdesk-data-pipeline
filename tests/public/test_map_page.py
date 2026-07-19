"""
tests/public/test_map_page.py — Tests for the canonical map page (/).

SNOW-344: /map/ is now a 301 redirect to /; all tests that previously
targeted /map/ now target / (via reverse("public:home")).

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
from waffle.testutils import override_flag

from tests.factories import (
    MicroRegionFactory,
    RegionDayRatingFactory,
    SubscriberFactory,
    UserFactory,
)


@pytest.mark.django_db
def test_map_page_renders() -> None:
    """GET / returns 200 and contains the map container."""
    client = Client()
    response = client.get(reverse("public:home"))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="map"' in content
    # SNOW-318: the region popup is restored; data-region-summary-url must be
    # present on #map so map.js can construct the per-region summary fetch URL.
    # The 'XX-0000' placeholder is the literal string Django renders for the
    # region_id kwarg; map.js string-replaces it with the real region id at
    # runtime.
    assert "data-region-summary-url" in content
    assert "XX-0000" in content
    # SNOW-236: data-season-end must be present on #map so map.js can clamp
    # the cold-open fetch to the last populated date after season end.
    assert 'data-season-end="' in content


@pytest.mark.django_db
def test_map_page_injects_api_urls() -> None:
    """
    The API URLs resolve via ``{% url %}`` and are exposed to JS through
    data-* attributes on the #map element. SNOW-239 replaced the legacy
    data-summaries-url + data-season-ratings-url with a single
    data-ratings-url pointing at the unified /api/ratings/ endpoint.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert f'data-regions-url="{reverse("api:regions_geojson")}"' in content
    assert f'data-ratings-url="{reverse("api:ratings")}"' in content
    assert f'data-resorts-url="{reverse("api:resorts_by_region")}"' in content
    assert f'data-resorts-geojson-url="{reverse("api:resorts_geojson")}"' in content


@pytest.mark.django_db
@freeze_time("2026-02-15")
def test_map_element_has_season_end_attribute() -> None:
    """
    SNOW-236: data-season-end must appear on the #map element itself
    (not just on #season-scrubber) so that map.js can read it at module
    scope during the cold-open boot path — before the scrubber IIFE runs.
    """
    region = MicroRegionFactory.create(region_id="CH-5500")
    RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 5))

    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    # Verify the attribute sits on the #map div, not only on #season-scrubber.
    map_div_start = content.index('id="map"')
    map_div_close_tag = content.index(">", map_div_start)
    map_div_attrs = content[map_div_start:map_div_close_tag]
    assert 'data-season-end="2026-03-05"' in map_div_attrs


@pytest.mark.django_db
def test_map_page_renders_resorts_overlay_toggle() -> None:
    """
    SNOW-78: a Resorts checkbox sits in the basemap-picker overlays
    section so the user can toggle the geocoded-resort pin layer on/off.
    Default is ``aria-checked="false"`` — the layer opens hidden.
    """
    client = Client()
    response = client.get(reverse("public:home"))
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
def test_map_page_renders_micro_regions_overlay_toggle() -> None:
    """
    SNOW-390: the Micro regions checkbox is a normal overlay toggle, matching
    L1 / L2 / L3 / Resorts. Default is ``aria-checked="true"`` — the
    danger-rating choropleth is visible on first paint — but the button is no
    longer locked with ``disabled`` / ``aria-disabled`` / a "required" tooltip.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'data-overlay-key="l4"' in content
    l4_btn_idx = content.index('data-overlay-key="l4"')
    next_li_idx = content.find("<li ", l4_btn_idx)
    button_scope = (
        content[l4_btn_idx:next_li_idx] if next_li_idx > 0 else content[l4_btn_idx:]
    )
    assert 'aria-checked="true"' in button_scope
    assert 'aria-disabled="true"' not in button_scope
    assert "disabled" not in button_scope
    assert "required" not in button_scope


@pytest.mark.django_db
def test_map_page_renders_resorts_legend_entry() -> None:
    """SNOW-78: the danger-scale legend includes a Resorts entry."""
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'data-testid="map-legend-resorts"' in content
    assert "map-legend-pin" in content


@pytest.mark.django_db
def test_map_page_renders_zoom_indicator() -> None:
    """
    SNOW-442: an always-visible zoom-level readout pill sits in the map
    utility cluster. map.js overwrites #map-zoom-indicator-value's text on
    load and on every zoom gesture; the server only seeds a placeholder.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'id="map-zoom-indicator"' in content
    assert 'id="map-zoom-indicator-value"' in content
    assert 'aria-live="polite"' in content


@pytest.mark.django_db
def test_map_page_loads_assets() -> None:
    """The page references the MapLibre library, the map CSS, and map JS."""
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert "maplibre-gl" in content
    assert "/static/css/map.css" in content
    assert "/static/js/map.js" in content


@pytest.mark.django_db
@override_settings(BASEMAP="swisstopo_winter")
def test_map_page_injects_default_basemap_key() -> None:
    """
    SNOW-58: ``settings.BASEMAP`` is rendered onto the #map element as
    ``data-default-basemap-key`` so the JS can fall back to the
    env-resolved default when localStorage is empty or names a basemap
    that has since been removed from the catalogue.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'data-default-basemap-key="swisstopo_winter"' in content


@pytest.mark.django_db
def test_map_page_renders_basemap_picker() -> None:
    """
    SNOW-58: the picker renders one ``menuitemradio`` button per entry
    in the ``basemaps`` context, each carrying ``data-basemap-key`` and
    ``data-basemap-url``. Order is curated server-side via
    ``_BASEMAP_LABELS``; verifying both keys are present is enough to
    pin the contract — JS resolves the active option at runtime.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'id="basemap-pill"' in content
    assert 'id="basemap-menu"' in content
    for key in ("openfreemap_liberty", "swisstopo_winter", "ign_plan", "basemap_at"):
        assert f'data-basemap-key="{key}"' in content
        assert f'data-basemap-url="{settings.BASEMAP_STYLES[key]}"' in content
    # ``swisstopo_light`` stays in BASEMAP_STYLES as a BASEMAP= env override
    # but is intentionally excluded from the picker (SNOW-367).
    assert 'data-basemap-key="swisstopo_light"' not in content


@pytest.mark.django_db
def test_map_view_passes_basemap_catalogue() -> None:
    """
    The view exposes ``basemaps`` and ``default_basemap_key`` in template
    context so the template can render the picker without re-deriving
    the catalogue from settings inline.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    ctx = response.context
    assert "basemaps" in ctx
    assert "default_basemap_key" in ctx
    keys = [bm["key"] for bm in ctx["basemaps"]]
    assert keys == ["openfreemap_liberty", "swisstopo_winter", "ign_plan", "basemap_at"]
    labels = [str(bm["label"]) for bm in ctx["basemaps"]]
    assert labels == ["Standard", "Swisstopo (CH)", "IGN (FR)", "basemap.at (AT)"]
    assert all({"key", "label", "url"} <= set(bm) for bm in ctx["basemaps"])
    assert ctx["default_basemap_key"] == settings.BASEMAP


@pytest.mark.django_db
def test_map_page_accepts_date_query_param() -> None:
    """
    SNOW-47: ``/?d=YYYY-MM-DD`` still 200s. The selected date is
    consumed entirely by JS (which reads ``location.search`` after the
    page loads), so the only server-side guarantee is that the page
    doesn't reject or strip the query string. The scrubber data
    attributes that the JS needs to interpret ``?d=`` must still be
    present in the rendered markup.
    """
    client = Client()
    response = client.get(reverse("public:home") + "?d=2026-02-15")
    assert response.status_code == 200
    content = response.content.decode()
    assert "data-season-start=" in content
    assert "data-season-end=" in content
    assert "data-today=" in content


@pytest.mark.django_db
def test_map_page_renders_unified_time_controls() -> None:
    """
    The play button (#scrubber-play) must be rendered server-side so the JS
    only has to wire behaviour onto pre-existing DOM.

    SNOW-314: the floating date pill (#map-date-pill) was removed from the
    scrubber controls; the date is now shown in the persistent region readout
    (#region-readout) which is part of the season ribbon.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'id="scrubber-play"' in content
    assert 'id="map-date-pill"' not in content


@pytest.mark.django_db
def test_map_page_renders_timelapse_transport_buttons() -> None:
    """
    The five transport buttons (skip-start, play-reverse, play-forward,
    skip-end) must all be present in the rendered markup so that map.js
    can wire behaviour onto pre-existing DOM nodes.  SNOW-230 replaced the
    old single-button speed cycler with a four-button layout; SNOW-315
    replaced the fast-forward button with a reverse button, making five
    controls total — assert the new IDs are present and the removed ones
    are absent.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'id="scrubber-skip-start"' in content
    assert 'id="scrubber-reverse"' in content
    assert 'id="scrubber-play"' in content
    assert 'id="scrubber-skip-end"' in content
    # Removed elements must no longer appear.
    assert 'id="scrubber-fast"' not in content
    assert 'id="scrubber-speed"' not in content
    assert "data-speed=" not in content


@pytest.mark.django_db
def test_map_page_no_offline_toggle_or_precache_url() -> None:
    """SNOW-79: the SNOW-9 "Save offline" button and its data-attribute are gone.

    The PWA shell SW now caches assets at runtime via stale-while-
    revalidate, so an explicit opt-in download UI is no longer needed.
    The previous ``#offline-toggle`` button + its
    ``data-offline-manifest-url`` attribute must not be in the rendered
    map page — keeping them around would re-introduce the "stuck on
    stale data" reports that motivated this rewrite.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'id="offline-toggle"' not in content
    assert "data-offline-manifest-url" not in content
    # The SNOW-9 opt-in was ``static/js/offline.js`` — a bare filename with
    # no prefix. The assertion is anchored on that closing quote so the
    # newer PWA scripts (``pwa_offline.js``, SNOW-377) whose names happen
    # to end with ``offline.js`` do not falsely trip it.
    assert '/offline.js"' not in content
    assert "'offline.js'" not in content


@pytest.mark.django_db
def test_map_page_inherits_pwa_manifest_link() -> None:
    """SNOW-79: every public page (incl. /) links the manifest from base.html."""
    client = Client()
    response = client.get(reverse("public:home"))
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
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert "maplibre-gl.min" in content
    assert "maplibre-gl.css" in content
    assert "unpkg.com" not in content


@pytest.mark.django_db
def test_map_page_renders_scrubber_loading_state() -> None:
    """
    SNOW-234: the scrubber is rendered with data-state="loading" and a
    loading placeholder element so the user sees feedback immediately
    while the season_ratings fetch is in flight. The transport controls
    stay in the DOM (hidden by CSS) so map.js can wire behaviour onto
    pre-existing nodes regardless of fetch timing.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'data-state="loading"' in content
    assert "season-scrubber-loading" in content
    assert "Season data loading" in content
    # Transport controls must remain in the DOM (hidden by CSS only).
    assert 'id="scrubber-play"' in content
    assert 'id="scrubber-reverse"' in content
    assert 'id="scrubber-skip-start"' in content
    assert 'id="scrubber-skip-end"' in content
    assert 'id="scrubber-fast"' not in content


@pytest.mark.django_db
def test_map_layer_menu_section_order() -> None:
    """
    SNOW-243: The basemap-menu popover must present sections in the order
    Countries → Overlays → Options → Base map.  This is a presentation
    reorder only; all items and their data-* attributes are unchanged.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    # Each label is unique in the rendered output; assert relative order.
    idx_countries = content.index("basemap-menu-section-label")
    # Find each label text after the first section-label class occurrence.
    idx_countries_label = content.index("Countries", idx_countries)
    idx_overlays_label = content.index("Overlays", idx_countries)
    idx_options_label = content.index("Options", idx_countries)
    idx_basemap_label = content.index("Base map", idx_countries)

    assert (
        idx_countries_label < idx_overlays_label < idx_options_label < idx_basemap_label
    ), (
        "Map layer menu sections are not in the expected order "
        "(Countries < Overlays < Options < Base map)"
    )


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
        response = client.get(reverse("public:home"))
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
        response = client.get(reverse("public:home"))
        content = response.content.decode()

        # Calendar fallback for the 2025/2026 season
        assert 'data-season-start="2025-11-01"' in content
        assert 'data-season-end="2026-05-31"' in content


# ---------------------------------------------------------------------------
# SNOW-324: report_mode — floating Report button on the map
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_flag("field_observations", active=False)
def test_report_button_not_shown_when_flag_off() -> None:
    """Report button is absent when field_observations flag is inactive."""
    subscriber = SubscriberFactory.create()
    client = Client()
    client.force_login(subscriber.user)
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert "report-btn" not in content


@pytest.mark.django_db
@override_flag("field_observations", active=True)
def test_report_button_shown_for_anonymous_with_signin_cta() -> None:
    """Report button is shown for anonymous users when flag is active (SNOW-333).

    Anonymous users see the button but are directed to sign in — report_eligible
    is False and data-signin-url is populated so report.js renders a CTA instead
    of the geolocation flow.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    # Button must be present so anonymous users can tap and see the sign-in CTA.
    assert "report-btn" in content
    # report_eligible must be false so JS branches to the CTA path.
    assert 'data-report-eligible="false"' in content
    # sign-in URL must be present so JS can build the CTA link.
    assert "data-signin-url" in content


@pytest.mark.django_db
@override_flag("field_observations", active=True)
def test_report_button_shown_for_subscriber_with_flag() -> None:
    """Report button and sheet are shown for a subscriber when flag is active."""
    subscriber = SubscriberFactory.create()
    client = Client()
    client.force_login(subscriber.user)
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert "report-btn" in content
    assert "report-sheet" in content
    assert "report.js" in content


@pytest.mark.django_db
@override_flag("field_observations", active=False)
def test_report_js_not_loaded_when_flag_off() -> None:
    """report.js is not referenced when the flag is inactive."""
    subscriber = SubscriberFactory.create()
    client = Client()
    client.force_login(subscriber.user)
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert "report.js" not in content


@pytest.mark.django_db
@override_flag("field_observations", active=True)
def test_report_eligible_true_for_authenticated_user() -> None:
    """Authenticated user has report_eligible=True in the rendered button (SNOW-333)."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert "report-btn" in content
    assert 'data-report-eligible="true"' in content
