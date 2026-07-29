"""
tests/e2e/test_home_intro_tour.py — Playwright tests for the SNOW-535
#home-intro dismiss controls and their wiring into the map-help coachmark
tour.

home_intro.js and map_help.js each have their own Vitest coverage
(tests/js/test_home_intro.js, tests/js/test_map_help.js) for their own
internal state machines. What is worth a real browser for is the part that
spans both scripts on a real page:

1. The "×" (#home-intro-close) dismisses the card and the dismissal
   persists across an actual reload — not a re-imported module.
2. The "Explore the map" CTA (#home-intro-dismiss) dismisses the card AND
   opens the tour — the cross-script wiring itself (a
   'snowdesk:map-help-requested' CustomEvent home_intro.js dispatches and
   map_help.js answers).
3. The tour does not auto-start on a first homepage load (fresh
   localStorage) now that home.html opts #map-help-overlay out of
   map_help.js's auto-start, and still opens from the "?" roundel
   (#map-help-toggle) regardless.
4. '?intro=1' re-opens an already-dismissed panel on a real navigation,
   and is stripped from the address bar on dismissal — the round-trip a
   query parameter survives and a '#about' hash does not, which is the
   whole reason the parameter exists.

navigator.serviceWorker is stripped before any page script runs (the
test_report_sheet.py / test_edit_resorts_panel.py idiom) so a real SW can't
intercept the reload in test 1 or leave a stale shell for a later test in
the same worker.
"""

from __future__ import annotations

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

STORAGE_KEY = "snowdesk.home.intro"
DISMISSED_VALUE = "dismissed"
MAP_HELP_STORAGE_KEY = "snowdesk.map.help"


def _assert_tour_on_first_step(page: Page) -> None:
    """Assert the open coachmark tour is showing step 1.

    ``#map-help-step-count`` is styled ``text-transform: uppercase``, and
    ``inner_text()`` returns text as rendered — so the DOM's "Step 1 of 13"
    arrives as "STEP 1 OF 13". Compare case-insensitively rather than
    against the casing of either the template string or the CSS.
    """
    count = page.locator("#map-help-step-count").inner_text().strip()
    assert count.upper().startswith("STEP 1"), (
        f"the tour should open on step 1; step counter read {count!r}"
    )


def _navigate_home_with_sw_stripped(
    page: Page, live_server_url: str, query: str = ""
) -> None:
    """Load / with navigator.serviceWorker stripped, wait for DOMContentLoaded.

    Stripping serviceWorker (before any page script runs) makes
    sw_register.js bail out immediately — mirrors
    test_report_sheet.py / test_edit_resorts_panel.py's identical helper —
    so a real SW can neither intercept this test's reload nor leave a
    stale shell behind for a later test in the same worker.

    ``query`` is appended verbatim (including its leading "?") for the
    ?intro=1 force-open case.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server_url}/{query}")
    page.wait_for_load_state("domcontentloaded")


def test_close_button_dismisses_and_persists_across_reload(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """The "×" hides #home-intro and the dismissal survives a real reload."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home_with_sw_stripped(page, live_server.url)

    intro = page.locator("#home-intro")
    assert intro.is_visible(), "#home-intro should be showing on a fresh visit"

    page.locator("#home-intro-close").click()

    assert intro.get_attribute("hidden") is not None, 'the "×" should hide #home-intro'
    assert page.evaluate(f"() => localStorage.getItem({STORAGE_KEY!r})") == (
        DISMISSED_VALUE
    ), 'the "×" should persist the dismissed flag, same as the CTA'

    _navigate_home_with_sw_stripped(page, live_server.url)

    assert page.locator("#home-intro").get_attribute("hidden") is not None, (
        "a persisted dismissal must keep #home-intro hidden on the next load"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


def test_explore_cta_dismisses_and_opens_the_tour(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """The "Explore the map" CTA dismisses #home-intro AND opens the tour."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home_with_sw_stripped(page, live_server.url)

    intro = page.locator("#home-intro")
    assert intro.is_visible(), "#home-intro should be showing on a fresh visit"
    tour = page.locator("#map-help-overlay")
    assert tour.get_attribute("hidden") is not None, "the tour must not already be open"

    page.locator("#home-intro-dismiss").click()

    assert intro.get_attribute("hidden") is not None, (
        'the CTA should dismiss #home-intro, same as the "×"'
    )
    assert page.evaluate(f"() => localStorage.getItem({STORAGE_KEY!r})") == (
        DISMISSED_VALUE
    )
    page.wait_for_selector("#map-help-overlay:not([hidden])", timeout=5_000)
    _assert_tour_on_first_step(page)
    assert page_errors == [], f"JS errors: {page_errors}"


def test_intro_query_param_forces_a_dismissed_panel_back_open(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """?intro=1 re-opens a dismissed panel, and is stripped once dismissed again.

    SNOW-535: the parameter is the handle for QA, screenshots and bug
    reports — unlike the '#about' hash it survives a server round-trip. It
    must beat the persisted dismissed flag on load, then take itself out of
    the address bar so the next reload respects the fresh dismissal.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # Dismiss it first, so the persisted flag is what the parameter overrides.
    _navigate_home_with_sw_stripped(page, live_server.url)
    page.locator("#home-intro-close").click()
    assert page.evaluate(f"() => localStorage.getItem({STORAGE_KEY!r})") == (
        DISMISSED_VALUE
    )

    # A plain reload stays dismissed …
    _navigate_home_with_sw_stripped(page, live_server.url)
    assert page.locator("#home-intro").get_attribute("hidden") is not None

    # … but ?intro=1 forces it back open.
    _navigate_home_with_sw_stripped(page, live_server.url, query="?intro=1")
    intro = page.locator("#home-intro")
    assert intro.get_attribute("hidden") is None, (
        "?intro=1 must re-open the panel despite the persisted dismissed flag"
    )

    # Dismissing strips the parameter, so the dismissal is not undone on reload.
    page.locator("#home-intro-close").click()
    assert intro.get_attribute("hidden") is not None
    assert page.evaluate("() => new URLSearchParams(location.search).has('intro')") is (
        False
    ), "the parameter must be stripped on dismissal"
    assert page_errors == [], f"JS errors: {page_errors}"


def test_tour_does_not_autostart_but_still_opens_from_the_roundel(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """A fresh homepage load never auto-starts the tour; the "?" roundel still does.

    SNOW-535: home.html sets data-map-help-no-autostart on #map-help-overlay,
    so map_help.js's first-load auto-start (localStorage key
    'snowdesk.map.help' absent) is suppressed on / regardless of whether
    #home-intro is showing or already dismissed.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home_with_sw_stripped(page, live_server.url)

    # Give any (incorrect) auto-start a moment to fire before asserting its
    # absence, rather than racing the assertion against page boot.
    page.wait_for_timeout(500)
    tour = page.locator("#map-help-overlay")
    assert tour.get_attribute("hidden") is not None, (
        "the tour must not auto-start on a fresh homepage load"
    )
    assert page.evaluate(f"() => localStorage.getItem({MAP_HELP_STORAGE_KEY!r})") is (
        None
    ), "auto-start must not have run (and so must not have persisted 'seen')"

    # The "?" roundel lives in the bottom-right stack, clear of #home-intro
    # (left-anchored) — no dismissal needed before clicking it.
    page.locator("#map-help-toggle").click()

    page.wait_for_selector("#map-help-overlay:not([hidden])", timeout=5_000)
    _assert_tour_on_first_step(page)
    assert page_errors == [], f"JS errors: {page_errors}"
