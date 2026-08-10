/*
 * tests/js/test_map_layers_menu_position.js — where the layers menu opens,
 * and how much of the viewport it is allowed to use (SNOW-511, SNOW-656).
 *
 * The menu is absolutely positioned against the basemap pill, which is the
 * FIRST roundel in a bottom-anchored column. That makes its CSS baseline a
 * function of the column's height: the taller the column, the higher the
 * pill, and the higher the menu's lower edge lands. On a 375x812 phone that
 * put the baseline mid-screen, squeezing 580px of content into a 333px
 * window — the menu opened already scrolled past its own Countries section
 * while 253px of map below it went unused.
 *
 * `positionMenu` therefore measures rather than declares BOTH edges: it
 * drops the baseline to just above the season scrubber, then caps the height
 * to the room that leaves above it (SNOW-511's original point, which stops
 * the first rows clipping behind the nav and the off-season banner).
 *
 * jsdom reports zero for every `getBoundingClientRect`, so the elements here
 * are given explicit rects. That is the whole mechanism under test — the two
 * numbers written onto `style` — and it is exactly the arithmetic that was
 * wrong, so stubbing the geometry is the point rather than a limitation.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** Give an element a fixed rect, the way a real layout would. */
function setRect(el, { top, bottom }) {
  el.getBoundingClientRect = () => ({
    top, bottom, height: bottom - top, left: 0, right: 375, width: 375, x: 0, y: top,
  });
}

/**
 * The geometry of a 375x812 phone: the nav and the off-season banner push
 * `#map` down to y=114, the scrubber owns the foot at y=707, and the pill
 * sits high because the roundel column below it is tall.
 */
function buildFixture({ pillBottom = 359, scrubber = true } = {}) {
  document.body.innerHTML = `
    <div id="map"></div>
    <div class="map-controls-br" id="map-controls-br">
      <div id="basemap-pill" data-state="collapsed">
        <button id="basemap-toggle" aria-expanded="false"></button>
        <ul id="basemap-menu" hidden>
          <li><button class="basemap-menu-item" data-basemap-key="a"></button></li>
        </ul>
      </div>
    </div>
    ${scrubber ? '<div id="season-scrubber"></div>' : ''}`;

  setRect(document.getElementById('map'), { top: 114, bottom: 812 });
  setRect(document.getElementById('basemap-pill'), { top: 319, bottom: pillBottom });
  if (scrubber) setRect(document.getElementById('season-scrubber'), { top: 707, bottom: 812 });
}

/** Load the picker fresh and open the menu through its own toggle. */
async function openMenu() {
  vi.resetModules();
  await import('../../static/js/map_state.js').catch(() => {});
  await import('../../static/js/map_basemap_picker.js');
  document.getElementById('basemap-toggle').click();
  return document.getElementById('basemap-menu');
}

beforeEach(() => {
  buildFixture();
  // The picker reads these bare identifiers from the shared classic-script
  // scope; under Vitest each file is a module, so they have to be globals.
  globalThis.BASEMAP_STORAGE_KEY = 'snowdesk.map.basemap';
  globalThis.MAP = null;
  globalThis.writeStorage = () => {};
  globalThis.OVERLAY_STORAGE_KEY = {};
  globalThis.resolveBasemapStyle = () => Promise.resolve({});
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('where the layers menu opens', () => {
  it('drops its baseline to just above the scrubber, not to the pill', async () => {
    const menu = await openMenu();

    // `bottom` is measured from the pill and is negative downward. The pill's
    // lower edge is at 359 and the floor is 707 - 8, so the menu has to hang
    // 340px below the pill to reach it. The CSS default of -96px would have
    // left it at y=455, mid-screen.
    expect(menu.style.bottom).toBe('-340px');
  });

  it('caps the height to the room that baseline leaves', async () => {
    const menu = await openMenu();

    // floor (699) - #map top (114) - the 8px top gap.
    expect(menu.style.maxHeight).toBe('577px');
  });

  it('follows the pill when the roundel column changes height', async () => {
    // A shorter column puts the pill lower; the baseline must not move with
    // it, because the floor is the scrubber, not the pill.
    buildFixture({ pillBottom: 500 });
    const menu = await openMenu();

    expect(menu.style.bottom).toBe('-199px');
    expect(menu.style.maxHeight).toBe('577px');
  });

  it('falls back to the map\'s own bottom edge when there is no scrubber', async () => {
    // The map partial is embedded without a scrubber on some surfaces; the
    // menu must still be placed rather than left at the CSS default.
    buildFixture({ scrubber: false });
    const menu = await openMenu();

    // map bottom (812) - 8 = 804 floor.
    expect(menu.style.bottom).toBe('-445px');
    expect(menu.style.maxHeight).toBe('682px');
  });
});

describe('the fixture itself', () => {
  it('reproduces the geometry the bug was measured in', async () => {
    // Guards the numbers above: if the fixture stops matching the phone that
    // was measured, the assertions stop meaning what they say.
    buildFixture();
    expect(document.getElementById('map').getBoundingClientRect().top).toBe(114);
    expect(document.getElementById('season-scrubber').getBoundingClientRect().top).toBe(707);
    expect(document.getElementById('basemap-pill').getBoundingClientRect().bottom).toBe(359);
  });
});
