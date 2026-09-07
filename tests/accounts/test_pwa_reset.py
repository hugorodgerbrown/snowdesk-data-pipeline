"""
Server-side tests for the SNOW-378 "Reset local data" escape hatch.

The JS half is exercised by Playwright in a later ticket; this file
verifies the settings page carries the trigger, the script is loaded on
the same page, and the copy is present.

SNOW-667 moved the control from /account/manage/ to /account/settings/.
"""

from __future__ import annotations

import pytest
from django.test import Client

from tests.factories import AccountFactory


@pytest.mark.django_db
def test_settings_page_has_reset_trigger() -> None:
    """The settings page ships the ``data-pwa-reset-trigger`` button."""
    account = AccountFactory.create()
    client = Client()
    client.force_login(account.user)

    response = client.get("/account/settings/")
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "data-pwa-reset-trigger" in body
    # SNOW-746: the row's heading carries the name and the button reads
    # "Reset" — the row already says what is being reset.
    assert "Reset local data" in body


@pytest.mark.django_db
def test_settings_page_loads_pwa_reset_script() -> None:
    """The settings page loads ``pwa_reset.js`` alongside its passkey script."""
    account = AccountFactory.create()
    client = Client()
    client.force_login(account.user)

    response = client.get("/account/settings/")
    body = response.content.decode("utf-8")

    assert "pwa_reset.js" in body


@pytest.mark.django_db
def test_settings_page_has_reset_helper_copy() -> None:
    """The helper line explains what is and is not affected."""
    account = AccountFactory.create()
    client = Client()
    client.force_login(account.user)

    response = client.get("/account/settings/")
    body = response.content.decode("utf-8")

    # Copy is spread across template line breaks + blocktrans whitespace
    # normalisation; collapse before asserting.
    collapsed = " ".join(body.split())
    assert "Clears cached bulletins" in collapsed
    assert "Your subscription is not affected." in collapsed


@pytest.mark.django_db
def test_settings_page_discloses_shared_map_data() -> None:
    """The row carries the shared-map-data line pwa_reset.js fills.

    The z0-9 overview map is the app's own map data — fetched once per
    basemap, shared by every downloaded area, never chosen and never
    removable on its own — so the downloads panel neither lists it nor
    charges its budget for it. That leaves this row as the only place it is
    disclosed, and the only control that clears it. Storage the user cannot
    see is storage they cannot consent to clearing.

    The sentence is server-rendered (so ``makemessages`` sees it) with the
    numeral left to JS; it ships ``hidden`` and is revealed only when there
    is a figure to show.
    """
    account = AccountFactory.create()
    client = Client()
    client.force_login(account.user)

    response = client.get("/account/settings/")
    body = response.content.decode("utf-8")

    assert "data-pwa-reset-size" in body
    assert "data-pwa-reset-size-value" in body
    assert "of shared map data" in body
