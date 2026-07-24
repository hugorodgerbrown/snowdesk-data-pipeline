"""
tests/e2e/test_sw_version_page.py — SNOW-517 real-SW test for the staff
/_sw-version/ page.

Uses the real-SW ``pwa_page`` fixture (not the simulated/stripped
pattern used elsewhere in this directory) because the assertion IS about
the SW lifecycle itself: that ``static/js/pwa_sw_version_probe.js`` can
post ``'version'`` to the active controller and write the reply into the
page (see ``docs/client-side-tests.md``'s "SW-lifecycle tests: real vs
simulated" section for why this fixture family is the right choice here).
Layers a staff session on top via ``_session_login`` (the same helper
``test_report_sheet.py`` / ``signed_in_page`` use) since ``pwa_page``
itself carries no authentication.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.sw_shell import read_cache_version
from tests.e2e.conftest import PwaPage, _session_login
from tests.factories import UserFactory


@pytest.mark.django_db(transaction=True)
def test_staff_sees_live_sw_version_populated_by_probe(
    pwa_page: PwaPage, django_db_blocker: Any
) -> None:
    """The live-version element fills in from the SW's `'version'` reply.

    `pwa_page` already registered and activated the real SW against `/`
    (and reloaded once, so the SW is genuinely controlling the page — see
    that fixture's docstring). Navigating on to `/_sw-version/` under the
    same origin/scope keeps the SW in control of the new page, so the
    probe script's `postMessage('version')` reaches it immediately.
    """
    with django_db_blocker.unblock():
        staff_user = UserFactory.create()

    _session_login(pwa_page.page.context, pwa_page.live_server_url, staff_user)

    page = pwa_page.page
    page.goto(f"{pwa_page.live_server_url}/_sw-version/")
    page.wait_for_load_state("load")

    # Baseline (server-rendered) values are present immediately.
    expected_version = read_cache_version()
    assert expected_version in page.content()

    # The probe fills in the live value from the SW's 'version' reply.
    page.wait_for_function(
        """(expected) => {
            const el = document.querySelector('[data-testid="sw-live-version"]');
            return !!el && el.textContent.trim() === expected;
          }""",
        arg=expected_version,
        timeout=5000,
    )
