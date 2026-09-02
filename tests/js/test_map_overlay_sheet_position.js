/*
 * tests/js/test_map_overlay_sheet_position.js — where a UGC sheet opens on
 * desktop, and what it must not overlap (SNOW-658).
 *
 * The three sheets (#map-downloads-sheet, #favourite-sheet, #report-sheet)
 * are `position: fixed` and includes/_overlay_sheet.html can only put them in
 * a viewport corner (`sm:bottom-4 sm:right-4`). That corner knows nothing
 * about the bottom row along the foot of the map or the roundel column
 * at its right edge, so a sheet opened over both.
 *
 * static/js/map_overlay_bounds.js answers the same question the layers menu
 * asks (tests/js/test_map_layers_menu_position.js pins that side), plus the
 * horizontal half the menu never needed — the menu opens from INSIDE the
 * roundel column, while a sheet has to clear it.
 *
 * jsdom reports zero for every `getBoundingClientRect`, so the elements here
 * are given explicit rects, exactly as the layers-menu suite does. The
 * arithmetic is the whole mechanism under test, so stubbing the geometry is
 * the point rather than a limitation.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

/** Give an element a fixed rect, the way a real layout would. */
function setRect(el, { top, bottom, left, right }) {
  el.getBoundingClientRect = () => ({
    top,
    bottom,
    left,
    right,
    height: bottom - top,
    width: right - left,
    x: left,
    y: top,
  });
}

/**
 * Pin the viewport size and the `sm` breakpoint answer that follows from it.
 *
 * jsdom's `innerWidth`/`innerHeight` are settable but its `matchMedia` always
 * reports `matches: false`, so the media query is answered from the width
 * here rather than left to report the mobile shape for every case.
 *
 * @param {number} width
 * @param {number} height
 */
function setViewport(width, height) {
  window.innerWidth = width;
  window.innerHeight = height;
  window.matchMedia = (query) => ({
    matches: width >= 640,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

/**
 * A 1024x768 desktop: the nav puts `#map` at y=64, the bottom-left stack's
 * last line owns the foot at y=680, and the bottom-right roundel column's
 * left edge is at x=944.
 *
 * SNOW-794: the floor is `.map-date-row`, not `#season-scrubber`. The
 * scrubber is a member of that row rather than a bar of its own now, and it
 * is absent entirely off-season and below 640px — the row is what is always
 * present and always lowest. The `bottomRow` flag stands in for "this page
 * has no map chrome at the foot at all".
 */
function buildFixture({ bottomRow = true, controls = true, width = 1024 } = {}) {
  setViewport(width, 768);
  document.body.innerHTML = `
    <div id="map"></div>
    ${controls ? '<div class="map-controls-br" id="map-controls-br"></div>' : ''}
    ${bottomRow ? '<div id="map-date-row"></div>' : ''}
    <div id="favourite-sheet" hidden></div>`;

  setRect(document.getElementById('map'), {
    top: 64, bottom: 768, left: 0, right: width,
  });
  if (controls) {
    setRect(document.getElementById('map-controls-br'), {
      top: 400, bottom: 680, left: 944, right: 1008,
    });
  }
  if (bottomRow) {
    setRect(document.getElementById('map-date-row'), {
      top: 680, bottom: 712, left: 0, right: width,
    });
  }
  return document.getElementById('favourite-sheet');
}

/** Load the module fresh and place the sheet through its public entry point. */
async function positionSheet(sheet) {
  await import('../../static/js/map_overlay_bounds.js');
  window.pwaOverlayBounds.positionSheet(sheet);
  return sheet;
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('where a UGC sheet opens on desktop', () => {
  beforeEach(() => {
    buildFixture();
  });

  it('stops above the bottom-left stack last line', async () => {
    const sheet = await positionSheet(document.getElementById('favourite-sheet'));

    // `bottom` is measured from the viewport, the sheet being fixed: the
    // floor is the bottom row's top (680) less the 8px gap, so 768 - 672.
    expect(sheet.style.bottom).toBe('96px');
  });

  it('caps the height to the room between that floor and the map', async () => {
    const sheet = await positionSheet(document.getElementById('favourite-sheet'));

    // floor (672) - #map top (64) - the 8px top gap.
    expect(sheet.style.maxHeight).toBe('600px');
  });

  it('clears the bottom-right roundel column', async () => {
    const sheet = await positionSheet(document.getElementById('favourite-sheet'));

    // viewport (1024) - the column's left edge (944) + the 8px gap.
    expect(sheet.style.right).toBe('88px');
  });
});

describe('what it falls back to', () => {
  it("uses the map's own bottom edge when there is no bottom row", async () => {
    const sheet = buildFixture({ bottomRow: false });
    await positionSheet(sheet);

    // map bottom (768) - 8 = 760 floor, so the sheet's own bottom is 8.
    expect(sheet.style.bottom).toBe('8px');
    expect(sheet.style.maxHeight).toBe('688px');
  });

  it('leaves the CSS right edge alone when the column has no box', async () => {
    // No column on the page at all — and a `display: none` ancestor yields
    // the same zero-width rect, which is why the guard is on the box rather
    // than on the element existing.
    const sheet = buildFixture({ controls: false });
    await positionSheet(sheet);

    expect(sheet.style.right).toBe('');
    // The vertical half is unaffected — the two measurements are independent.
    expect(sheet.style.bottom).toBe('96px');
  });
});

describe('below the sm breakpoint', () => {
  it('writes nothing — the bottom dock is the right shape there', async () => {
    const sheet = buildFixture({ width: 375 });
    await positionSheet(sheet);

    expect(sheet.style.bottom).toBe('');
    expect(sheet.style.right).toBe('');
    expect(sheet.style.maxHeight).toBe('');
  });

  it('clears a desktop geometry when the viewport shrinks past it', async () => {
    const sheet = buildFixture();
    await positionSheet(sheet);
    expect(sheet.style.right).toBe('88px');

    // Inline styles beat the CSS, so a stranded desktop value would pin a
    // phone-width sheet to a geometry it must not have.
    setViewport(375, 768);
    window.dispatchEvent(new Event('resize'));

    expect(sheet.style.bottom).toBe('');
    expect(sheet.style.right).toBe('');
    expect(sheet.style.maxHeight).toBe('');
  });

  it('re-places a tracked sheet when the viewport grows again', async () => {
    const sheet = buildFixture({ width: 375 });
    await positionSheet(sheet);
    expect(sheet.style.right).toBe('');

    setViewport(1024, 768);
    window.dispatchEvent(new Event('resize'));

    expect(sheet.style.right).toBe('88px');
    expect(sheet.style.bottom).toBe('96px');
  });
});

describe('the fixture itself', () => {
  it('reproduces the desktop geometry the assertions are stated in', () => {
    buildFixture();
    expect(document.getElementById('map').getBoundingClientRect().top).toBe(64);
    expect(document.getElementById('map-date-row').getBoundingClientRect().top)
      .toBe(680);
    expect(document.getElementById('map-controls-br').getBoundingClientRect().left)
      .toBe(944);
  });
});
