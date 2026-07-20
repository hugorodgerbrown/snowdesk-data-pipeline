"""
tests/e2e/test_community_reports.py — Playwright tests for the SNOW-419
"Community reports" map overlay toggle, plus (SNOW-475) a regression test
for the field-report submission form's MANUAL centre-pin path — both live
here since they share the ``observations.FieldObservation`` model.

Uses plain ``page`` + ``live_server`` fixtures (no signed-in session
needed — unlike favourites, the overlay shows anonymised, publicly-shared
data with no per-user eligibility gate). Every test still needs
``@override_flag("community_reports", active=True)`` since the flag is
seeded ``superusers=True`` in production and the test client is
anonymous; ``override_flag`` mutates the ``Flag.everyone`` DB column,
visible to the live-server thread since pytest-django's ``live_server``
runs in-process (mirrors ``test_favourites.py``).

The SNOW-475 test below is signed-in (``field_observations`` requires
authentication + a verified account — see ``observations.views._auth_gate``)
and drives the MANUAL location-source path by dispatching
``snowdesk:geolocate-error`` directly rather than waiting on a real
(ungranted) geolocation permission — deterministic in headless Chromium,
and mirrors the explicit-dispatch technique ``test_favourites.py`` uses for
``snowdesk:favourite-selected``.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from observations.models import FieldObservation
from tests.e2e.conftest import _session_login
from tests.factories import AccountFactory, FieldObservationFactory, UserFactory


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


@override_flag("community_reports", active=True)
@pytest.mark.django_db(transaction=True)
def test_overlay_toggle_installs_clustered_source(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Enabling the overlay fetches the geojson and installs the clustered source.

    SNOW-472: also covers the shared SDF flag icon that replaced the
    per-OBSERVATION_TYPE text glyphs — asserts the point layer references
    it and that ``map.addImage`` actually registered it. Does not cover
    the basemap-swap re-registration path (the riskiest part of that
    change, since ``setStyle`` wipes registered images and
    ``installCommunityReportsLayer`` has to re-add it) — a real basemap
    swap needs a live fetch to the swisstopo/IGN/ESRI style JSON, which
    isn't available to this harness. First-load registration is exercised
    here instead.
    """
    with django_db_blocker.unblock():
        FieldObservationFactory.create(
            latitude=46.2,
            longitude=7.6,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )

    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")
    # Default off — unlike favourites, a shared layer of other people's
    # reports is opt-in.
    assert toggle.get_attribute("aria-checked") == "false"

    with page.expect_response(lambda r: "/api/community-reports.geojson" in r.url):
        toggle.click()

    assert toggle.get_attribute("aria-checked") == "true"

    page.wait_for_function("() => !!MAP.getSource('community-reports')")
    assert page.evaluate("() => !!MAP.getLayer('community-reports-clusters')")
    assert page.evaluate("() => !!MAP.getLayer('community-reports-cluster-count')")
    assert page.evaluate("() => !!MAP.getLayer('community-reports-point')")

    # SNOW-472: the unclustered point layer draws a single shared SDF flag
    # icon (not a per-OBSERVATION_TYPE text glyph) — assert the layer
    # references THAT specific icon id (not just "some icon is set"), and
    # that the icon was actually registered on the style via map.addImage.
    assert page.evaluate(
        "() => MAP.getLayoutProperty('community-reports-point', 'icon-image')"
        " === 'community-report-flag'"
    )
    assert page.evaluate("() => MAP.hasImage('community-report-flag')")

    source_data = page.evaluate(
        "() => MAP.getSource('community-reports').serialize().data"
    )
    assert len(source_data["features"]) == 1
    feature = source_data["features"][0]
    assert feature["properties"]["type"] == "WHUMPFING"
    # Anonymisation: only the 3dp-rounded pair crosses the wire.
    assert feature["geometry"]["coordinates"] == [7.6, 46.2]


@override_flag("community_reports", active=True)
@pytest.mark.django_db(transaction=True)
def test_point_tap_popup_shows_type_and_time_without_region(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Tapping a lone report pin opens a popup with the type + time only.

    SNOW-472: the region name was dropped from the popup — the pin's own
    position on the map already conveys where the report is, and the FK
    region can be coarser or cross-border than the visible spot. Assert the
    popup shows the OBSERVATION_TYPE label and carries no place label: the
    meta line is the relative time alone, with no ' · ' separator and not
    the seeded region's name anywhere in the card.
    """
    with django_db_blocker.unblock():
        obs = FieldObservationFactory.create(
            latitude=46.10,
            longitude=7.10,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        assert obs.region is not None
        region_name = obs.region.name

    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")
    with page.expect_response(lambda r: "/api/community-reports.geojson" in r.url):
        toggle.click()
    page.wait_for_function("() => !!MAP.getLayer('community-reports-point')")

    # Centre on the lone pin at a high zoom, then wait for the point layer's
    # own render pass.
    page.evaluate("() => MAP.jumpTo({ center: [7.10, 46.10], zoom: 14 })")
    page.wait_for_function(
        "() => MAP.queryRenderedFeatures("
        "{ layers: ['community-reports-point'] }).length === 1"
    )
    # Inflate the icon to a large, forgiving click target. Icon size is
    # independent of the popup content under test here, so growing it only
    # de-flakes the hit-test — a ~20px icon is an unreliable mouse target.
    page.evaluate(
        "() => MAP.setLayoutProperty('community-reports-point', 'icon-size', 3)"
    )
    page.wait_for_timeout(150)

    # The flag icon is anchored bottom-left at the coordinate, so its body
    # sits up and to the right of the projected point — aim into it, in
    # viewport coords (the canvas may be offset from the page origin).
    target = page.evaluate(
        "() => { const r = MAP.getCanvas().getBoundingClientRect();"
        " const p = MAP.project([7.10, 46.10]);"
        " return { x: r.left + p.x + 30, y: r.top + p.y - 45 }; }"
    )
    page.mouse.click(target["x"], target["y"])

    popup = page.locator(".community-report-popup")
    popup.wait_for(state="visible")

    assert page.locator(".community-report-popup__type").inner_text() == "Whumpfing"
    meta = page.locator(".community-report-popup__meta").inner_text()
    assert "·" not in meta
    assert region_name not in popup.inner_text()


@override_flag("community_reports", active=True)
@pytest.mark.django_db(transaction=True)
def test_cluster_tap_does_not_also_select_the_region_underneath(
    live_server: LiveServer,
    page: Page,
    django_db_blocker: Any,
    _load_test_data: None,
) -> None:
    """A cluster tap must not ALSO select/pop up the region underneath it.

    Regression coverage for the click-routing fix: MapLibre fires every
    layer-scoped click handler whose layer intersects the tapped point —
    there is no stopPropagation between them — so without the guard in
    the regions-fill handler, tapping a community-reports cluster sitting
    over a rendered region would also select that region and open its
    popup. Requires ``_load_test_data`` — without region boundary fixtures
    loaded, ``regions-fill`` renders no features anywhere, and the guard
    would trivially "pass" without ever being exercised.

    This drives the synthetic click via ``MAP.fire`` (the plain-click
    pattern ``test_favourites.py`` uses, deterministic in headless
    Chromium) at the cluster's exact projected point, and asserts on the
    *absence* of a region popup rather than on the cluster's own
    zoom-to-expand completing — ``getClusterExpansionZoom`` round-trips
    through a Web Worker that does not reliably resolve in this headless
    harness, which is a test-environment limitation unrelated to the
    click-routing fix under test here.
    """
    with django_db_blocker.unblock():
        # Coordinates inside CH-4115 (Martigny/Verbier — the FieldObservation
        # factory default territory), which _load_test_data seeds a region
        # boundary for, so regions-fill actually renders a feature here.
        # Several near-identical points cluster into one feature at the
        # map's initial zoom level.
        for _ in range(5):
            FieldObservationFactory.create(
                latitude=46.10,
                longitude=7.10,
                observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
            )

    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")
    with page.expect_response(lambda r: "/api/community-reports.geojson" in r.url):
        toggle.click()
    page.wait_for_function("() => !!MAP.getSource('community-reports')")
    # queryRenderedFeatures only returns results once the clustered
    # layer's own render pass has run at the current zoom — poll rather
    # than a single read.
    page.wait_for_function(
        "() => MAP.getLayer('community-reports-clusters') && "
        "MAP.queryRenderedFeatures({ layers: ['community-reports-clusters'] })"
        ".length > 0"
    )

    cluster_point = page.evaluate(
        "() => {"
        " const f = MAP.queryRenderedFeatures("
        "   { layers: ['community-reports-clusters'] })[0];"
        " const p = MAP.project(f.geometry.coordinates);"
        " return { lngLat: f.geometry.coordinates, point: [p.x, p.y] };"
        "}"
    )

    page.evaluate(
        "({ lngLat, point }) => MAP.fire('click', {"
        " lngLat: { lng: lngLat[0], lat: lngLat[1] },"
        " point: { x: point[0], y: point[1] } })",
        cluster_point,
    )
    # No async condition to await here — the routing guard runs
    # synchronously inside the click handler itself.
    page.wait_for_timeout(250)

    assert page.locator(".region-popup").count() == 0


@override_flag("community_reports", active=True)
@pytest.mark.django_db(transaction=True)
def test_overlay_toggle_persists_across_reload(
    live_server: LiveServer, page: Page
) -> None:
    """Flipping the community-reports overlay toggle on survives a reload."""
    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "false"

    toggle.click()
    assert toggle.get_attribute("aria-checked") == "true"

    stored = page.evaluate(
        "() => window.localStorage.getItem('snowdesk.map.overlay.community_reports')"
    )
    assert stored == "true"

    page.reload()
    page.wait_for_load_state("domcontentloaded")
    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "true"


@override_flag("field_observations", active=True)
@pytest.mark.django_db(transaction=True)
def test_report_manual_path_uses_centre_pin_without_a_map_click(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """SNOW-475: the MANUAL report path enables submission via the
    place-picker's centre pin, with no map click required.

    Routes to MANUAL by dispatching ``snowdesk:geolocate-error`` directly
    (deterministic in headless Chromium — no real, ungranted geolocation
    permission to race). Once the form settles, the centre pin must be
    visible and the problem buttons enabled without any synthetic map
    click; panning the map (``MAP.setCenter``) must then update the form's
    hidden lat/lon to the new centre — proving the pin itself stays fixed
    at the viewport centre while the map moves underneath it.
    """
    with django_db_blocker.unblock():
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=True)

    _session_login(page.context, live_server.url, user)
    _navigate_home(page, live_server.url)

    page.click("#report-btn")
    page.wait_for_selector("#report-sheet:not([hidden])")

    # Force the MANUAL branch deterministically rather than waiting on a
    # real (ungranted) geolocation permission to resolve or time out.
    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('snowdesk:geolocate-error'))"
    )
    page.wait_for_selector("#report-form")

    # The centre pin appears, and the problem buttons unblock immediately —
    # the place-picker's onChange fires once on activation with the current
    # (always-valid) map centre, with no "tap the map" step in between.
    page.wait_for_selector("#map-place-pin:not([hidden])")
    page.wait_for_function(
        "() => !document.querySelector("
        "'#report-form button[name=observation_type]').disabled"
    )

    # Pan the map — MAP.setCenter fires 'moveend', the same event a real
    # pan would. The pin's CSS keeps it fixed at 50%/50% of the viewport
    # throughout; only the coordinate captured below moves.
    page.evaluate("() => MAP.setCenter([7.6, 46.2])")
    page.wait_for_function(
        "() => { "
        "const el = document.querySelector('#report-form input[name=lat]'); "
        "return el && Math.abs(parseFloat(el.value) - 46.2) < 0.01; "
        "}"
    )
    lat = page.eval_on_selector("#report-form input[name=lat]", "el => el.value")
    lon = page.eval_on_selector("#report-form input[name=lon]", "el => el.value")
    assert float(lat) == pytest.approx(46.2, abs=0.01)
    assert float(lon) == pytest.approx(7.6, abs=0.01)
    assert page.locator("#map-place-pin:not([hidden])").count() == 1
