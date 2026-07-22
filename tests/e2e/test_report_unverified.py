"""
tests/e2e/test_report_unverified.py — Playwright test for SNOW-477: the map
"Report" affordance for an authenticated-but-unverified user.

Regression cover for the reported bug: tapping the Report icon did nothing and
logged a 403 in the console. The client marked any authenticated user eligible
(``report_eligible`` = authenticated only) while the server gate required a
verified ``Account`` (SNOW-430), so ``report.js`` fired the form-load GET, the
server 403'd it, and the ``htmx:responseError`` handler swallowed the error —
the sheet stalled on "Finding your location…".

The fix aligns ``report_eligible`` with the server gate and gives an
authenticated-but-unverified user a distinct sheet state. This test drives the
real client: an unverified user taps ``#report-btn`` and must see the
"verify your email" prompt, with no form-load request fired (so no 403) and the
sheet never stuck on the locating placeholder.

Uses the simulated-SW pattern (``navigator.serviceWorker`` stripped via the
shared ``_disable_real_sw`` fixture) — see ``docs/client-side-tests.md`` —
this test is about report.js's branching, not the SW lifecycle.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from tests.e2e.conftest import _session_login
from tests.factories import AccountFactory, UserFactory


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Load / and wait for the map + report button.

    Callers request the ``_disable_real_sw`` fixture so the strip is applied
    before this navigation — see
    ``tests/e2e/test_offline_observation_submit.py`` for the identical
    technique and rationale.
    """
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )
    page.wait_for_selector("#report-btn")


@override_flag("field_observations", active=True)
@pytest.mark.django_db(transaction=True)
def test_unverified_user_sees_verify_prompt_no_form_load(
    live_server: LiveServer,
    page: Page,
    django_db_blocker: Any,
    _disable_real_sw: None,
) -> None:
    """An unverified user taps Report → verify prompt, no form-load GET, no 403."""
    with django_db_blocker.unblock():
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=False)

    _session_login(page.context, live_server.url, user)
    _navigate_home(page, live_server.url)

    # The button must advertise the unverified state and not eligibility.
    assert page.get_attribute("#report-btn", "data-report-eligible") == "false"
    assert page.get_attribute("#report-btn", "data-report-unverified") == "true"

    # Record any request to the form endpoint — the unverified branch must not
    # fire it (that GET is what the server 403'd in the bug).
    form_requests: list[str] = []
    page.on(
        "request",
        lambda req: (
            form_requests.append(req.url)
            if "report/form" in req.url or "report_form" in req.url
            else None
        ),
    )
    # Capture only the failure signature from the bug report — a 403 in the
    # console. Asserting zero console output would be brittle (the map/tiles
    # can log unrelated messages); the reported symptom is specifically a 403.
    forbidden_errors: list[str] = []
    page.on(
        "console",
        lambda msg: (
            forbidden_errors.append(msg.text)
            if msg.type == "error" and "403" in msg.text
            else None
        ),
    )

    page.click("#report-btn")

    # The verify-your-email prompt renders; the sheet is not stuck on the
    # locating placeholder and never fetched the form.
    page.wait_for_selector('#report-sheet:has-text("Verify your email")')
    assert page.locator('#report-sheet:has-text("Finding your location")').count() == 0
    assert page.locator("#report-sheet #report-form").count() == 0

    # Give any stray async request a beat to appear, then assert none did — no
    # form-load GET means no 403 (the bug fired the GET and swallowed the 403).
    page.wait_for_timeout(200)
    assert form_requests == []
    assert forbidden_errors == []

    # SNOW-486: the verify prompt must carry the shared × dismiss control like
    # every other sheet state (it previously rendered headerless, leaving the
    # overlay with no close mechanic). Clicking it dismisses the sheet.
    close = page.locator('#report-sheet [data-action="dismiss"]')
    assert close.count() == 1
    close.click()
    page.wait_for_selector("#report-sheet[hidden]", state="attached")
