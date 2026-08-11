/*
 * tests/js/test_map_layers_menu_position.js — where the layers menu opens,
 * and how much of the viewport it is allowed to use (SNOW-511, SNOW-656,
 * SNOW-664).
 *
 * The menu is absolutely positioned inside the bottom-right roundel column.
 * SNOW-511 capped its height so its top rows could not slide behind the nav
 * and the conditional off-season banner; SNOW-656 then dropped its lower edge
 * to just above the season scrubber, because the CSS baseline it inherited
 * left a third of the map unused.
 *
 * SNOW-664 changed the box those offsets are measured from. The menu used to
 * be a child of #basemap-pill; that pill moved into #map-controls-collapsible,
 * which is `overflow: hidden` for its height animation and would clip a menu
 * opening leftward out of it, so the menu is a child of #map-controls-br now.
 * The floor and the cap are unchanged — the arithmetic simply starts from the
 * stack's own lower edge, which (unlike the pill's) does not move when the
 * column changes height. That last property is asserted below: it is the
 * SNOW-656 bug's root cause, gone rather than compensated for.
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
 * `#map` down to y=114, the scrubber owns the foot at y=707, and the roundel
 * column is bottom-anchored 60px above the viewport foot, so its lower edge
 * sits at y=752 whatever height the collapsible group is standing at.
 *
 * `stackTop` is what varies with that height — the column grows upward.
 */
function buildFixture({ stackTop = 319, scrubber = true } = {}) {
  document.body.innerHTML = `
    <div id="map"></div>
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
      <div id="map-controls-collapsible">
        <div class="map-controls-collapsible-inner">
          <div id="basemap-pill" data-state="collapsed">
            <button id="basemap-toggle" aria-expanded="false"></button>
          </div>
        </div>
      </div>
      <ul id="basemap-menu" hidden>
        <li><button class="basemap-menu-item" data-basemap-key="a"></button></li>
      </ul>
    </div>
    ${scrubber ? '<div id="season-scrubber"></div>' : ''}`;

  setRect(document.getElementById('map'), { top: 114, bottom: 812 });
  setRect(document.getElementById('map-controls-br'), { top: stackTop, bottom: 752 });
  if (scrubber) setRect(document.getElementById('season-scrubber'), { top: 707, bottom: 812 });
}

/** Load the picker fresh and open the menu through its own toggle. */
async function openMenu() {
  vi.resetModules();
  await import('../../static/js/map_state.js').catch(() => {});
  // SNOW-658: the floor/cap arithmetic itself now lives in
  // map_overlay_bounds.js (window.pwaOverlayBounds), shared with the three
  // UGC sheets, which need the same answer in their own coordinate base.
  // home.html loads it ahead of the map bundle; here it is imported
  // explicitly, the way every other suite loads a module the file under test
  // reads off `window`.
  await import('../../static/js/map_overlay_bounds.js');
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
  it('drops its baseline to just above the scrubber', async () => {
    const menu = await openMenu();

    // `bottom` is measured from the stack. Its lower edge is at 752 and the
    // floor is 707 - 8, so the menu's own lower edge sits 53px above it.
    expect(menu.style.bottom).toBe('53px');
  });

  it('caps the height to the room that baseline leaves', async () => {
    const menu = await openMenu();

    // floor (699) - #map top (114) - the 8px top gap.
    expect(menu.style.maxHeight).toBe('577px');
  });

  it('holds the baseline still when the roundel column changes height', async () => {
    // SNOW-664: the column is bottom-anchored, so opening or collapsing the
    // group moves only its TOP edge. Measuring from the stack rather than
    // from the pill is what makes the baseline independent of that — the
    // pill-relative version had to chase it, and lost (SNOW-656).
    buildFixture({ stackTop: 500 });
    const menu = await openMenu();

    expect(menu.style.bottom).toBe('53px');
    expect(menu.style.maxHeight).toBe('577px');
  });

  it('falls back to the map\'s own bottom edge when there is no scrubber', async () => {
    // The map partial is embedded without a scrubber on some surfaces; the
    // menu must still be placed rather than left at the CSS default.
    buildFixture({ scrubber: false });
    const menu = await openMenu();

    // map bottom (812) - 8 = 804 floor, which is BELOW the stack's own foot,
    // so the offset goes negative and the menu hangs past it.
    expect(menu.style.bottom).toBe('-52px');
    expect(menu.style.maxHeight).toBe('682px');
  });
});

describe('the menu is not clipped by the collapsible group', () => {
  it('is a child of the stack, not of the pill inside the group', async () => {
    // SNOW-664. #map-controls-collapsible is `overflow: hidden` for its
    // height animation, and that clips both axes — a menu opening leftward
    // from inside it would be cut off with no other symptom.
    const menu = await openMenu();

    expect(menu.parentElement.id).toBe('map-controls-br');
    expect(document.getElementById('map-controls-collapsible').contains(menu)).toBe(false);
  });

  it('closes when the collapsible strip hides the roundel it belongs to', async () => {
    const menu = await openMenu();
    expect(menu.hidden).toBe(false);

    // The strip writes only `data-expanded`; the picker observes it, because
    // a menu left open beside a hidden roundel is an orphan.
    document.getElementById('map-controls-br').dataset.expanded = 'false';
    await Promise.resolve();

    expect(menu.hidden).toBe(true);
  });

  it('stays open when a click lands on the menu\'s own chrome', async () => {
    // The menu used to be a descendant of #basemap-pill, so the outside-click
    // guard's `pill.contains` covered it. It is a sibling now, and this menu
    // is mostly not clickable items — a tap on a section heading, a separator
    // or the padding must not read as "outside".
    const menu = await openMenu();

    menu.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(menu.hidden).toBe(false);
  });
});

describe('the fixture itself', () => {
  it('reproduces the geometry the bug was measured in', async () => {
    // Guards the numbers above: if the fixture stops matching the phone that
    // was measured, the assertions stop meaning what they say.
    buildFixture();
    expect(document.getElementById('map').getBoundingClientRect().top).toBe(114);
    expect(document.getElementById('season-scrubber').getBoundingClientRect().top).toBe(707);
    expect(document.getElementById('map-controls-br').getBoundingClientRect().bottom).toBe(752);
  });
});
