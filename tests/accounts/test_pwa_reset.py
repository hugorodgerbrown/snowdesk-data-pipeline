"""
Server-side tests for the SNOW-378 "Reset local data" escape hatch.

The JS half is exercised by Playwright in a later ticket; this file
verifies the manage page carries the trigger, the script is loaded on
the same page, and the copy is present.
"""

from __future__ import annotations

import pytest
from django.test import Client

from tests.factories import AccountFactory


@pytest.mark.django_db
def test_manage_page_has_reset_trigger() -> None:
    """The manage page ships the ``data-pwa-reset-trigger`` button."""
    account = AccountFactory.create()
    client = Client()
    client.force_login(account.user)

    response = client.get("/account/manage/")
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "data-pwa-reset-trigger" in body
    assert "Reset local data on this device" in body


@pytest.mark.django_db
def test_manage_page_loads_pwa_reset_script() -> None:
    """The manage page loads ``pwa_reset.js`` alongside its passkey script."""
    account = AccountFactory.create()
    client = Client()
    client.force_login(account.user)

    response = client.get("/account/manage/")
    body = response.content.decode("utf-8")

    assert "pwa_reset.js" in body


@pytest.mark.django_db
def test_manage_page_has_reset_helper_copy() -> None:
    """The helper line explains what is and is not affected."""
    account = AccountFactory.create()
    client = Client()
    client.force_login(account.user)

    response = client.get("/account/manage/")
    body = response.content.decode("utf-8")

    # Copy is spread across template line breaks + blocktrans whitespace
    # normalisation; collapse before asserting.
    collapsed = " ".join(body.split())
    assert "Clears cached bulletins" in collapsed
    assert "Your subscription is not affected." in collapsed
