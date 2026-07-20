"""
tests/e2e/test_observations_page.py — Playwright test for the SNOW-476
/observations/ page.

Signs in via ``_session_login`` (the same session-cookie shortcut
``test_community_reports.py`` and the PWA lifecycle tests use), seeds one
``FieldObservation`` owned by that user, and asserts the page renders an
``observation-row`` for it.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from tests.e2e.conftest import _session_login
from tests.factories import FieldObservationFactory, UserFactory


@override_flag("observations_page", active=True)
@pytest.mark.django_db(transaction=True)
def test_own_observation_renders_as_a_row(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """A signed-in user's own report shows up as an observation-row."""
    with django_db_blocker.unblock():
        user = UserFactory.create()
        FieldObservationFactory.create(user=user)

    _session_login(page.context, live_server.url, user)
    page.goto(f"{live_server.url}/observations/")
    page.wait_for_load_state("domcontentloaded")

    page.wait_for_selector('[data-testid="observation-row"]')
    assert page.locator('[data-testid="observation-row"]').count() == 1
