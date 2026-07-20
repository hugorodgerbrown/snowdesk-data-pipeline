"""
tests/e2e/test_report_sheet.py — Playwright tests for SNOW-474: the report
map sheet's persistent close (×) control and Esc dismissal.

Setup mirrors ``test_offline_observation_submit.py`` (simulated SW —
``navigator.serviceWorker`` stripped — plus ``_session_login`` and a
granted/stubbed geolocation fix so report.js's real GPS-path form load
runs) but is otherwise unrelated to the offline mutation-queue flow: these
tests only exercise the sheet's open/close chrome, added because the
report-form state previously had no close control at all.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from tests.e2e.conftest import _session_login
from tests.factories import AccountFactory, UserFactory


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """Load / with navigator.serviceWorker stripped, wait for the map to load.

    Stripping serviceWorker (before any page script runs) makes
    sw_register.js bail out immediately — see
    ``test_offline_observation_submit.py``'s identical helper.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


@override_flag("field_observations", active=True)
@pytest.mark.django_db(transaction=True)
def test_report_form_close_button_hides_sheet(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """The persistent × in the report form's header closes the sheet."""
    with django_db_blocker.unblock():
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=True)

    _session_login(page.context, live_server.url, user)
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 46.10, "longitude": 7.10, "accuracy": 10})

    _navigate_home_with_sw_stripped(page, live_server.url)

    page.click("#report-btn")
    page.wait_for_selector("#report-form")

    # The Cancel button also carries data-action="close-report-sheet" —
    # filter on the × glyph to target the header control specifically.
    close_btn = page.locator(
        '#report-sheet [data-action="close-report-sheet"]', has_text="×"
    )
    assert close_btn.count() == 1
    close_btn.click()

    page.wait_for_selector("#report-sheet[hidden]", state="attached")


@override_flag("field_observations", active=True)
def test_anonymous_signin_cta_has_close_button(
    live_server: LiveServer, page: Page
) -> None:
    """The anonymous sign-in CTA state also carries the persistent × (SNOW-474)."""
    _navigate_home_with_sw_stripped(page, live_server.url)

    page.click("#report-btn")
    page.wait_for_selector("#report-sheet:not([hidden])")

    close_btn = page.locator(
        '#report-sheet [data-action="close-report-sheet"]', has_text="×"
    )
    assert close_btn.count() == 1
    close_btn.click()

    page.wait_for_selector("#report-sheet[hidden]", state="attached")


@override_flag("field_observations", active=True)
@pytest.mark.django_db(transaction=True)
def test_escape_key_closes_report_sheet(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Esc dismisses the open report sheet."""
    with django_db_blocker.unblock():
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=True)

    _session_login(page.context, live_server.url, user)
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 46.10, "longitude": 7.10, "accuracy": 10})

    _navigate_home_with_sw_stripped(page, live_server.url)

    page.click("#report-btn")
    page.wait_for_selector("#report-form")

    page.keyboard.press("Escape")
    page.wait_for_selector("#report-sheet[hidden]", state="attached")
