"""
tests/e2e/test_overlay_basemap_persistence.py — Playwright regression tests
for SNOW-473 (overlay tier visibility reverting on basemap swap) and
SNOW-493 findings 2/3 (favourites dropped, and foreign-country regions
duplicated, on the same basemap swap).

``overlayState`` in ``static/js/map.js`` was seeded once at boot from
localStorage and never updated by the basemap picker's toggle handler
(which writes localStorage + mutates the live layer only), so the
``styledata`` re-install handler — which runs after every
``MAP.setStyle()`` call — re-seeded layer visibility from the stale
``overlayState``, reverting every tier to its boot value: L4 turned off
came back on, and a runtime-enabled lazy tier (l1/l2/l3/resorts/
community_reports) vanished.

Every test here drives a genuine, empty-style swap via
``_engage_fallback_style_and_wait_for_regions`` — a real MapLibre
``error`` event that engages the SNOW-483 inline fallback style, which
truly removes every runtime-added source and so makes the ``styledata``
handler's early-return guard (``if (map.getSource('regions')) return``)
false and runs its whole reinstall body. See that helper's docstring for
why the tempting ``MAP.setStyle(MAP.getStyle(), {diff: false})`` shortcut
is NOT used: ``getStyle()`` serialises our runtime sources into the
snapshot it returns, so ``setStyle`` re-applies a style that still
contains 'regions'/'favourites'/etc, the guard fires immediately, and the
reinstall body — the exact code every test here targets — never executes.
A test using that shortcut would pass even with the reinstall logic
deleted.

SNOW-493 findings 2/3: the same ``styledata`` handler never re-installed
the favourites layer at all (finding 2), and re-fetched every enabled
foreign country's L1/L2/L4 data on every swap even though the merged
caches already held it, duplicating those features each time (finding 3).
``test_favourite_pin_survives_basemap_swap`` and
``test_repeated_basemap_swap_does_not_duplicate_country_regions`` cover
those two respectively.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from django.core.management import call_command
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from tests.e2e.conftest import _session_login
from tests.factories import AccountFactory, FavouriteFactory


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _engage_fallback_style_and_wait_for_regions(page: Page) -> None:
    """Force a genuine, empty-style swap and wait for ``regions`` to reappear.

    The tempting shortcut ``MAP.setStyle(MAP.getStyle(), {diff: false})``
    turns out NOT to actually remove our runtime-added sources in this
    MapLibre version — ``getStyle()`` serialises them into the snapshot it
    returns, so ``setStyle`` re-applies a style that already contains
    'regions' / 'favourites' / etc, and the ``styledata`` re-install
    handler's own early-return guard (``if (map.getSource('regions'))
    return``) then skips its entire body, including the exact
    re-fetch/re-install logic every test in this module targets. Verified
    empirically: instrumenting ``installRegionsLayers`` with a call counter
    showed it never runs a second time via that technique, even though a
    test asserting only on layout *visibility* would still pass — the
    untouched snapshot preserves visibility correctly regardless of whether
    the reinstall block executes at all.

    This helper instead engages the SNOW-483 inline fallback style via the
    same technique ``test_offline_map.py``'s
    ``test_non_tile_error_with_no_style_loaded_still_engages_fallback``
    uses — a genuine MapLibre ``error`` event with ``isStyleLoaded``
    stubbed false — which calls ``map.setStyle(buildFallbackStyle())``
    with a truly empty style (zero sources). That really does remove
    every source, so the ``styledata`` handler's guard is false and its
    body — the code findings 2/3 fix — genuinely executes. Confirmed via
    the same instrumentation: the install counter increments on this path.
    """
    page.evaluate(
        """async () => {
            const original = MAP.isStyleLoaded;
            MAP.isStyleLoaded = () => false;
            try {
                MAP.fire('error', { error: new Error('style fetch failed') });
                const deadline = Date.now() + 5000;
                while (Date.now() < deadline) {
                    if (MAP.getStyle()?.name === 'snowdesk-offline-fallback') return;
                    await new Promise((r) => setTimeout(r, 50));
                }
            } finally {
                MAP.isStyleLoaded = original;
            }
        }"""
    )
    page.wait_for_function("() => !!MAP.getSource('regions')", timeout=10000)


@pytest.mark.django_db(transaction=True)
def test_l4_stays_hidden_across_basemap_swap(
    live_server: LiveServer, page: Page
) -> None:
    """Turning L4 (micro regions) off must survive a basemap swap.

    Regression coverage for SNOW-473: before the fix, the stale
    ``overlayState.l4`` (seeded ``true`` at boot) was re-applied by the
    ``styledata`` handler's ``installRegionsLayers`` call, silently turning
    L4 back on the moment the user changed basemap.
    """
    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="l4"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "true"

    toggle.click()
    assert toggle.get_attribute("aria-checked") == "false"
    assert (
        page.evaluate("() => MAP.getLayoutProperty('regions-fill', 'visibility')")
        == "none"
    )

    _engage_fallback_style_and_wait_for_regions(page)

    assert (
        page.evaluate("() => MAP.getLayoutProperty('regions-fill', 'visibility')")
        == "none"
    )


@pytest.mark.django_db(transaction=True)
def test_runtime_enabled_tier_survives_basemap_swap(
    live_server: LiveServer, page: Page
) -> None:
    """Enabling a lazy tier (resorts) at runtime must survive a basemap swap.

    Regression coverage for SNOW-473: before the fix, the stale
    ``overlayState.resorts`` (seeded ``false`` at boot — the picker's
    ``snowdesk:overlay-load`` handler reveals the layer but never sets
    ``overlayState.resorts = true``) was re-applied by
    ``installResortsLayer``, so the resorts pins vanished the moment the
    user changed basemap.
    """
    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="resorts"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "false"

    with page.expect_response(lambda r: "resorts" in r.url and ".geojson" in r.url):
        toggle.click()
    assert toggle.get_attribute("aria-checked") == "true"

    page.wait_for_function(
        "() => MAP.getLayer('resorts-pin') && "
        "MAP.getLayoutProperty('resorts-pin', 'visibility') === 'visible'"
    )

    _engage_fallback_style_and_wait_for_regions(page)

    # The install fns run synchronously within the styledata handler, so once
    # the regions source is back the resorts layer exists too; assert its
    # re-seeded visibility directly (the bug left it 'none').
    assert (
        page.evaluate("() => MAP.getLayoutProperty('resorts-pin', 'visibility')")
        == "visible"
    )


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_favourite_pin_survives_basemap_swap(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """SNOW-493 finding 2: the favourites pin must survive a basemap swap.

    Favourites is default-on (SNOW-414), so the boot-time
    ``ensureOverlayLoaded('favourites')`` fetch installs the pin
    automatically for an eligible signed-in user. Before the fix, the
    ``styledata`` re-install handler never re-installed the favourites
    layer at all (only community-reports and the country tiers were
    re-added), so the pin silently vanished on every basemap change even
    though ``favouritesGeojsonCache`` still held the data.
    """
    with django_db_blocker.unblock():
        account = AccountFactory.create()
        favourite = FavouriteFactory.create(user=account.user, name="Swap Peak")
    fav_uuid = str(favourite.uuid)

    _session_login(page.context, live_server.url, account.user)
    _navigate_home(page, live_server.url)

    page.wait_for_function(
        f"""() => {{
            const src = MAP.getSource('favourites');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.uuid === {fav_uuid!r},
            );
        }}"""
    )

    _engage_fallback_style_and_wait_for_regions(page)

    # The styledata handler installs synchronously once the regions
    # source reappears, so the favourites source/feature should already
    # be back by the time _engage_fallback_style_and_wait_for_regions
    # returns.
    still_present = page.evaluate(
        f"""() => {{
            const src = MAP.getSource('favourites');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.uuid === {fav_uuid!r},
            );
        }}"""
    )
    assert still_present, "the favourite pin must survive a basemap swap"


@pytest.mark.django_db(transaction=True)
def test_repeated_basemap_swap_does_not_duplicate_country_regions(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """SNOW-493 finding 3: a foreign country's regions must not duplicate.

    Before the fix, the ``styledata`` handler reset ``loadedCountries``
    down to just ``ch`` and re-ran ``ensureCountryLoaded`` for every
    enabled country on every swap, re-fetching and re-merging data that
    ``geojsonCache``/``majorGeojsonCache``/``subGeojsonCache`` already
    held — so France's feature count grew on every basemap change instead
    of staying constant.
    """
    with django_db_blocker.unblock():
        call_command("loaddata", "eaws_FR", verbosity=0)

    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="country.fr"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "false"

    with page.expect_response(lambda r: "regions" in r.url and "country=fr" in r.url):
        toggle.click()

    page.wait_for_function(
        """() => {
            const src = MAP.getSource('regions');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.country === 'FR',
            );
        }"""
    )

    def _fr_feature_count() -> int:
        return cast(
            int,
            page.evaluate(
                """() => {
                const src = MAP.getSource('regions');
                const data = src.serialize().data;
                return (data.features || []).filter(
                    (f) => f.properties && f.properties.country === 'FR',
                ).length;
            }"""
            ),
        )

    baseline = _fr_feature_count()
    assert baseline > 0, "expected at least one FR region feature to load"

    _engage_fallback_style_and_wait_for_regions(page)
    assert _fr_feature_count() == baseline, (
        "FR region feature count must not grow after a basemap swap"
    )

    _engage_fallback_style_and_wait_for_regions(page)
    assert _fr_feature_count() == baseline, (
        "FR region feature count must not grow after a second basemap swap"
    )
