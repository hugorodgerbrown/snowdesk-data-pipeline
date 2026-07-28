"""
tests/e2e/test_edit_resorts_panel.py — Playwright tests for the staff
in-map resort editor at ``/?edit=resorts``.

Covers:

* **The hand-curated detail fields (SNOW-500) are editable in the panel.**
  A value typed into the panel's details section is posted with the
  coordinates by one Save and lands on the row.
* **Save says what it is doing.** The button reports the in-flight state
  and a confirmation line reports the outcome.
* **Placement stays on the draggable marker.** This mode deliberately does
  *not* use the shared centre pin (``window.PlacePicker``) the favourite
  and observation flows position with — it is a mouse-driven desktop tool,
  where a geo-anchored marker beats a screen-anchored one because it stays
  locked to its coordinate while you zoom in to check it. There is a test
  here pinning that down, so a future "unify the placement surfaces" pass
  has to argue with it rather than silently regress the tool.

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

# The coordinate the panel shows for the draft pin, read out of the
# readout's "Draft" row. Asserting on the rendered text (rather than on
# internal state) is what ties the visible readout to the marker.
_READOUT_DRAFT = """
    () => {
        const dds = document.querySelectorAll('#edit-resorts-target dd');
        return dds.length > 1 ? dds[1].textContent.trim() : '';
    }
"""


# Holds the save POST open for a beat so its in-flight state is observable
# at all. Installed as an init script (before any page script runs), not a
# page.route handler — see the test that uses it.
_DELAY_SAVE_SCRIPT = """
    (() => {
        const realFetch = window.fetch;
        window.fetch = (url, opts) => {
            const isSave = String(url).includes('/save/')
                && opts && opts.method === 'POST';
            if (!isSave) return realFetch(url, opts);
            return new Promise((resolve) => {
                setTimeout(() => resolve(realFetch(url, opts)), 1200);
            });
        };
    })();
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
def test_placement_uses_a_draggable_marker_not_the_shared_centre_pin(
    page: Page,
    live_server: LiveServer,
    django_db_blocker: Any,
) -> None:
    """Selecting a placed resort drops a marker; the centre pin stays away.

    Edit-resorts is a mouse-driven desktop tool and positions with a
    geo-anchored ``maplibregl.Marker``, not the screen-anchored centre pin
    (``#map-place-pin``) the favourite and observation flows use. Asserting
    the centre pin is absent is what stops a later "unify the placement
    surfaces" change from quietly swapping the mechanic here.
    """
    _superuser_page(page, live_server, django_db_blocker)
    _open_edit_mode(page, live_server.url)

    page.locator("#edit-resorts-queue li[data-resort-id]").first.click()

    expect(page.locator(".maplibregl-marker")).to_have_count(1)
    expect(page.locator("#map-place-pin")).to_be_hidden()


@pytest.mark.usefixtures("_load_test_data")
def test_the_draft_pin_holds_its_coordinate_across_a_zoom(
    page: Page,
    live_server: LiveServer,
    django_db_blocker: Any,
) -> None:
    """Zooming leaves the placed coordinate exactly where it was.

    This is the property a marker buys over a screen-fixed pin: it is
    anchored to the ground, so zooming in to check a placement inspects
    that placement rather than silently re-picking a different point.
    """
    _superuser_page(page, live_server, django_db_blocker)
    _open_edit_mode(page, live_server.url)
    page.locator("#edit-resorts-queue li[data-resort-id]").first.click()
    expect(page.locator(".maplibregl-marker")).to_have_count(1)

    before = page.evaluate(_READOUT_DRAFT)
    assert before not in ("", "—")

    # Zoom about a point well off to one side — the case that used to drag
    # the chosen coordinate along with it.
    page.evaluate(
        "() => MAP.zoomTo(MAP.getZoom() + 2, {around: MAP.unproject([40, 40]),"
        " duration: 0})"
    )
    page.wait_for_function("() => !MAP.isMoving()")

    assert page.evaluate(_READOUT_DRAFT) == before


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

    # What gets saved is the marker's coordinate, which the panel already
    # renders — reading it from there avoids racing the flyTo the selection
    # kicked off, which is still animating the map centre. Assert the marker
    # first so a missing draft fails here, naming the cause, rather than as
    # a bare timeout on the post-save wait below.
    expect(page.locator(".maplibregl-marker")).to_have_count(1)
    draft = page.evaluate(_READOUT_DRAFT)
    assert draft not in ("", "—")
    page.locator("#edit-resorts-save").click()

    # The readout's "Current" row catching up with the draft is the panel's
    # own confirmation that the round trip completed.
    page.wait_for_function(
        "(expected) => {"
        "  const dds = document.querySelectorAll('#edit-resorts-target dd');"
        "  return dds.length > 0 && dds[0].textContent.trim() === expected;"
        "}",
        arg=draft,
    )

    lat, lon = (float(part) for part in draft.split(","))
    with django_db_blocker.unblock():
        resort = Resort.objects.get(pk=resort_id)
    assert resort.num_lifts == 14
    assert resort.operator_name == "Test Lifts AG"
    assert resort.website == "https://example.com/"
    assert resort.latitude == pytest.approx(lat, abs=1e-5)
    assert resort.longitude == pytest.approx(lon, abs=1e-5)


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


@pytest.mark.usefixtures("_load_test_data")
def test_save_reports_that_it_is_saving_and_that_it_saved(
    page: Page,
    live_server: LiveServer,
    django_db_blocker: Any,
) -> None:
    """The Save button says what it is doing, and confirms when it is done.

    Without this the only visible effect of a save is the readout's
    "Current" row quietly catching up, which is indistinguishable from a
    click that did nothing. The save is held open for a beat so the
    in-flight state is observable at all.
    """
    _superuser_page(page, live_server, django_db_blocker)
    # Hold the save open browser-side rather than in a page.route handler:
    # a sync route handler blocks the same thread expect() polls on, so the
    # in-flight state would come and go while the assertions were frozen.
    page.add_init_script(_DELAY_SAVE_SCRIPT)
    _open_edit_mode(page, live_server.url)

    page.locator("#edit-resorts-queue li[data-resort-id]").first.click()
    save = page.locator("#edit-resorts-save")
    save.click()

    expect(save).to_have_text("Saving…")
    expect(save).to_be_disabled()

    status = page.locator("#edit-resorts-status")
    expect(status).to_be_visible()
    expect(status).to_contain_text("Saved")
    # The label reverts, but the button stays disabled: a completed save
    # consumes the draft marker, so there is nothing left to save until a
    # new pin is placed.
    expect(save).to_have_text("Save")
    expect(save).to_be_disabled()
    expect(page.locator(".maplibregl-marker")).to_have_count(0)
