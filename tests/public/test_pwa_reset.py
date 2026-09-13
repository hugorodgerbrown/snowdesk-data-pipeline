"""
Server-side tests for the SNOW-378 "Reset local data" escape hatch.

The JS half is exercised by Playwright in a later ticket; this file
verifies the page carries the trigger, the script is loaded on the same
page, and the copy is present.

SNOW-667 moved the control from /account/manage/ to /account/settings/.
SNOW-930 moved it again, to the public ``/offline/`` — and moved this
module out of ``tests/accounts/`` with it. The reset touches no cookies
and no account row; it clears service workers, Cache Storage, IndexedDB
and both Web Storage areas, all of which are facts about this browser. It
was behind a login only because settings was the one page available to
put it on, which meant the escape hatch was unreachable to a reader whose
session had expired. That settings no longer renders it is asserted in
tests/public/test_offline_page.py, so no copy can be left behind.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_offline_page_has_reset_trigger() -> None:
    """``/offline/`` ships the ``data-pwa-reset-trigger`` button, to anyone."""
    response = Client().get(reverse("public:offline_page"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "data-pwa-reset-trigger" in body
    # SNOW-746: the row's heading carries the name and the button reads
    # "Reset" — the row already says what is being reset.
    assert "Reset local data" in body


@pytest.mark.django_db
def test_offline_page_loads_pwa_reset_script() -> None:
    """``pwa_reset.js`` reaches the page — it comes from base.html, on every page."""
    response = Client().get(reverse("public:offline_page"))
    body = response.content.decode("utf-8")

    assert "pwa_reset.js" in body


@pytest.mark.django_db
def test_offline_page_has_reset_helper_copy() -> None:
    """The helper line explains what is and is not affected.

    SNOW-860 shortened the first half of it — "cached bulletins, offline
    data, and saved preferences" was the whole disclosure for a wipe that
    also takes every downloaded map and every unsent change, and the
    breakdown panel now enumerates all four. What survives here is the part
    the panel cannot say: what this does NOT touch.

    "Does not log you out" is the load-bearing half: ``resetLocalData``
    clears service workers, Cache Storage, IndexedDB and both Web Storage
    areas and touches no cookies, so the session survives. A user reaching
    for this on a borrowed device needs to know it is not a sign-out — the
    sign-out control moved to the Account group precisely so the two are
    not read as the same thing.
    """
    response = Client().get(reverse("public:offline_page"))
    body = response.content.decode("utf-8")

    # Copy is spread across template line breaks + blocktrans whitespace
    # normalisation; collapse before asserting.
    collapsed = " ".join(body.split())
    assert "Delete locally cached app data. Does not log you out." in collapsed


@pytest.mark.django_db
def test_offline_page_discloses_shared_map_data() -> None:
    """The row discloses the shared overview map, in the breakdown panel.

    The z0-9 overview map is the app's own map data — fetched once per
    basemap, shared by every downloaded area, never chosen and never
    removable on its own — so the downloads panel neither lists it nor
    charges its budget for it (SNOW-867). That leaves this row as the only
    place it is disclosed, and the only control that clears it. Storage the
    user cannot see is storage they cannot consent to clearing.

    SNOW-867 disclosed it as a one-line "includes N MB of shared map data"
    paragraph. SNOW-860 supersedes that with the four-category breakdown,
    where the overview map is a NAMED, SIZED row in the Downloaded maps
    category beside everything else on the device — strictly more than the
    line it replaces. The figure is still client-side (nothing here is
    server-knowable), so what the page must ship is the panel, its strings
    and the module that paints them.
    """
    response = Client().get(reverse("public:offline_page"))
    body = response.content.decode("utf-8")

    assert 'data-testid="reset-data-summary-panel"' in body
    assert 'id="reset-data-summary-list"' in body
    assert 'data-string="base-layer-name"' in body
    assert "reset_data_summary.js" in body
    # The reader is shared with the map's Manage downloads sheet — one
    # reader, so the two surfaces cannot disagree about what is stored.
    assert "basemap_downloaded_areas.js" in body
    # And the superseded markup is gone, not left behind to paint nothing.
    assert "data-pwa-reset-size" not in body
