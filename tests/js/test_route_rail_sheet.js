/*
 * tests/js/test_route_rail_sheet.js — the route rail as the phone's bottom
 * sheet (static/js/route_rail.js, SNOW-1099).
 *
 * The rail binds window.MapSheet's detents on its first open, opens them
 * with the route and resets them on close. A point placed while the sheet
 * peeks raises it to half. The FULL detent is the terrain data (SNOW-1146;
 * a staff debug readout until then): the terrain rows load the first time
 * the data is revealed for a route, once, and fill it for the segment
 * under the point. A press on the point header's terrain slope line
 * reveals it — FULL on a phone, opened in the card from sm up. The leader
 * line stops on the sheet's top edge rather than the panel's foot.
 *
 * map_sheet.js's own binding (heights, drags, the fit) is covered in
 * test_map_sheet_detents.js; a fake stands in for it here, so this file
 * holds the rail to the controller's contract and nothing more. A file of
 * its own because the rail reads its markup once at load, and this markup
 * carries the terrain hooks test_route_rail.js's does not.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/route_cursor_core.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/slope_classes_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/route_rail_core.js';

const UUID = '11111111-2222-3333-4444-555555555555';
const NIL = '00000000-0000-0000-0000-000000000000';

document.body.innerHTML = `
  <div id="map">
    <section id="route-rail" data-route-rail hidden
             data-detents="peek,half,full" data-detent-initial="peek"
             data-route-terrain-url-template="/routes/${NIL}/terrain.json">
      <div data-row-renameable data-sheet-drag>
        <h2 id="route-rail-eyebrow">Route</h2>
        <p data-route-rail-title><span data-row-label data-route-rail-name></span></p>
        <button type="button" data-route-rail-close></button>
        <p data-route-rail-meta></p>
      </div>
      <div>
        <svg data-route-rail-lane></svg>
        <div data-route-rail-readout hidden>
          <span data-route-rail-dot></span>
          <span data-route-rail-elevation></span>
          <span data-route-rail-distance></span>
        </div>
        <div data-route-rail-ticks></div>
      </div>
      <div data-sheet-peek>
        <p data-route-rail-hint>Tap or drag the profile to view terrain data</p>
      </div>
      <div data-sheet-half>
        <div data-route-point-card-slope>
          <dt>Terrain slope</dt>
          <dd><button type="button" data-route-terrain-toggle aria-expanded="false">
            <span data-route-point-card-ground></span>
          </button></dd>
        </div>
      </div>
      <dl data-route-terrain>
        <div data-route-terrain-hint>Place a point on the profile to read its figures.</div>
        <dd data-route-terrain-field="sample">–</dd>
        <dd data-route-terrain-field="km">–</dd>
        <dd data-route-terrain-field="heights">–</dd>
        <dd data-route-terrain-field="aspect">–</dd>
        <dd data-route-terrain-field="angle">–</dd>
        <dd data-route-terrain-field="bearing">–</dd>
        <dd data-route-terrain-field="track">–</dd>
        <dd data-route-terrain-field="fall">–</dd>
      </dl>
    </section>
  </div>
`;

window.ResizeObserver = class {
  observe() {}
  disconnect() {}
};

/**
 * A stand-in for map_sheet.js's detent controller: it keeps the detent on
 * `data-detent`, as the real one does, and records what the rail asks of it.
 */
const calls = [];
const bindDetents = vi.fn((el, opts) => {
  calls.push(['bind', opts]);
  return {
    open: () => {
      calls.push(['open']);
      el.setAttribute('data-detent', 'peek');
    },
    reset: () => {
      calls.push(['reset']);
      el.removeAttribute('data-detent');
    },
    set: (name) => {
      calls.push(['set', name]);
      el.setAttribute('data-detent', name);
    },
    current: () => el.getAttribute('data-detent'),
  };
});
window.MapSheet = { bindDetents };

await import('../../static/js/route_rail.js');

const rail = document.getElementById('route-rail');

/** @returns {object} A 24-segment routes-GeoJSON feature. */
function feature(uuid = UUID) {
  const coordinates = Array.from({ length: 81 }, (_, i) => [7.4 + i / 10000, 46.1, 1500 + i]);
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates },
    properties: {
      uuid,
      name: 'Mont Fort',
      distance_m: 620,
      slope: { points: [], angles: new Array(24).fill(20) },
      legs: [{ i: 1, from: 0, to: 23, climbing: true }],
    },
  };
}

/**
 * One terrain row, as routes:terrain serves it.
 *
 * @param {number} i
 * @param {object} [overrides]
 */
function row(i, overrides = {}) {
  return {
    i,
    from_m: i * 25,
    length_m: 25,
    ele_from_m: 2822,
    ele_to_m: 2829.2,
    angle_deg: 20.1,
    aspect_deg: 308,
    bearing_deg: 108,
    track_gradient_deg: 14.5,
    track_gradient_rejected: false,
    track_gradient_from: Math.max(0, i - 2),
    track_gradient_to: i + 2,
    roll_deg: -7.1,
    ...overrides,
  };
}

/** @param {string} name @returns {string} The readout field's text. */
const field = (name) => rail.querySelector(`[data-route-terrain-field="${name}"]`).textContent;

/** Let the fetch's promise chain and the detent observer run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

let fetchMock;

beforeEach(() => {
  calls.length = 0;
  const rows = Array.from({ length: 24 }, (_, i) => row(i));
  fetchMock = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ rows }) }));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  window.pwaRouteRail.close();
  rail.removeAttribute('data-detent');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('the detents', () => {
  it('binds once, opens with the route and resets on close', () => {
    window.pwaRouteRail.open(feature());
    window.pwaRouteRail.close();
    window.pwaRouteRail.open(feature());
    expect(bindDetents).toHaveBeenCalledTimes(1);
    expect(calls.map((c) => c[0]).filter((c) => c !== 'bind')).toEqual(['open', 'reset', 'open']);
    expect(rail.getAttribute('data-detent')).toBe('peek');
  });

  it('closes the route when the sheet is dragged away', () => {
    window.pwaRouteRail.open(feature());
    bindDetents.mock.calls[0][1].onClose();
    expect(window.pwaRouteRail.isOpen()).toBe(false);
  });

  it('raises a peeking sheet to half when a point is placed', () => {
    window.pwaRouteRail.open(feature());
    window.pwaRouteRail.cursor().setIndex(5);
    expect(rail.getAttribute('data-detent')).toBe('half');
  });

  it('hides the hint while a point is placed, and brings it back on clear', () => {
    const hint = /** @type {HTMLElement} */ (rail.querySelector('[data-route-rail-hint]'));
    window.pwaRouteRail.open(feature());
    expect(hint.hidden).toBe(false);
    window.pwaRouteRail.cursor().setIndex(5);
    expect(hint.hidden).toBe(true);
    window.pwaRouteRail.cursor().setIndex(null);
    expect(hint.hidden).toBe(false);
  });

  it('leaves a sheet at full where it is when a point moves', () => {
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'full');
    window.pwaRouteRail.cursor().setIndex(5);
    expect(rail.getAttribute('data-detent')).toBe('full');
  });
});

describe('the terrain data', () => {
  it('loads nothing until the sheet reaches full', async () => {
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'half');
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('loads the route’s terrain rows once, the first time it reaches full', async () => {
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'full');
    await settle();
    rail.setAttribute('data-detent', 'half');
    rail.setAttribute('data-detent', 'full');
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(`/routes/${UUID}/terrain.json`);
  });

  it('loads again for the next route', async () => {
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'full');
    await settle();
    window.pwaRouteRail.close();
    const other = '99999999-2222-3333-4444-555555555555';
    window.pwaRouteRail.open(feature(other));
    rail.setAttribute('data-detent', 'full');
    await settle();
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      `/routes/${UUID}/terrain.json`,
      `/routes/${other}/terrain.json`,
    ]);
  });

  it('shows the hint and dashes with no point placed', async () => {
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'full');
    await settle();
    expect(rail.querySelector('[data-route-terrain-hint]').hidden).toBe(false);
    expect(field('angle')).toBe('–');
  });

  it('fills every field for the segment under the point', async () => {
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'full');
    await settle();
    window.pwaRouteRail.cursor().setIndex(5);
    expect(rail.querySelector('[data-route-terrain-hint]').hidden).toBe(true);
    expect(field('sample')).toBe('5 of 24');
    expect(field('km')).toBe('0.13 → 0.15 km');
    expect(field('heights')).toBe('2822 → 2829.2 m');
    expect(field('aspect')).toBe('NW · 308°');
    expect(field('angle')).toBe('20.1°');
    expect(field('bearing')).toBe('E · 108°');
    expect(field('track')).toBe('14.5°');
    expect(field('fall')).toBe('160°');
  });

  it('reads a rejected track gradient as a dash', async () => {
    const rows = Array.from({ length: 24 }, (_, i) => row(i));
    rows[5] = row(5, { track_gradient_deg: null, track_gradient_rejected: true });
    fetchMock.mockImplementation(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ rows }) }));
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'full');
    await settle();
    window.pwaRouteRail.cursor().setIndex(5);
    expect(field('track')).toBe('–');
  });

  it('keeps dashes when the rows fail to load, and retries at the next full', async () => {
    fetchMock.mockImplementationOnce(() => Promise.reject(new Error('offline')));
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'full');
    await settle();
    window.pwaRouteRail.cursor().setIndex(5);
    expect(field('angle')).toBe('–');
    rail.setAttribute('data-detent', 'half');
    rail.setAttribute('data-detent', 'full');
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(field('angle')).toBe('20.1°');
  });

  it('puts the scroll back at the top on leaving full', async () => {
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'full');
    await settle();
    rail.scrollTop = 120;
    rail.setAttribute('data-detent', 'half');
    await settle();
    expect(rail.scrollTop).toBe(0);
  });
});

describe('the terrain slope line reveals the data', () => {
  const toggle = () => rail.querySelector('[data-route-terrain-toggle]');
  const line = () => rail.querySelector('[data-route-point-card-slope]');

  it('raises a phone sheet to full, and lowers it to half', async () => {
    window.pwaRouteRail.open(feature());
    toggle().click();
    await settle();
    expect(rail.getAttribute('data-detent')).toBe('full');
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    toggle().click();
    await settle();
    expect(rail.getAttribute('data-detent')).toBe('half');
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
  });

  it('takes a press on the line’s label too', () => {
    window.pwaRouteRail.open(feature());
    line().querySelector('dt').click();
    expect(rail.getAttribute('data-detent')).toBe('full');
  });

  it('follows a drag to full', async () => {
    window.pwaRouteRail.open(feature());
    rail.setAttribute('data-detent', 'full');
    await settle();
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
  });

  it('opens the data in the card at a desktop width', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    window.pwaRouteRail.open(feature());
    toggle().click();
    expect(rail.hasAttribute('data-route-terrain-open')).toBe(true);
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    toggle().click();
    expect(rail.hasAttribute('data-route-terrain-open')).toBe(false);
  });

  it('opens shut for the next route', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    window.pwaRouteRail.open(feature());
    toggle().click();
    window.pwaRouteRail.close();
    window.pwaRouteRail.open(feature());
    expect(rail.hasAttribute('data-route-terrain-open')).toBe(false);
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
  });

  it('offers nothing for a pending share, which has no rows', () => {
    const pending = feature();
    delete pending.properties.uuid;
    window.pwaRouteRail.open(pending);
    expect(rail.hasAttribute('data-route-terrain-unavailable')).toBe(true);
    expect(toggle().disabled).toBe(true);
    line().querySelector('dt').click();
    expect(rail.getAttribute('data-detent')).toBe('peek');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('the leader line on a sheet', () => {
  it('stops on the sheet’s top edge, where the panel would use its foot', () => {
    window.pwaRouteRail.open(feature());
    vi.spyOn(rail.querySelector('[data-route-rail-lane]'), 'getBoundingClientRect')
      .mockReturnValue({ left: 0, top: 600, right: 1000, bottom: 680, width: 1000, height: 80 });
    vi.spyOn(rail, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, top: 560, right: 1000, bottom: 768, width: 1000, height: 208 });
    window.pwaRouteRail.cursor().setIndex(5);
    expect(window.pwaRouteRail.isSheet()).toBe(true);
    expect(window.pwaRouteRail.cursorPoint().y).toBe(560);
  });

  it('is not a sheet at a desktop width', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    window.pwaRouteRail.open(feature());
    expect(window.pwaRouteRail.isSheet()).toBe(false);
  });
});
