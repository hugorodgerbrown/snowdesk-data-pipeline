"""
tests/e2e/test_map_help.py — Playwright tests for the #map-help-overlay
coachmark tour (SNOW-457).

Mirrors the pure-DOM / localStorage approach of ``tests/e2e/test_home_intro.py``
(no live tiles needed — map_help.js runs synchronously on DOMContentLoaded,
before any basemap fetch). Neither ``favourites`` nor ``field_observations``
waffle flags are enabled by default in these tests, so #favourite-add-btn and
#report-btn are absent from the DOM — this doubles as coverage for the
flag-gated "skip an absent step" behaviour: the active step count is always
strictly fewer than the 9 <li> definitions rendered in the template.

Tests drive the tour via #map-help-toggle (the "?" roundel) rather than
relying on auto-start for the interactive assertions, because auto-start is
itself conditional on #home-intro's state — the toggle path is deterministic
regardless of that overlay. A dedicated auto-start test covers the
#home-intro interaction explicitly.
"""

from __future__ import annotations

import re
from typing import cast

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

_STORAGE_KEY = "snowdesk.map.help"
_DISMISSED_VALUE = "seen"
_HOME_INTRO_KEY = "snowdesk.home.intro"
_HOME_INTRO_DISMISSED = "dismissed"


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for DOMContentLoaded."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")


def _overlay_is_hidden(page: Page) -> bool:
    """Return True when #map-help-overlay carries the ``hidden`` attribute."""
    return cast(
        bool,
        page.evaluate(
            "() => document.getElementById('map-help-overlay')?.hasAttribute('hidden') ?? true"
        ),
    )


def _get_storage_value(page: Page, key: str) -> str | None:
    """Return the localStorage value for ``key``, or None."""
    return cast(
        "str | None",
        page.evaluate(
            "(key) => { try { return localStorage.getItem(key); } catch(_) { return null; } }",
            key,
        ),
    )


def _set_storage_value(page: Page, key: str, value: str) -> None:
    """Set a localStorage key/value pair, tolerating storage errors."""
    page.evaluate(
        "([key, value]) => { try { localStorage.setItem(key, value); } catch(_) {} }",
        [key, value],
    )


def _clear_storage(page: Page, key: str) -> None:
    """Remove a localStorage key so tests start from a clean state."""
    page.evaluate(
        "(key) => { try { localStorage.removeItem(key); } catch(_) {} }",
        key,
    )


def _open_tour(page: Page) -> None:
    """Click the "?" roundel via a direct event dispatch.

    Dispatches the ``click`` event straight to the button rather than doing a
    physical mouse click, for the same reason ``test_home_intro.py`` does:
    the homepage is the full interactive map and in headless CI the canvas /
    site chrome can transiently intercept the click point while tiles
    settle. The unit under test here is map_help.js's toggle handler, not
    the browser's hit-testing.
    """
    page.dispatch_event("#map-help-toggle", "click")


def _click_next(page: Page) -> None:
    page.dispatch_event("#map-help-next", "click")


def _click_back(page: Page) -> None:
    page.dispatch_event("#map-help-back", "click")


def _click_skip(page: Page) -> None:
    page.dispatch_event("#map-help-skip", "click")


def _tooltip_title(page: Page) -> str:
    """Return the current step's title via textContent.

    ``evaluate`` (cast to str, as elsewhere in this module) rather than
    Playwright's own ``inner_text`` — the latter's return type is only
    resolvable as ``str`` when playwright's stubs are installed, which the
    ``tox -e mypy`` environment deliberately does not do.
    """
    return cast(
        "str",
        page.evaluate(
            "() => document.getElementById('map-help-tooltip-title')?.textContent ?? ''"
        ),
    )


def _step_count_text(page: Page) -> str:
    """Return the step counter's raw textContent.

    Uses ``textContent`` via ``evaluate`` rather than Playwright's
    ``inner_text`` (which returns CSS-rendered text — the counter is styled
    ``text-transform: uppercase``, so ``inner_text`` would read back
    "STEP 1 OF 6" and defeat a literal "Step 1" comparison).
    """
    return cast(
        str,
        page.evaluate(
            "() => document.getElementById('map-help-step-count')?.textContent ?? ''"
        ),
    )


def _current_step_and_total(page: Page) -> tuple[int, int]:
    """Parse "Step {n} of {total}" into (n, total)."""
    text = _step_count_text(page)
    match = re.search(r"(\d+)\D+(\d+)", text)
    assert match, f"unexpected step-count text: {text!r}"
    return int(match.group(1)), int(match.group(2))


def _present_help_targets(page: Page) -> int:
    """Count how many of the template's step targets actually exist in the DOM.

    #favourite-add-btn and #report-btn are always absent (both waffle flags
    are off by default in these tests); #season-ribbon is additionally
    conditional on ``ribbon`` context (only rendered when there is data to
    show), so the "expected active step count" is computed from the DOM
    rather than hard-coded.
    """
    return cast(
        int,
        page.evaluate(
            "() => Array.from(document.querySelectorAll("
            "'#map-help-steps li[data-help-target]'))"
            ".filter((li) => document.querySelector(li.getAttribute('data-help-target')))"
            ".length"
        ),
    )


def test_help_button_opens_tour_on_step_one(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Clicking the "?" roundel opens the overlay on step 1."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    _clear_storage(page, _STORAGE_KEY)

    # auto-start is suppressed because #home-intro is visible on a fresh
    # browser context, so this only asserts the toggle-driven open path
    # below, not map_help.js's auto-start guard (see
    # test_autostart_skipped_while_home_intro_is_showing for that).
    assert _overlay_is_hidden(page), "overlay should start hidden"

    _open_tour(page)
    page.wait_for_timeout(100)

    assert not _overlay_is_hidden(page), (
        "overlay should be visible after clicking #map-help-toggle"
    )
    step, _total = _current_step_and_total(page)
    assert step == 1, "tour should open on step 1"
    assert _tooltip_title(page) == "Find a region", (
        "step 1 should target the search control"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


def test_next_and_back_advance_and_retreat(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Next advances to step 2; Back returns to step 1."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    _clear_storage(page, _STORAGE_KEY)
    _open_tour(page)
    page.wait_for_timeout(100)

    first_title = _tooltip_title(page)

    _click_next(page)
    page.wait_for_timeout(100)
    second_title = _tooltip_title(page)

    assert second_title != first_title, "Next should advance to a different step"
    assert not _overlay_is_hidden(page), "overlay should still be open after Next"

    _click_back(page)
    page.wait_for_timeout(100)

    assert _tooltip_title(page) == first_title, "Back should return to step 1"
    assert page_errors == [], f"JS errors: {page_errors}"


def test_skip_dismisses_and_persists_to_storage(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Skip hides the overlay and writes the dismissed flag to localStorage."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    _clear_storage(page, _STORAGE_KEY)
    _open_tour(page)
    page.wait_for_timeout(100)

    _click_skip(page)
    page.wait_for_timeout(100)

    assert _overlay_is_hidden(page), "overlay should be hidden after Skip"
    assert _get_storage_value(page, _STORAGE_KEY) == _DISMISSED_VALUE, (
        f"localStorage['{_STORAGE_KEY}'] should be '{_DISMISSED_VALUE}' after Skip"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


def test_reload_with_dismissed_flag_does_not_autostart(
    live_server: LiveServer,
    page: Page,
) -> None:
    """A page reload after Skip does not re-open the overlay automatically."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    _clear_storage(page, _STORAGE_KEY)
    _open_tour(page)
    page.wait_for_timeout(100)
    _click_skip(page)
    page.wait_for_timeout(100)

    page.reload()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(100)

    assert _overlay_is_hidden(page), (
        "overlay should stay hidden on reload once the dismissed flag is set"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


def test_help_button_reopens_tour_after_dismissal(
    live_server: LiveServer,
    page: Page,
) -> None:
    """The "?" roundel re-opens the tour from step 1 even once dismissed."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    _clear_storage(page, _STORAGE_KEY)
    _open_tour(page)
    page.wait_for_timeout(100)
    _click_next(page)
    page.wait_for_timeout(100)
    _click_skip(page)
    page.wait_for_timeout(100)
    assert _overlay_is_hidden(page), "precondition: overlay hidden after Skip"

    _open_tour(page)
    page.wait_for_timeout(100)

    assert not _overlay_is_hidden(page), (
        "#map-help-toggle should re-open the tour regardless of stored state"
    )
    step, _total = _current_step_and_total(page)
    assert step == 1, "re-opening the tour should restart at step 1"
    assert page_errors == [], f"JS errors: {page_errors}"


def test_escape_closes_and_persists(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Pressing Escape closes the overlay and persists the dismissed flag."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    _clear_storage(page, _STORAGE_KEY)
    _open_tour(page)
    page.wait_for_timeout(100)

    page.keyboard.press("Escape")
    page.wait_for_timeout(100)

    assert _overlay_is_hidden(page), "Escape should close the overlay"
    assert _get_storage_value(page, _STORAGE_KEY) == _DISMISSED_VALUE, (
        f"localStorage['{_STORAGE_KEY}'] should be '{_DISMISSED_VALUE}' after Escape"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


def test_flag_gated_steps_are_skipped(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Steps whose target is absent from the DOM are excluded from the
    active sequence — the template always renders all 9 <li> step
    definitions, but #favourite-add-btn and #report-btn are always absent
    in these tests (both waffle flags are off by default), so the active
    step count should be strictly less than 9, and neither of their titles
    should ever appear while walking the sequence.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)

    # Confirm the flag-gated targets really are absent in this test's setup —
    # otherwise the "skip" assertion below would be vacuous.
    assert page.locator("#favourite-add-btn").count() == 0
    assert page.locator("#report-btn").count() == 0

    expected_active = _present_help_targets(page)
    assert expected_active < 9, (
        "sanity check: at least one step target should be absent"
    )

    _clear_storage(page, _STORAGE_KEY)
    _open_tour(page)
    page.wait_for_timeout(100)

    _step, total = _current_step_and_total(page)
    assert total == expected_active, (
        f"active step count ({total}) should match the DOM-present targets "
        f"({expected_active})"
    )

    titles: list[str] = []
    for _ in range(total):
        titles.append(_tooltip_title(page))
        _click_next(page)
        page.wait_for_timeout(50)

    assert "Add a favourite" not in titles
    assert "Report conditions" not in titles
    assert _overlay_is_hidden(page), (
        "clicking Next through every step should close the tour (Done)"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


def test_autostart_on_first_visit_when_home_intro_already_dismissed(
    live_server: LiveServer,
    page: Page,
) -> None:
    """The tour auto-starts on load when #home-intro is already dismissed.

    Pre-seeds the #home-intro dismissed flag (as if this were a returning
    visitor) so home_intro.js hides that overlay before map_help.js runs its
    own first-run check, then reloads with the map-help flag cleared.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    _set_storage_value(page, _HOME_INTRO_KEY, _HOME_INTRO_DISMISSED)
    _clear_storage(page, _STORAGE_KEY)

    page.reload()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(100)

    assert not _overlay_is_hidden(page), (
        "tour should auto-start when #home-intro is already dismissed"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


def test_autostart_skipped_while_home_intro_is_showing(
    live_server: LiveServer,
    page: Page,
) -> None:
    """The tour does not auto-start while #home-intro is still visible.

    On a genuinely first visit (both flags cleared), #home-intro renders
    open and map_help.js should defer — the identity card is dismissed
    first, and the tour stays reachable via the "?" roundel.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    _clear_storage(page, _HOME_INTRO_KEY)
    _clear_storage(page, _STORAGE_KEY)

    page.reload()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(100)

    assert _overlay_is_hidden(page), (
        "tour should not auto-start while #home-intro is still showing"
    )
    assert page_errors == [], f"JS errors: {page_errors}"
