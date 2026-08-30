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
import re
from html.parser import HTMLParser

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse
from freezegun import freeze_time

from tests.factories import (
    AccountFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
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
    micro-region boundary is visible on first paint — but the button is no
    longer locked with ``disabled`` / ``aria-disabled`` / a "required" tooltip.

    SNOW-656 narrowed what this row carries: the danger-rating choropleth it
    used to drive alongside the boundary is the separate Bulletins row below.
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
def test_map_page_renders_bulletin_fill_control() -> None:
    """
    SNOW-656: the danger choropleth and the dissolved bulletin boundary are
    governed by a five-step opacity control, not the Micro regions row.

    It is a roundel in the bottom-right stack whose flyout opens to the left.
    The five steps are 0 / 0.25 / 0.5 / 0.75 / 1 — 0 being the off position,
    so the control subsumes the toggle it replaced — and 0.5 is pre-checked,
    matching ``layer_visibility_core``'s DEFAULT_STEP for a device with
    nothing stored.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    assert 'id="map-fill-toggle"' in content
    assert 'id="map-fill-flyout"' in content

    for step in ("0", "0.25", "0.5", "0.75", "1"):
        assert f'data-bulletins-step="{step}"' in content, step

    # Exactly one step is pre-checked, and it is the default one. Each step is
    # a <button …>, so scoping to the tag that carries the step attribute is
    # what tells the checked one from its four siblings.
    buttons = re.findall(r"<button[^>]*data-bulletins-step=\"[\d.]+\"[^>]*>", content)
    assert len(buttons) == 5, buttons
    checked = [b for b in buttons if 'aria-checked="true"' in b]
    assert len(checked) == 1, checked
    assert 'data-bulletins-step="0.5"' in checked[0]


@pytest.mark.django_db
def test_bulletin_fill_control_is_inside_the_collapsible_group() -> None:
    """
    SNOW-656: the roundel sits in ``#map-controls-collapsible``, so it hides
    with the strip. SNOW-664 put the layers roundel in above it, so it is the
    second item there rather than the first — still below the always-visible
    locate roundel, which is what the assertion is about.

    The flyout itself is deliberately OUTSIDE that wrapper: the wrapper is
    ``overflow: hidden`` for its height animation, which clips both axes, so
    a panel opening leftward out of it would be cut off.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    collapsible_idx = content.index('id="map-controls-collapsible"')
    inner_end_idx = content.index('id="map-fill-flyout"')
    roundel_idx = content.index('id="map-fill-toggle"')
    locate_idx = content.index('id="locate-toggle"')

    assert locate_idx < collapsible_idx < roundel_idx, (
        "the fill roundel must follow locate, inside the collapsible group"
    )
    assert roundel_idx < inner_end_idx, "the flyout must come after the roundel"

    # The flyout is a sibling of the collapsible wrapper, not a descendant:
    # everything between the roundel and the flyout closes the wrapper.
    between = content[roundel_idx:inner_end_idx]
    assert between.count("</div>") >= 3, (
        "the flyout appears to still be inside #map-controls-collapsible, "
        "where overflow:hidden would clip it"
    )


@pytest.mark.django_db
def test_locate_is_the_only_roundel_outside_the_collapsible_group() -> None:
    """
    SNOW-664: a minimised control column is locate and the toggle, nothing
    else.

    "Where am I" is the one question worth a permanent control on a map the
    user is standing in; everything else in the column is a choice about what
    the map shows, and a choice can wait behind the toggle. The layers roundel
    used to stay out alongside locate — it is one slot down, inside the group,
    and FIRST there so it lands directly under locate when the group is open.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    stack_idx = content.index('id="map-controls-br"')
    locate_idx = content.index('id="locate-toggle"')
    collapsible_idx = content.index('id="map-controls-collapsible"')
    layers_idx = content.index('id="basemap-pill"')
    fill_idx = content.index('id="map-fill-pill"')

    assert stack_idx < locate_idx < collapsible_idx, (
        "locate must be the first child of the stack, outside the group"
    )
    assert collapsible_idx < layers_idx < fill_idx, (
        "the layers roundel must be the first item INSIDE the collapsible "
        "group, above the bulletin-fill roundel"
    )

    # Only locate stands between the stack opening and the group: any other
    # roundel here would still be on screen with the column minimised.
    before_group = content[stack_idx:collapsible_idx]
    assert before_group.count("map-utility-pill--") == 1, (
        "a second always-visible roundel has appeared beside locate"
    )


@pytest.mark.django_db
def test_layers_menu_is_outside_the_collapsible_group() -> None:
    """
    SNOW-664: ``#basemap-menu`` is a child of the stack, not of the
    ``#basemap-pill`` that opens it.

    The pill moved into ``#map-controls-collapsible``, which is
    ``overflow: hidden`` for its height animation — and that clips both axes,
    so a menu opening leftward out of it would be cut off with no other
    symptom. Same move, same reason, as ``#map-fill-flyout`` (SNOW-656).
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    pill_idx = content.index('id="basemap-pill"')
    menu_idx = content.index('id="basemap-menu"')
    flyout_idx = content.index('id="map-fill-flyout"')

    assert pill_idx < menu_idx < flyout_idx, (
        "the menu must sit between the collapsible group and the fill flyout"
    )

    # Everything between the pill and the menu closes the pill, the inner and
    # the wrapper — if the menu were still nested, it would not.
    between = content[pill_idx:menu_idx]
    assert between.count("</div>") >= 3, (
        "the layers menu appears to still be inside #map-controls-collapsible, "
        "where overflow:hidden would clip it"
    )


@pytest.mark.django_db
def test_bulletin_fill_control_is_in_the_help_tour() -> None:
    """SNOW-656: the control carries a coachmark step, between locate and the
    custom-area download — the roundel's own position in the stack.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    steps = re.findall(r'data-help-target="([^"]+)"', content)
    assert "#map-fill-toggle" in steps
    assert steps.index("#locate-toggle") < steps.index("#map-fill-toggle")
    assert steps.index("#map-fill-toggle") < steps.index("#map-custom-download-control")


@pytest.mark.django_db
def test_help_tour_walks_the_control_stack_in_dom_order() -> None:
    """
    SNOW-664: locate leads the bottom-right leg of the tour, then layers.

    The tour is a top-to-bottom walk of the page, so its order is a
    projection of the DOM's. When the two roundels swapped, the steps had to
    swap with them or the highlight ring would jump up the column and back
    down again.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    steps = re.findall(r'data-help-target="([^"]+)"', content)
    assert steps.index("#locate-toggle") < steps.index("#basemap-toggle")
    assert steps.index("#basemap-toggle") < steps.index("#map-fill-toggle")


@pytest.mark.django_db
def test_map_page_renders_resorts_legend_entry() -> None:
    """SNOW-78: the danger-scale legend includes a Resorts entry."""
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'data-testid="map-legend-resorts"' in content
    assert "map-legend-pin" in content


@pytest.mark.django_db
def test_map_data_attribution_section_ships_hidden() -> None:
    """SNOW-640: the "Map data" section starts collapsed, not empty.

    ``map.js``'s ``updateMapAttribution`` reveals it only once the active
    style yields at least one source attribution. Server-rendering it
    visible would paint a heading over a blank line for the whole window
    between first paint and the style resolving — and permanently, for a
    style that carries no attribution at all, which is the staging defect
    this ticket was raised for.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    section_idx = content.index('id="map-attribution-section"')
    # The `hidden` attribute has to be on the section element itself, not
    # merely somewhere nearby — scope the assertion to the opening tag.
    opening_tag = content[section_idx : content.index(">", section_idx)]
    assert "hidden" in opening_tag
    # The paragraph the JS fills stays inside it, so both hooks it looks
    # up by id are served together or not at all.
    assert 'id="map-attribution-text"' in content
    assert content.index('id="map-attribution-text"') > section_idx


@pytest.mark.django_db
def test_map_page_omits_zoom_indicator() -> None:
    """
    SNOW-445: the always-visible zoom-level readout pill (SNOW-442) was a
    developer/debug artefact and has been removed from the user-facing map.
    The live zoom is exposed on the console instead (window.snowdeskMap).
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert 'id="map-zoom-indicator"' not in content
    assert 'id="map-zoom-indicator-value"' not in content


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


def test_basemap_styles_openfreemap_liberty_matches_style_url_setting() -> None:
    """
    SNOW-242: ``BASEMAP_STYLES["openfreemap_liberty"]`` and
    ``OPENFREEMAP_STYLE_URL`` are derived from the same env-configurable
    setting — asserting equality here pins that single-source-of-truth
    contract rather than duplicating the URL.
    """
    assert (
        settings.BASEMAP_STYLES["openfreemap_liberty"] == settings.OPENFREEMAP_STYLE_URL
    )


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
    SNOW-243: The basemap-menu popover must present its sections in a fixed
    order.  This is a presentation reorder only; all remaining items and
    their data-* attributes are unchanged.

    SNOW-521: the Options section (Auto-zoom) was removed along with the
    L3 bulletin-groupings overlay and the basemap sync-status caption —
    see ``test_layers_menu_removed_items.py`` (e2e) for the absence
    coverage.

    SNOW-658 renamed the first two sections to say what their rows actually
    are — "Bulletins" (one row per PROVIDER) and "Boundaries" (one per EAWS
    level) — and split the trailing rows out of the tier list into their own
    sections: "Locations" for resorts.  SNOW-762 removed "Conditions" with
    the weather overlay; SNOW-761 rebuilt the overlay and the heading came
    back with it, still holding that one row.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    # Each label is unique in the rendered output; assert relative order.
    start = content.index("basemap-menu-section-label")
    positions = [
        content.index(label, start)
        for label in (
            "Bulletins",
            "Boundaries",
            "Locations",
            "Conditions",
            "Terrain",
            "Base map",
        )
    ]

    assert positions == sorted(positions), (
        "Map layer menu sections are not in the expected order "
        "(Bulletins < Boundaries < Locations < Conditions < Terrain < Base map)"
    )
    assert "Options" not in content


@pytest.mark.django_db
def test_map_layer_menu_renders_sync_status_dots() -> None:
    """
    SNOW-505: each always-rendered overlay row (l1/l2/l4/resorts) carries
    a server-rendered ``.sync-dot`` starting at ``data-sync-state="unknown"``
    — ``map_layer_sync_status.js`` resolves it to cached/uncached the first
    time the popover opens.

    SNOW-521 dropped the L3 (bulletin groupings) overlay row entirely.
    SNOW-656 briefly added a Bulletins row and then removed it again: the
    bulletin-fill control is a roundel on the map, not a row in this menu, so
    it has no dot here. Its feed (``/api/ratings/``) is one of the four a
    country load fetches, so a country missing it already shows red on its
    own row.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    for key in ("l1", "l2", "l4", "resorts"):
        key_idx = content.index(f'data-overlay-key="{key}"')
        button_close_idx = content.index("</button>", key_idx)
        button_scope = content[key_idx:button_close_idx]
        assert 'class="sync-dot" data-sync-state="unknown"' in button_scope, key
    assert 'data-overlay-key="l3"' not in content
    assert 'data-overlay-key="bulletins"' not in content


@pytest.mark.django_db
def test_map_layer_menu_has_no_user_data_rows() -> None:
    """
    SNOW-658: the favourites (eligible-only) and community_reports rows are
    gone from this menu — and so, deliberately, are their sync dots.

    Both are USER-GENERATED data with a roundel of their own, so each toggle
    moved into the panel that roundel opens (its "Display on the map"
    footer switch), driving
    ``window.pwaFavouritesOverlay`` / ``window.pwaCommunityReportsOverlay``.
    The dots did not move with them: a panel is not a cache-state dashboard,
    which is the same call SNOW-645 made for the downloaded-areas row.

    Asserted for a signed-in user, since the favourites row was rendered only
    for one — an anonymous request never had it to lose.
    """
    account = AccountFactory.create()
    client = Client()
    client.force_login(account.user)
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    for key in ("favourites", "community_reports"):
        assert f'data-overlay-key="{key}"' not in content, key
    # The switches that replaced them, in their own panels.
    assert 'id="map-favourites-overlay-toggle"' in content
    assert 'id="map-community-reports-overlay-toggle"' in content


@pytest.mark.django_db
def test_basemap_menu_omits_sync_status_caption() -> None:
    """
    SNOW-521 removed the "Browsed areas only" ``#basemap-sync-status``
    caption row from the "Base map" section — its coverage story is now
    carried entirely by the per-region download icon in
    ``#region-readout``, not a basemap-wide caption.
    """
    client = Client()
    response = client.get(reverse("public:home"))
    content = response.content.decode()

    assert 'id="basemap-sync-status"' not in content


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
def test_report_button_shown_for_anonymous_with_signin_cta() -> None:
    """Report button is shown for anonymous users (SNOW-333).

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
    # report_unverified must be false — anonymous users get the sign-in CTA, not
    # the "verify your email" prompt (SNOW-477).
    assert 'data-report-unverified="false"' in content
    # sign-in URL must be present so JS can build the CTA link.
    assert "data-signin-url" in content


@pytest.mark.django_db
def test_report_button_shown_for_account() -> None:
    """Report button and sheet are shown for a logged-in account."""
    account = AccountFactory.create()
    client = Client()
    client.force_login(account.user)
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert "report-btn" in content
    assert "report-sheet" in content
    assert "report.js" in content


@pytest.mark.django_db
def test_report_eligible_true_for_verified_user() -> None:
    """A verified user has report_eligible=True in the rendered button.

    Eligibility requires both authentication and a verified ``Account`` — the
    same gate the server enforces (SNOW-477) — so a bare authenticated user is
    not enough.
    """
    user = UserFactory.create()
    AccountFactory.create(user=user, is_verified=True)
    client = Client()
    client.force_login(user)
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert "report-btn" in content
    assert 'data-report-eligible="true"' in content
    assert 'data-report-unverified="false"' in content


@pytest.mark.django_db
def test_report_list_url_asks_for_the_map_variant() -> None:
    """The panel's list URL carries ``?variant=map`` (SNOW-752).

    That is what makes each row's label a control framing the report on the
    map behind the sheet.  ``observation_list`` set the flag unconditionally
    until this ticket; the parameter exists so ``/account/observations/`` can
    read the same endpoint and NOT get those rows, having no map to fly.
    Same spelling as the favourites and routes lists, which is the point of
    having a convention.
    """
    user = UserFactory.create()
    AccountFactory.create(user=user, is_verified=True)
    client = Client()
    client.force_login(user)
    content = client.get(reverse("public:home")).content.decode()

    assert (
        f'data-report-list-url="{reverse("observations:list")}?variant=map"' in content
    )


@pytest.mark.django_db
def test_report_unverified_for_authenticated_unverified_user() -> None:
    """An authenticated-but-unverified user is not eligible but is flagged unverified.

    This is the SNOW-477 case: the client used to mark them eligible (auth
    only), so ``report.js`` fired the form-load GET which the server 403'd.
    ``report_eligible`` is now False and ``report_unverified`` is True so the
    sheet shows a "verify your email" prompt instead.
    """
    user = UserFactory.create()
    AccountFactory.create(user=user, is_verified=False)
    client = Client()
    client.force_login(user)
    response = client.get(reverse("public:home"))
    content = response.content.decode()
    assert "report-btn" in content
    assert 'data-report-eligible="false"' in content
    assert 'data-report-unverified="true"' in content


# ---------------------------------------------------------------------------
# Hover affordance (SNOW-658)
# ---------------------------------------------------------------------------

# Every clickable control on the map, by the class that identifies it. Each
# one had its own hover treatment before this ticket — four of them across
# these seven names — and each now carries the shared ``hover-affordance``
# class instead (src/css/main.css: pointer cursor plus a translucent
# infill).
MAP_CONTROL_CLASSES = (
    "map-utility-button",
    "basemap-menu-item",
    "map-fill-step",
    "map-download-control",
    "map-legend-toggle",
    "map-controls-toggle",
)

_CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')


@pytest.mark.django_db
@pytest.mark.parametrize("control", MAP_CONTROL_CLASSES)
def test_every_map_control_carries_the_shared_hover_affordance(
    control: str,
) -> None:
    """A control a user can click says so under the pointer, the same way.

    Hugo: "The affordances are inconsistent - for all interactive elements
    (roundels, 'x' closure, 'add' buttons) it should be consistent on hover
    - change the mouse pointer, and add infill." The treatment lives in one
    class rather than in a hover pair per call site, which is what let four
    of them drift apart; this test is what stops a new control — or a new
    copy of an existing one — shipping without it.
    """
    client = Client()
    content = client.get(reverse("public:home")).content.decode()

    occurrences = [
        classes
        for classes in _CLASS_ATTR_RE.findall(content)
        if control in classes.split()
    ]
    assert occurrences, f"no {control} rendered — has it been renamed?"
    for classes in occurrences:
        assert "hover-affordance" in classes.split(), classes


@pytest.mark.django_db
@override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
@freeze_time("2026-02-17")
def test_ribbon_action_carries_the_shared_hover_affordance() -> None:
    """The ribbon's "view bulletin" roundel takes the same treatment.

    It sits in the ribbon header beside the per-region download roundel, so
    the two disagreeing on hover would be a new inconsistency in the very
    row this ticket set out to make consistent. It renders only with a
    focused region's season data, which is why it is not in the
    parametrised sweep above.
    """
    region = MicroRegionFactory.create(region_id="CH-4115")
    RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 2, 17))
    client = Client()

    content = client.get(reverse("public:home")).content.decode()

    action = [
        classes
        for classes in _CLASS_ATTR_RE.findall(content)
        if "region-readout-action" in classes.split()
    ]
    assert action, "the ribbon did not render — has its data gate changed?"
    for classes in action:
        assert "hover-affordance" in classes.split(), classes


class _CollapsibleChildCounter(HTMLParser):
    """Count the direct element children of ``.map-controls-collapsible-inner``.

    Stdlib rather than a parser dependency: the markup being counted is a
    flat run of ``<div>``/``<button>`` roundels whose only descendants are
    balanced ``<svg>`` wrappers and self-closing ``<path>``/``<polygon>``
    shapes, which ``html.parser`` handles without help. ``_VOID`` is carried
    anyway so a future ``<img>`` or ``<input>`` inside a roundel cannot throw
    the depth count off silently.
    """

    #: Elements that never take a closing tag, so they must not open a level.
    _VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self) -> None:
        """Start outside the target element with an empty count."""
        super().__init__(convert_charrefs=True)
        self.count = 0
        self._depth: int | None = None
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Enter the target element, or count/descend once inside it."""
        if self._done:
            return
        if self._depth is None:
            classes = dict(attrs).get("class") or ""
            if "map-controls-collapsible-inner" in classes.split():
                self._depth = 0
            return
        if tag in self._VOID:
            # Opens no level, so it is a child at the current depth rather
            # than a new one.
            if self._depth == 0:
                self.count += 1
            return
        self._depth += 1
        if self._depth == 1:
            self.count += 1

    def handle_endtag(self, tag: str) -> None:
        """Leave the current level, latching shut on the target's own close."""
        if self._done or self._depth is None or tag in self._VOID:
            return
        if self._depth == 0:
            # The target element's own closing tag. Latch rather than reset:
            # without this the counter would treat everything after the stack
            # as a fresh level-1 run and keep counting.
            self._done = True
            return
        self._depth -= 1


@pytest.mark.django_db
def test_collapsible_group_css_fallback_matches_the_rendered_child_count() -> None:
    """map.css's ``--map-controls-count`` fallback must equal the real count.

    The fallback is only read before mapControlsCollapseInit publishes the
    live count — but ``.map-controls-br`` is server-rendered
    ``data-expanded="true"``, so a stale fallback is a visible clip rather
    than an unused default: ``#map-controls-collapsible`` is
    ``overflow: hidden``, and a group sized for fewer children than it holds
    swallows the ones at the top until the JS corrects it.

    It has gone stale once already — it sat at 4 after SNOW-664 moved the
    layers roundel into the group and SNOW-656 added the bulletin fill,
    hiding both on a pre-JS paint. This is a cross-file invariant that
    neither file can hold alone, which is why it is asserted here rather
    than left to the comment above the rule.
    """
    client = Client()
    content = client.get(reverse("public:home")).content.decode()

    counter = _CollapsibleChildCounter()
    counter.feed(content)
    assert counter.count, (
        "no .map-controls-collapsible-inner children found — has the "
        "bottom-right control stack been restructured?"
    )

    css = (settings.BASE_DIR / "static" / "css" / "map.css").read_text()
    fallbacks = {int(n) for n in re.findall(r"--map-controls-count,\s*(\d+)", css)}
    assert fallbacks, "the --map-controls-count fallback has gone from map.css"
    assert fallbacks == {counter.count}, (
        f"map.css falls back to {sorted(fallbacks)} but the stack renders "
        f"{counter.count} children — update the fallback in "
        f"`.map-controls-br[data-expanded='true'] #map-controls-collapsible`."
    )


@pytest.mark.django_db
def test_terrain_row_renders_when_tile_url_configured() -> None:
    """SNOW-691: the Terrain section and its Slope angle row render.

    The row carries the same ``.sync-dot`` its siblings do, and the tile
    template reaches ``#map`` as ``data-slope-tile-url`` for map.js to build
    the raster source from.
    """
    client = Client()
    content = client.get(reverse("public:home")).content.decode()

    assert 'data-overlay-key="slope"' in content
    assert "Terrain" in content
    assert 'data-slope-layer-eligible="true"' in content
    assert "data-slope-tile-url=" in content

    key_idx = content.index('data-overlay-key="slope"')
    button_scope = content[key_idx : content.index("</button>", key_idx)]
    assert 'class="sync-dot" data-sync-state="unknown"' in button_scope


@pytest.mark.django_db
@override_settings(SLOPE_TILE_URL="")
def test_terrain_row_absent_without_tile_url() -> None:
    """SNOW-724: clearing SLOPE_TILE_URL takes the whole overlay out.

    This is the operator kill switch that ``slope_layer``'s
    ``everyone=False`` used to be — the setting is env-overridable, so
    withdrawing a third-party raster whose licence position is still open
    is a restart rather than a deploy. It has to leave the DOM clean. In
    particular the tile template must not be emitted: there is no Snowdesk
    endpoint behind it to 403, so a template rendered for an ineligible
    page would be an invitation to install the layer anyway.

    The heading is asserted absent alongside the row because it sits inside
    the same eligibility check — SNOW-658's lesson that a section whose only
    row is gated must gate its heading too, or the menu grows an empty
    "Terrain".
    """
    client = Client()
    content = client.get(reverse("public:home")).content.decode()

    assert 'data-overlay-key="slope"' not in content
    assert "Terrain" not in content
    assert 'data-slope-layer-eligible="false"' in content
    assert "data-slope-tile-url=" not in content


@pytest.mark.django_db
def test_terrain_section_follows_locations_in_the_layer_menu() -> None:
    """SNOW-691: Terrain sits after Locations and before Base map.

    Slope is a permanent property of the ground rather than something that
    changes with the scrubbed day, so it gets a section of its own rather
    than a row under Conditions — and it belongs with the other
    view-controls, above the basemap list that closes the menu.

    Overlaps ``test_map_layer_menu_section_order`` above, which since
    SNOW-724 can name Terrain too — kept because this one exists to pin
    Terrain's position specifically, and would be the test to update if the
    section ever moved.
    """
    client = Client()
    content = client.get(reverse("public:home")).content.decode()

    start = content.index("basemap-menu-section-label")
    positions = [
        content.index(label, start)
        for label in ("Bulletins", "Boundaries", "Locations", "Terrain", "Base map")
    ]
    assert positions == sorted(positions), (
        "Map layer menu sections are not in the expected order "
        "(Bulletins < Boundaries < Locations < Terrain < Base map)"
    )


@pytest.mark.django_db
def test_slope_legend_section_renders_with_its_caveat() -> None:
    """SNOW-691: the legend carries the five classes and a route to the caveats.

    The link is the reason this test exists rather than a bare "the swatches
    are present". The caveats moved to /help/ because they ran to several
    paragraphs in a panel that has room for none — but a shading a reader
    might mistake for permission cannot ship with no path to the warnings,
    so the heading being a link is the load-bearing part.
    """
    client = Client()
    content = client.get(reverse("public:home")).content.decode()

    assert 'data-testid="map-legend-slope"' in content
    for band in ("30–35°", "35–40°", "40–45°", "45–50°", "Over 50°"):
        assert band in content, band
    # The caveats live on /help/, not in the legend — but the legend must
    # still be the way to reach them, or a reader gets five colours and no
    # warning that the layer averages the ground and stops at a border.
    assert "#help-topic-slope" in content
