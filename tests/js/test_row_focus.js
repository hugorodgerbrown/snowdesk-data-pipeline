/*
 * tests/js/test_row_focus.js — the shared "frame this row's place" click.
 *
 * static/js/row_focus.js is the behaviour behind a UGC panel row's NAME on
 * the three panels whose rows are places (routes, favourites, field
 * observations): turn the overlay on, close the panel, move the camera.
 * The sibling of test_row_rename_commit.js, and covering the same class of
 * thing — one module, three callers, so a regression here is a regression on
 * all three at once.
 *
 * What is worth asserting, and why:
 *
 *   1. The ORDER. Overlay before close before camera. Flying to a route with
 *      its layer off lands on an empty map, and moving the camera behind a
 *      panel that covers it (every phone) is invisible.
 *   2. The overlay is only switched on when it is off. show() on an overlay
 *      the user already enabled is a no-op in map.js, but calling it anyway
 *      would mean this module could not tell the two states apart — and the
 *      panel switch reads isEnabled() for exactly that reason.
 *   3. Two ordinates fly, four fit. That is the whole point of one attribute
 *      carrying both shapes.
 *   4. A malformed attribute moves NOTHING and still returns true. Returning
 *      false would send the click on to the caller's next test — the add CTA
 *      — which is a different action on the same sheet.
 *   5. (SNOW-835) A row that names a BASEMAP switches to it first, and the
 *      camera waits for `snowdesk:basemap-changed` before it moves. This is
 *      the half that cannot be seen by eye: a `fitBounds` issued while
 *      `setStyle` is still tearing the layers down is discarded silently, so
 *      "it works when I try it" and "it races" look identical. The other
 *      half is the inertness — three of the four panels pass no basemap
 *      attribute at all, and a regression there breaks framing everywhere.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/row_focus.js';

/** A sheet holding one row whose name carries `target`. */
function buildSheet(target) {
  document.body.innerHTML = `
    <div id="sheet">
      <ul>
        <li>
          <button data-row-label data-row-focus="${target}">Lac de Vaux</button>
          <button data-row-rename data-route-rename="r-1">Rename</button>
        </li>
      </ul>
      <button data-panel-add>Add a route</button>
    </div>`;
  return document.getElementById('sheet');
}

/**
 * A sheet whose row names a REGION rather than a coordinate (SNOW-811) —
 * the downloads panel's region rows, which carry no bbox because the
 * polygon is already on the map.
 */
function buildRegionSheet(regionId) {
  document.body.innerHTML = `
    <div id="sheet">
      <ul>
        <li>
          <button data-row-label data-row-focus-region="${regionId}">Binntal</button>
        </li>
      </ul>
      <button data-panel-add>Download a custom area</button>
    </div>`;
  return document.getElementById('sheet');
}

/**
 * A sheet whose row names a basemap as well as a place (SNOW-835) — the
 * downloads panel's rows, stamped on the clone by
 * map_downloads_manager.js's `applyRowFocus`.
 *
 * @param {?string} key The basemap key, or null for a row that carries
 *   none (a pre-SNOW-645 record, or an account-only row).
 */
function buildDownloadSheet(key) {
  document.body.innerHTML = `
    <div id="sheet">
      <ul>
        <li>
          <button data-row-label data-row-focus="7.1,46.0,7.3,46.2"
                  ${key === null ? '' : `data-row-focus-basemap="${key}"`}>Lauterbrunnen</button>
        </li>
      </ul>
    </div>`;
  return document.getElementById('sheet');
}

/**
 * The basemap picker, as _map_embed.html renders it: one
 * `.basemap-menu-item` per basemap, `aria-checked` on the active one.
 *
 * A hand-copy of the real markup down to the class, because that class is
 * what row_focus.js selects on — the picker binds its own click handler to
 * `.basemap-menu-item`, so a `[data-basemap-key]` element without it would
 * be a row with nothing behind the press.
 *
 * @param {string} activeKey The key the map is currently showing.
 * @returns {Object<string, HTMLElement>} Each row by key, so a test can
 *   spy on the one it expects to be pressed.
 */
function buildPicker(activeKey) {
  const menu = document.createElement('ul');
  menu.id = 'basemap-menu';
  const rows = {};
  for (const key of ['openfreemap_liberty', 'swisstopo_winter']) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'basemap-menu-item hover-affordance';
    item.dataset.basemapKey = key;
    item.dataset.basemapUrl = `/static/basemaps/${key}.json`;
    item.setAttribute('role', 'menuitemradio');
    item.setAttribute('aria-checked', key === activeKey ? 'true' : 'false');
    menu.appendChild(item);
    rows[key] = item;
  }
  document.body.appendChild(menu);
  return rows;
}

/** The event map.js dispatches once the new style's layers are back. */
function settleBasemap() {
  document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));
}

/** A stub overlay bridge in the shape map.js publishes. */
function stubOverlay(enabled) {
  return {
    enabled: enabled,
    show: vi.fn(function () { this.enabled = true; }),
    isEnabled() { return this.enabled; },
  };
}

/** Dispatch a real click on `selector` and return what the module said. */
function clickAndHandle(sheet, selector, options) {
  let answer = null;
  sheet.addEventListener('click', (event) => {
    answer = window.pwaRowFocus.handleClick(event, options);
  }, { once: true });
  sheet.querySelector(selector).click();
  return answer;
}

describe('window.pwaRowFocus.parse', () => {
  it('reads a point and a bbox', () => {
    expect(window.pwaRowFocus.parse('7.5,46.1')).toEqual([7.5, 46.1]);
    expect(window.pwaRowFocus.parse('7.1,46.0,7.3,46.2')).toEqual([7.1, 46.0, 7.3, 46.2]);
  });

  it('rejects any other ordinate count', () => {
    // A bbox missing an ordinate frames nothing, and guessing which one is
    // absent would fly the map somewhere arbitrary.
    expect(window.pwaRowFocus.parse('7.5')).toBeNull();
    expect(window.pwaRowFocus.parse('7.1,46.0,7.3')).toBeNull();
    expect(window.pwaRowFocus.parse('')).toBeNull();
    expect(window.pwaRowFocus.parse(null)).toBeNull();
  });

  it('rejects an empty ordinate rather than reading it as the equator', () => {
    // Number('') is 0 — the one coercion that turns a malformed attribute
    // into a plausible-looking coordinate off the Gulf of Guinea.
    expect(window.pwaRowFocus.parse('7.5,')).toBeNull();
    expect(window.pwaRowFocus.parse('north,46.1')).toBeNull();
  });
});

describe('window.pwaRowFocus.handleClick', () => {
  let focus;

  beforeEach(() => {
    focus = { point: vi.fn(), bounds: vi.fn(), region: vi.fn() };
    window.pwaMapFocus = focus;
  });

  it('leaves a click that is not on a row name alone', () => {
    const sheet = buildSheet('7.5,46.1');
    const overlay = stubOverlay(true);
    const close = vi.fn();

    const answer = clickAndHandle(sheet, '[data-panel-add]', { overlay, close });

    expect(answer).toBe(false);
    expect(close).not.toHaveBeenCalled();
    expect(focus.point).not.toHaveBeenCalled();
  });

  it('flies to a two-ordinate target, having closed the panel', () => {
    const sheet = buildSheet('7.5,46.1');
    const overlay = stubOverlay(true);
    const close = vi.fn();

    const answer = clickAndHandle(sheet, '[data-row-focus]', { overlay, close });

    expect(answer).toBe(true);
    expect(close).toHaveBeenCalledOnce();
    expect(focus.point).toHaveBeenCalledWith(7.5, 46.1);
    expect(focus.bounds).not.toHaveBeenCalled();
  });

  it('fits a four-ordinate target instead', () => {
    const sheet = buildSheet('7.1,46.0,7.3,46.2');

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });

    expect(focus.bounds).toHaveBeenCalledWith([7.1, 46.0, 7.3, 46.2]);
    expect(focus.point).not.toHaveBeenCalled();
  });

  it('switches a disabled overlay on before the camera moves', () => {
    // Otherwise the flight lands on a map with nothing drawn on it, which
    // reads as a broken button rather than as a hidden layer.
    const sheet = buildSheet('7.5,46.1');
    const overlay = stubOverlay(false);

    clickAndHandle(sheet, '[data-row-focus]', { overlay, close: vi.fn() });

    expect(overlay.show).toHaveBeenCalledOnce();
    expect(focus.point).toHaveBeenCalledOnce();
  });

  it('leaves an overlay the user already enabled alone', () => {
    const sheet = buildSheet('7.5,46.1');
    const overlay = stubOverlay(true);

    clickAndHandle(sheet, '[data-row-focus]', { overlay, close: vi.fn() });

    expect(overlay.show).not.toHaveBeenCalled();
  });

  it('claims a malformed target without moving the map', () => {
    const sheet = buildSheet('7.5,');
    const overlay = stubOverlay(false);
    const close = vi.fn();

    const answer = clickAndHandle(sheet, '[data-row-focus]', { overlay, close });

    expect(answer).toBe(true);
    expect(overlay.show).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    expect(focus.point).not.toHaveBeenCalled();
    expect(focus.bounds).not.toHaveBeenCalled();
  });

  it('resolves a region row through pwaMapFocus.region', () => {
    // SNOW-811: a region download is recorded against its id and carries no
    // bbox on purpose — the polygon is already on the map, and a stored box
    // would be a second, coarser copy of it.
    const sheet = buildRegionSheet('CH-4242');
    const overlay = stubOverlay(false);
    const close = vi.fn();

    const answer = clickAndHandle(sheet, '[data-row-focus-region]', {
      overlay,
      close,
    });

    expect(answer).toBe(true);
    expect(overlay.show).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
    expect(focus.region).toHaveBeenCalledWith('CH-4242');
    expect(focus.bounds).not.toHaveBeenCalled();
    expect(focus.point).not.toHaveBeenCalled();
  });

  it('claims a region row an older map bundle cannot resolve', () => {
    // window.pwaMapFocus is published by map.js, which a cached shell can
    // still be serving from before region() existed. The press is claimed —
    // it WAS a focus — and moves nothing, the same answer a malformed
    // coordinate gets.
    delete focus.region;
    const sheet = buildRegionSheet('CH-4242');

    const answer = clickAndHandle(sheet, '[data-row-focus-region]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });

    expect(answer).toBe(true);
    expect(focus.bounds).not.toHaveBeenCalled();
  });

  it('claims a row carrying neither form without moving the map', () => {
    // What buildRow leaves on an orphaned download bucket: no record, so no
    // region to name and no box to frame.
    document.body.innerHTML = `
      <div id="sheet"><ul><li>
        <button data-row-label data-row-focus="">basemap-pinned-abc123</button>
      </li></ul></div>`;
    const sheet = document.getElementById('sheet');
    const close = vi.fn();

    const answer = clickAndHandle(sheet, '[data-row-label]', {
      overlay: stubOverlay(false),
      close,
    });

    expect(answer).toBe(true);
    expect(close).not.toHaveBeenCalled();
    expect(focus.region).not.toHaveBeenCalled();
    expect(focus.bounds).not.toHaveBeenCalled();
  });

  it('closes the panel even with no map bundle to fly', () => {
    // The panels are reachable on a page where map.js never booted. Closing
    // is still the right answer to the press — leaving the sheet open over a
    // map that did not move would look like nothing happened at all.
    delete window.pwaMapFocus;
    const sheet = buildSheet('7.5,46.1');
    const close = vi.fn();

    const answer = clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close,
    });

    expect(answer).toBe(true);
    expect(close).toHaveBeenCalledOnce();
  });
});

describe('a row that names its basemap (SNOW-835)', () => {
  // The downloaded-squares overlay filters to the ACTIVE basemap's tile
  // template, so framing a Swisstopo area while the map is on OpenFreeMap
  // arrives at ground with nothing drawn on it. The row takes its basemap
  // with it — by pressing the picker's own row, never by reimplementing
  // the swap, so the preference is persisted and the popover's checked
  // row moves with it.
  //
  // Every fixture here builds the SHEET first and the picker second:
  // buildDownloadSheet writes `document.body.innerHTML`, which would
  // otherwise throw the picker away.
  let focus;

  beforeEach(() => {
    focus = { point: vi.fn(), bounds: vi.fn(), region: vi.fn() };
    window.pwaMapFocus = focus;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('presses the matching picker row and waits for the style to settle', () => {
    const sheet = buildDownloadSheet('swisstopo_winter');
    const picker = buildPicker('openfreemap_liberty');
    const clicked = vi.fn();
    picker.swisstopo_winter.addEventListener('click', clicked);

    const answer = clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });

    expect(answer).toBe(true);
    expect(clicked).toHaveBeenCalledOnce();
    // The whole point of the ticket: a fitBounds issued now, while
    // setStyle is still tearing the layers off the map, is discarded
    // without an error — the row would switch the basemap and go nowhere.
    expect(focus.bounds).not.toHaveBeenCalled();

    settleBasemap();

    expect(focus.bounds).toHaveBeenCalledWith([7.1, 46.0, 7.3, 46.2]);
  });

  it('never presses a row other than the one the area names', () => {
    const sheet = buildDownloadSheet('swisstopo_winter');
    const picker = buildPicker('openfreemap_liberty');
    const wrong = vi.fn();
    picker.openfreemap_liberty.addEventListener('click', wrong);

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });
    settleBasemap();

    expect(wrong).not.toHaveBeenCalled();
  });

  it('frames one basemap-changed only, not every later swap', () => {
    // The listener is torn down when it fires. Left on `document`, it
    // would frame this row again the next time the user changed basemap
    // by hand — minutes later, from the picker, having pressed nothing.
    const sheet = buildDownloadSheet('swisstopo_winter');
    buildPicker('openfreemap_liberty');

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });
    settleBasemap();
    settleBasemap();

    expect(focus.bounds).toHaveBeenCalledOnce();
  });

  it('frames straight away when that basemap is already active', () => {
    // The common case for every row in the group the map is showing: no
    // setStyle, no flicker, and no frame of latency added to a press that
    // needed none.
    const sheet = buildDownloadSheet('openfreemap_liberty');
    const picker = buildPicker('openfreemap_liberty');
    const clicked = vi.fn();
    picker.openfreemap_liberty.addEventListener('click', clicked);

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });

    expect(focus.bounds).toHaveBeenCalledWith([7.1, 46.0, 7.3, 46.2]);
    expect(clicked).not.toHaveBeenCalled();
  });

  it('frames a keyless row without touching the picker', () => {
    // Two rows reach this shape: a download recorded before SNOW-645 (no
    // basemap key at all), and an account-only one (SNOW-749), whose key
    // is a fact about a device the reader is not holding — so
    // map_downloads_manager.js withholds the attribute rather than
    // switching this device's persisted preference for tiles that are not
    // here either way. This is also the state EVERY routes, favourites and
    // observations row is in.
    const sheet = buildDownloadSheet(null);
    const picker = buildPicker('openfreemap_liberty');
    const clicked = vi.fn();
    picker.swisstopo_winter.addEventListener('click', clicked);

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });

    expect(focus.bounds).toHaveBeenCalledOnce();
    expect(clicked).not.toHaveBeenCalled();
  });

  it('frames a row whose basemap the picker no longer offers', () => {
    // A record naming a retired key (`swisstopo_light`) has no row to
    // press. Framing anyway is the same answer as before this ticket, and
    // the alternative — holding the camera for a swap that can never
    // happen — is the one thing worse than not switching.
    const sheet = buildDownloadSheet('swisstopo_light');
    buildPicker('openfreemap_liberty');

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });

    expect(focus.bounds).toHaveBeenCalledOnce();
  });

  it('frames without switching on a page that has no picker', () => {
    // /account/ and the three server-rendered panels: no #basemap-menu in
    // the document at all, and nothing here may assume there is one.
    const sheet = buildDownloadSheet('swisstopo_winter');

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });

    expect(focus.bounds).toHaveBeenCalledOnce();
  });

  it('frames straight away when the picker row is disabled offline', () => {
    // map_layer_sync_status.js disables a basemap row whose style is not
    // cached while the app is offline, and the picker's own handler
    // returns on that attribute. Pressing it would do nothing and then
    // hold the camera for the whole settle timeout.
    const sheet = buildDownloadSheet('swisstopo_winter');
    const picker = buildPicker('openfreemap_liberty');
    picker.swisstopo_winter.setAttribute('aria-disabled', 'true');
    const clicked = vi.fn();
    picker.swisstopo_winter.addEventListener('click', clicked);

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });

    expect(focus.bounds).toHaveBeenCalledOnce();
    expect(clicked).not.toHaveBeenCalled();
  });

  it('frames anyway when the style never settles', () => {
    // A style that fails to load still leaves a map worth moving, and a
    // press that silently swallows the camera move is the failure the
    // wait would otherwise introduce.
    vi.useFakeTimers();
    const sheet = buildDownloadSheet('swisstopo_winter');
    buildPicker('openfreemap_liberty');

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });
    expect(focus.bounds).not.toHaveBeenCalled();

    vi.advanceTimersByTime(4000);

    expect(focus.bounds).toHaveBeenCalledWith([7.1, 46.0, 7.3, 46.2]);

    // And the event arriving late — a slow style that did load after all —
    // does not frame a second time.
    settleBasemap();
    expect(focus.bounds).toHaveBeenCalledOnce();
  });

  it('switches after the panel has closed, not before', () => {
    // The sheet covers the map below `sm`, so it goes first and the new
    // style loads behind an already-dismissed panel.
    const sheet = buildDownloadSheet('swisstopo_winter');
    const picker = buildPicker('openfreemap_liberty');
    const order = [];
    picker.swisstopo_winter.addEventListener('click', () => order.push('switch'));

    clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(() => order.push('close')),
    });
    settleBasemap();
    order.push('frame');

    expect(order).toEqual(['close', 'switch', 'frame']);
  });

  it('switches nothing when there is no map bundle to frame with', () => {
    // Persisting a new basemap preference with no camera move to justify
    // it would be a side effect the press never asked for.
    delete window.pwaMapFocus;
    const sheet = buildDownloadSheet('swisstopo_winter');
    const picker = buildPicker('openfreemap_liberty');
    const clicked = vi.fn();
    picker.swisstopo_winter.addEventListener('click', clicked);

    const answer = clickAndHandle(sheet, '[data-row-focus]', {
      overlay: stubOverlay(true),
      close: vi.fn(),
    });

    expect(answer).toBe(true);
    expect(clicked).not.toHaveBeenCalled();
  });
});
