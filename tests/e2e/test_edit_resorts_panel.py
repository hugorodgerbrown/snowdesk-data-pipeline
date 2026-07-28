"""
tests/e2e/test_edit_resorts_panel.py — Playwright tests for the staff
in-map resort editor at ``/?edit=resorts``.

Two behaviours, both new:

* **Placement uses the shared centre pin.** Selecting a resort arms
  ``window.PlacePicker`` — the same surface the favourite-create and
  field-observation flows use — so the pin holds still on screen and the
  map moves underneath it. The old draggable ``maplibregl.Marker`` is
  gone, and the panel's "Under pin" readout must track ``MAP.getCenter()``
  across a pan.
* **The hand-curated detail fields (SNOW-500) are editable in the panel.**
  A value typed into the panel's details section is posted with the
  coordinates by one Save and lands on the row.

Setup notes:

* The gate is a superuser session plus the ``edit_map`` waffle flag. The
  flag *is* seeded with ``superusers=True`` by regions migration 0002, but
  ``transactional_db`` (which ``live_server`` pulls in) flushes every table
  before each test — the migration-seeded row included — so the row is
  re-created here rather than assumed. Writing it to the DB is also what
  makes it visible to the live-server thread.
* ``navigator.serviceWorker`` is stripped before any page script runs
  (the ``test_report_sheet.py`` idiom): a real SW would intercept fetches
  the test cannot see and cache the shell between tests.
* Edit mode forces the swisstopo basemap (villages are legible on it),
  which is a live external style URL. It is stubbed with a minimal
  background-only style so the test does not depend on geo.admin.ch being
  reachable or fast; ``map.js`` re-installs its own layers on the swap
  either way.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from waffle.models import Flag

from regions.models import Resort
from tests.e2e.conftest import _session_login
from tests.factories import UserFactory

# Minimal valid MapLibre style standing in for the swisstopo basemap that
# edit mode switches to. Background-only: nothing here needs real tiles,
# and map.js re-adds every Snowdesk layer on 'styledata' regardless.
_STUB_STYLE = json.dumps(
    {
        "version": 8,
        "name": "stub",
        "glyphs": "http://localhost/static/fonts/{fontstack}/{range}.pbf",
        "sources": {},
        "layers": [
            {
                "id": "background",
                "type": "background",
                "paint": {"background-color": "#eef2f7"},
            }
        ],
    }
)

# The coordinate the panel says is under the pin, parsed back out of the
# readout's "Under pin" row. Asserting on the rendered text (rather than
# on internal state) is what ties the visible readout to the map.
_READOUT_UNDER_PIN = """
    () => {
        const dds = document.querySelectorAll('#edit-resorts-target dd');
        return dds.length > 1 ? dds[1].textContent.trim() : '';
    }
"""


def _stub_swisstopo(page: Page) -> None:
    """Serve a minimal style for the basemap edit mode forces on."""
    page.route(
        "**/vectortiles.geo.admin.ch/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=_STUB_STYLE,
        ),
    )


def _open_edit_mode(page: Page, live_server_url: str) -> None:
    """Load ``/?edit=resorts`` with the SW stripped and the panel hydrated."""
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', {get: () => undefined});"
    )
    _stub_swisstopo(page)
    page.goto(f"{live_server_url}/?edit=resorts")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )
    # The catalogue is fetched after boot; wait for a real row rather than
    # the "Loading…" placeholder.
    page.wait_for_selector("#edit-resorts-queue li[data-resort-id]")


def _ensure_details_open(page: Page) -> None:
    """Expand the details section, whatever state selection left it in.

    Selecting a resort that already carries metadata opens the section by
    itself, so an unconditional click on the summary would close it.
    """
    details = page.locator("#edit-resorts-details")
    if not details.evaluate("el => el.open"):
        page.locator("#edit-resorts-details summary").click()
    expect(details).to_have_attribute("open", "")


def _superuser_page(
    page: Page,
    live_server: LiveServer,
    django_db_blocker: Any,
) -> None:
    """Give ``page`` a session cookie for a superuser (the flag's audience)."""
    with django_db_blocker.unblock():
        Flag.objects.update_or_create(
            name="edit_map",
            defaults={"superusers": True},
        )
        user = UserFactory.create(is_superuser=True, is_staff=True)
    _session_login(page.context, live_server.url, user)


@pytest.mark.usefixtures("_load_test_data")
def test_selecting_a_resort_arms_the_centre_pin(
    page: Page,
    live_server: LiveServer,
    django_db_blocker: Any,
) -> None:
    """Selecting a row reveals the shared pin and reads the map centre.

    The pin is hidden until something is being placed; selecting a resort
    is what arms the picker, and the coordinate it reports is the map's
    centre — the point the pin is drawn over.
    """
    _superuser_page(page, live_server, django_db_blocker)
    _open_edit_mode(page, live_server.url)

    pin = page.locator("#map-place-pin")
    expect(pin).to_be_hidden()

    page.locator("#edit-resorts-queue li[data-resort-id]").first.click()
    expect(pin).to_be_visible()

    centre = page.evaluate(
        "() => { const c = MAP.getCenter(); return [c.lat, c.lng]; }"
    )
    expected = f"{centre[0]:.5f}, {centre[1]:.5f}"
    assert page.evaluate(_READOUT_UNDER_PIN) == expected


@pytest.mark.usefixtures("_load_test_data")
def test_panning_the_map_moves_the_coordinate_under_the_pin(
    page: Page,
    live_server: LiveServer,
    django_db_blocker: Any,
) -> None:
    """The map moves under a stationary pin, and the readout follows.

    This is the whole point of the change: no marker is dragged, so the
    pin's screen position is unchanged by the pan while the coordinate it
    reports is not.
    """
    _superuser_page(page, live_server, django_db_blocker)
    _open_edit_mode(page, live_server.url)
    page.locator("#edit-resorts-queue li[data-resort-id]").first.click()

    pin = page.locator("#map-place-pin")
    expect(pin).to_be_visible()
    before_box = pin.bounding_box()
    before_readout = page.evaluate(_READOUT_UNDER_PIN)

    page.evaluate("() => MAP.panBy([120, 90], {duration: 0})")
    page.wait_for_function(
        "(previous) => {"
        "  const dds = document.querySelectorAll('#edit-resorts-target dd');"
        "  return dds.length > 1 && dds[1].textContent.trim() !== previous;"
        "}",
        arg=before_readout,
    )

    after_box = pin.bounding_box()
    assert before_box is not None and after_box is not None
    assert (after_box["x"], after_box["y"]) == (before_box["x"], before_box["y"])

    centre = page.evaluate(
        "() => { const c = MAP.getCenter(); return [c.lat, c.lng]; }"
    )
    assert page.evaluate(_READOUT_UNDER_PIN) == f"{centre[0]:.5f}, {centre[1]:.5f}"


@pytest.mark.usefixtures("_load_test_data")
def test_saving_writes_the_detail_fields_with_the_coordinates(
    page: Page,
    live_server: LiveServer,
    django_db_blocker: Any,
) -> None:
    """One Save posts the panel's detail fields alongside the placed point."""
    _superuser_page(page, live_server, django_db_blocker)
    _open_edit_mode(page, live_server.url)

    row = page.locator("#edit-resorts-queue li[data-resort-id]").first
    resort_id = int(row.get_attribute("data-resort-id") or 0)
    row.click()

    _ensure_details_open(page)
    page.locator('[data-resort-field="num_lifts"]').fill("14")
    page.locator('[data-resort-field="operator_name"]').fill("Test Lifts AG")
    page.locator('[data-resort-field="website"]').fill("https://example.com/")

    centre = page.evaluate(
        "() => { const c = MAP.getCenter(); return [c.lat, c.lng]; }"
    )
    page.locator("#edit-resorts-save").click()

    # The readout's "Current" row catching up with the placed point is the
    # panel's own confirmation that the round trip completed.
    page.wait_for_function(
        "(expected) => {"
        "  const dds = document.querySelectorAll('#edit-resorts-target dd');"
        "  return dds.length > 0 && dds[0].textContent.trim() === expected;"
        "}",
        arg=f"{centre[0]:.5f}, {centre[1]:.5f}",
    )

    with django_db_blocker.unblock():
        resort = Resort.objects.get(pk=resort_id)
    assert resort.num_lifts == 14
    assert resort.operator_name == "Test Lifts AG"
    assert resort.website == "https://example.com/"
    assert resort.latitude == pytest.approx(centre[0], abs=1e-5)
    assert resort.longitude == pytest.approx(centre[1], abs=1e-5)


@pytest.mark.usefixtures("_load_test_data")
def test_existing_details_populate_the_panel(
    page: Page,
    live_server: LiveServer,
    django_db_blocker: Any,
) -> None:
    """A resort with stored metadata opens with the section filled in.

    The section is collapsed by default; a row that already has values
    opens it, so a populated record never looks empty.
    """
    _superuser_page(page, live_server, django_db_blocker)
    with django_db_blocker.unblock():
        resort = Resort.objects.order_by("region__region_id", "name").first()
        assert resort is not None
        resort.num_lifts = 21
        resort.operator_name = "Seeded Ops"
        resort.save(update_fields=["num_lifts", "operator_name", "updated_at"])

    _open_edit_mode(page, live_server.url)
    page.locator(f'#edit-resorts-queue li[data-resort-id="{resort.pk}"]').click()

    expect(page.locator("#edit-resorts-details")).to_have_attribute("open", "")
    expect(page.locator('[data-resort-field="num_lifts"]')).to_have_value("21")
    expect(page.locator('[data-resort-field="operator_name"]')).to_have_value(
        "Seeded Ops"
    )
