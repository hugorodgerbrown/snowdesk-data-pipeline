/*
 * tests/js/test_route_rail.js — rail one's DOM half
 * (static/js/route_rail.js, SNOW-1018).
 *
 * The assertion the ticket names: pressing a leg opens it on the route
 * cursor, with `aria-pressed` following the CURSOR rather than the click —
 * so a leg closed from rail two's side un-presses here too. Since
 * SNOW-1019 pressing the open leg again moves rail two's window there
 * instead of closing it, rail one brackets what rail two shows, and
 * Escape closes the leg before the rail. Around it: one path per leg with its direction, an
 * unsampled route still cut into legs, a pending share without its
 * menu, the rail closing with the detail sheet, and Delete confirming
 * before it posts.
 *
 * The markup below is the hooks of templates/includes/_route_rail.html;
 * tests/public/test_route_rail.py holds the partial itself to them.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/route_cursor_core.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/route_rail_core.js';
import '../../static/js/bank_ribbon_core.js';
import '../../static/js/route_rail_two_core.js';

const UUID = '11111111-2222-3333-4444-555555555555';

document.body.innerHTML = `
  <div id="map">
    <section id="route-rail" data-route-rail hidden
             data-route-rename-url-template="/routes/partials/__UUID__/rename/"
             data-route-share-url-template="/routes/__UUID__/share/"
             data-route-delete-url-template="/routes/partials/__UUID__/delete/"
             data-route-plan-trip-url="/trips/new/">
      <div data-row-renameable>
        <span data-row-label data-route-rail-name></span>
        <input data-row-rename-input hidden>
        <div data-route-rail-actions>
          <div data-overflow-menu>
            <ul role="menu">
              <li><button role="menuitem" data-route-rail-details>Terrain and bulletin</button></li>
              <li aria-hidden="true" data-route-rail-owner></li>
              <li data-route-rail-owner><a role="menuitem" data-route-rail-plan-trip>Plan a trip</a></li>
              <li data-route-rail-owner><button role="menuitem" data-route-rail-share>Share</button></li>
              <li aria-hidden="true" data-route-rail-owner></li>
              <li data-route-rail-owner><button role="menuitem" data-row-rename data-route-rename="">Rename</button></li>
              <li data-route-rail-owner><button role="menuitem" data-route-rail-delete>Delete</button></li>
            </ul>
          </div>
        </div>
        <button type="button" data-route-rail-close aria-label="Close the route profile"></button>
        <p data-route-rail-figures></p>
        <div data-route-rail-claim hidden></div>
      </div>
      <svg data-route-rail-lane></svg>
      <div data-route-rail-ticks></div>
      <div data-route-rail-readout></div>
      <div data-route-rail-two hidden>
        <p data-route-rail-two-title></p>
        <button type="button" data-route-rail-two-zoom="out" aria-label="Zoom out"></button>
        <button type="button" data-route-rail-two-zoom="in" aria-label="Zoom in"></button>
        <button type="button" data-route-rail-two-close aria-label="Close the leg"></button>
        <p data-route-rail-two-figures></p>
        <svg data-route-rail-two-lane role="slider" tabindex="0"></svg>
        <div data-route-rail-two-ticks></div>
        <div data-route-rail-two-readout></div>
      </div>
      <form data-route-rail-csrf hidden>
        <input type="hidden" name="csrfmiddlewaretoken" value="tok">
      </form>
    </section>
  </div>
  <div id="route-detail-sheet" data-overlay hidden></div>
`;

await import('../../static/js/route_rail_two.js');
await import('../../static/js/route_rail.js');

const rail = document.getElementById('route-rail');
const mapEl = document.getElementById('map');
const sheet = document.getElementById('route-detail-sheet');
const railTwo = rail.querySelector('[data-route-rail-two]');

/**
 * A 24-segment route: a climb over segments 0-11 and a descent over 12-23.
 *
 * @param {object} [overrides] Properties replacing the defaults.
 * @returns {object} A routes-GeoJSON feature.
 */
function feature(overrides = {}) {
  const coordinates = Array.from({ length: 81 }, (_, i) => [
    7.4 + i / 10000,
    46.1,
    i <= 40 ? 1500 + i * 5 : 1700 - (i - 40) * 5,
  ]);
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates },
    properties: {
      uuid: UUID,
      name: 'Mont Fort',
      distance_m: 620,
      ascent_m: 200,
      descent_m: 200,
      slope: { points: [], angles: new Array(24).fill(20) },
      legs: [
        { i: 1, from: 0, to: 11, climbing: true },
        { i: 2, from: 12, to: 23, climbing: false },
      ],
      ...overrides,
    },
  };
}

/** @returns {Array<SVGPathElement>} The leg fills currently drawn. */
function legPaths() {
  return Array.from(rail.querySelectorAll('.route-rail-leg'));
}

beforeEach(() => {
  sheet.setAttribute('hidden', '');
});

/**
 * The menu items a reader can see, by their visible label.
 *
 * @returns {Array<string>}
 */
function visibleMenuItems() {
  return Array.from(rail.querySelectorAll('[role="menuitem"]'))
    .filter((item) => !item.closest('li').hidden)
    .map((item) => item.textContent.trim());
}

/**
 * Press Escape the way a keyboard does: on the focused element, bubbling
 * through the document to the window.
 *
 * @param {Element} [target] Where focus is. Defaults to the body.
 */
function pressEscape(target = document.body) {
  target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
}

afterEach(() => {
  window.pwaRouteRail.close();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('open', () => {
  it('shows the rail and lifts the map’s bottom chrome', () => {
    expect(window.pwaRouteRail.open(feature())).toBe(true);

    expect(rail.hidden).toBe(false);
    expect(mapEl.hasAttribute('data-route-rail-open')).toBe(true);
    expect(window.pwaRouteRail.isOpen()).toBe(true);
  });

  it('draws one path per leg, marked with its direction', () => {
    window.pwaRouteRail.open(feature());

    expect(legPaths().map((p) => p.getAttribute('data-climbing'))).toEqual([
      'true',
      'false',
    ]);
    expect(legPaths().map((p) => p.getAttribute('aria-label'))).toEqual([
      'Leg 1 — climb',
      'Leg 2 — descent',
    ]);
  });

  it('writes the name and the figures line', () => {
    window.pwaRouteRail.open(feature());

    expect(rail.querySelector('[data-route-rail-name]').textContent).toBe('Mont Fort');
    expect(rail.querySelector('[data-route-rail-figures]').textContent).toBe(
      '0.6 km · ▲200 m · ▼200 m · 1500→1500 m',
    );
  });

  it('labels its ticks in one unit', () => {
    window.pwaRouteRail.open(feature());

    const labels = Array.from(
      rail.querySelectorAll('[data-route-rail-ticks] span'),
    ).map((span) => span.textContent);
    expect(labels.length).toBeGreaterThan(1);
    expect(new Set(labels.map((label) => label.split(' ')[1])).size).toBe(1);
  });

  it('points the menu at the open route', () => {
    window.pwaRouteRail.open(feature());

    expect(rail.querySelector('[data-route-rail-plan-trip]').getAttribute('href')).toBe(
      `/trips/new/?route=${UUID}`,
    );
    expect(rail.querySelector('[data-route-rename]').getAttribute('data-route-rename')).toBe(
      UUID,
    );
    expect(rail.querySelector('[data-route-rail-actions]').hidden).toBe(false);
  });

  it('cuts a never-sampled route into legs, sizing the cursor from them', () => {
    window.pwaRouteRail.open(feature({ slope: undefined }));

    expect(legPaths()).toHaveLength(2);
    const cursor = window.pwaRouteRail.cursor();
    expect(cursor).not.toBeNull();
    cursor.setIndex(1000);
    expect(cursor.state().index).toBe(23);
  });

  it('cuts a pending share into legs, though its slope is not drawn', () => {
    window.pwaRouteRail.open(feature({ uuid: undefined, token: 'abc', pending: true }));

    expect(legPaths()).toHaveLength(2);
  });

  it('draws the outline alone for a route with no legs', () => {
    window.pwaRouteRail.open(feature({ slope: undefined, legs: undefined }));

    expect(legPaths()).toHaveLength(0);
    expect(rail.querySelectorAll('[data-route-rail-lane] path')).toHaveLength(1);
    expect(window.pwaRouteRail.cursor()).toBeNull();
  });

  it('keeps only the details item for a pending share, which has no uuid', () => {
    window.pwaRouteRail.open(
      feature({ uuid: undefined, token: 'abc', pending: true }),
      { details: vi.fn() },
    );

    expect(rail.querySelector('[data-route-rail-actions]').hidden).toBe(false);
    expect(visibleMenuItems()).toEqual(['Terrain and bulletin']);
  });

  it('offers every item for an owned route, details first', () => {
    window.pwaRouteRail.open(feature(), { details: vi.fn() });

    expect(visibleMenuItems()).toEqual([
      'Terrain and bulletin',
      'Plan a trip',
      'Share',
      'Rename',
      'Delete',
    ]);
  });

  it('seats a pending share\'s Save in the claim slot', () => {
    const save = document.createElement('button');
    save.textContent = 'Save route';

    window.pwaRouteRail.open(
      feature({ uuid: undefined, token: 'abc', pending: true }),
      { details: vi.fn(), claim: save },
    );

    const slot = rail.querySelector('[data-route-rail-claim]');
    expect(slot.hidden).toBe(false);
    expect(slot.contains(save)).toBe(true);
  });

  it('leaves the claim slot empty for an owned route', () => {
    window.pwaRouteRail.open(feature(), { claim: document.createElement('button') });

    const slot = rail.querySelector('[data-route-rail-claim]');
    expect(slot.hidden).toBe(true);
    expect(slot.children).toHaveLength(0);
  });

  it('omits the range when the GPX\'s first or last point has no elevation', () => {
    // readProfile's first run then starts inside the route, and its last
    // stops short of the finish: those readings are heights the route
    // passes, not the ones it starts and finishes at.
    const gappy = feature();
    const coordinates = gappy.geometry.coordinates;
    coordinates[0] = [coordinates[0][0], coordinates[0][1], null];
    coordinates[coordinates.length - 1] = [
      coordinates[coordinates.length - 1][0],
      coordinates[coordinates.length - 1][1],
      null,
    ];

    window.pwaRouteRail.open(gappy);

    expect(rail.querySelector('[data-route-rail-figures]').textContent).toBe(
      '0.6 km · ▲200 m · ▼200 m',
    );
  });

  it('omits the range when only the finish is missing its elevation', () => {
    const gappy = feature();
    const coordinates = gappy.geometry.coordinates;
    const last = coordinates.length - 1;
    coordinates[last] = [coordinates[last][0], coordinates[last][1], null];

    window.pwaRouteRail.open(gappy);

    expect(rail.querySelector('[data-route-rail-figures]').textContent).not.toContain('→');
  });
});

describe('pressing a leg', () => {
  it('opens it on the cursor, and rail two with it', () => {
    window.pwaRouteRail.open(feature());
    const [first] = legPaths();

    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(window.pwaRouteRail.cursor().state().openLeg).toMatchObject({
      i: 1,
      from: 0,
      to: 11,
    });
    expect(first.getAttribute('aria-pressed')).toBe('true');
    expect(rail.querySelector('[data-route-rail-readout]').textContent).toBe(
      'Leg 1 — climb',
    );
    expect(railTwo.hidden).toBe(false);
  });

  it('moves rail two’s window when the open leg is pressed, without closing it', () => {
    window.pwaRouteRail.open(feature());
    const [first] = legPaths();
    const railLane = rail.querySelector('[data-route-rail-lane]');
    vi.spyOn(railLane, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 1000, bottom: 80, width: 1000, height: 80, x: 0, y: 0,
    });
    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    // Zoom rail two in to its six-sample floor, so there is a window to move.
    const zoomIn = railTwo.querySelector('[data-route-rail-two-zoom="in"]');
    zoomIn.click();
    zoomIn.click();
    expect(window.pwaRouteRailTwo.view()).toEqual({ from: 3, to: 9 });

    // x = 100 of 1000 is sample 2 of 24.
    first.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 100 }));

    expect(window.pwaRouteRail.cursor().state().openLeg).not.toBeNull();
    expect(first.getAttribute('aria-pressed')).toBe('true');
    expect(window.pwaRouteRailTwo.view()).toEqual({ from: 0, to: 6 });
  });

  it('moves the open leg when another is pressed', () => {
    window.pwaRouteRail.open(feature());
    const [first, second] = legPaths();

    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    second.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(window.pwaRouteRail.cursor().state().openLeg.i).toBe(2);
    expect(first.getAttribute('aria-pressed')).toBe('false');
    expect(second.getAttribute('aria-pressed')).toBe('true');
  });

  it('answers Enter from the keyboard', () => {
    window.pwaRouteRail.open(feature());
    const [, second] = legPaths();

    second.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));

    expect(window.pwaRouteRail.cursor().state().openLeg.i).toBe(2);
  });

  it('un-presses when the leg is closed from another surface', () => {
    window.pwaRouteRail.open(feature());
    const [first] = legPaths();
    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    window.pwaRouteRail.cursor().closeLeg();

    expect(first.getAttribute('aria-pressed')).toBe('false');
  });
});

describe('rail two', () => {
  it('is bracketed on rail one while it shows part of the leg', () => {
    window.pwaRouteRail.open(feature());
    const [first] = legPaths();
    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const bracket = () => rail.querySelector('[data-route-rail-window]');

    // A 12-sample leg opens whole: nothing to bracket.
    expect(bracket()).toBeNull();

    railTwo.querySelector('[data-route-rail-two-zoom="in"]').click();
    const view = window.pwaRouteRailTwo.view();
    expect(Number(bracket().getAttribute('x'))).toBeCloseTo((view.from / 24) * 1000);
    expect(Number(bracket().getAttribute('width'))).toBeCloseTo(
      ((view.to - view.from) / 24) * 1000,
    );

    railTwo.querySelector('[data-route-rail-two-zoom="out"]').click();
    expect(bracket()).toBeNull();
  });

  it('closes on Escape before the rail does', () => {
    window.pwaRouteRail.open(feature());
    legPaths()[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));

    pressEscape();
    expect(window.pwaRouteRail.cursor().state().openLeg).toBeNull();
    expect(railTwo.hidden).toBe(true);
    expect(rail.hidden).toBe(false);

    pressEscape();
    expect(rail.hidden).toBe(true);
  });

  it('hides with the rail', () => {
    window.pwaRouteRail.open(feature());
    legPaths()[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));

    window.pwaRouteRail.close();

    expect(railTwo.hidden).toBe(true);
    expect(window.pwaRouteRailTwo.view()).toBeNull();
  });
});

describe('the cursor line (SNOW-1019)', () => {
  /** @returns {?Element} The cursor line, if drawn. */
  const cursorLine = () => rail.querySelector('[data-route-rail-cursor]');

  /**
   * Move a pointer over rail one's lane.
   *
   * @param {string} pointerType 'mouse' or 'touch'.
   * @param {number} clientX The pointer's x.
   */
  const moveOverLane = (pointerType, clientX) => {
    const event = new MouseEvent('pointermove', { bubbles: true, clientX });
    Object.defineProperty(event, 'pointerType', { value: pointerType });
    rail.querySelector('[data-route-rail-lane]').dispatchEvent(event);
  };

  it('draws the cursor index by share, and hides on null', () => {
    window.pwaRouteRail.open(feature());
    expect(cursorLine()).toBeNull();

    window.pwaRouteRail.cursor().setIndex(5);
    // Sample 5 of 24: the middle of the sixth share of 1000.
    expect(Number(cursorLine().getAttribute('x1'))).toBeCloseTo((5.5 / 24) * 1000);
    expect(cursorLine().getAttribute('pointer-events')).toBe('none');
    expect(cursorLine().getAttribute('vector-effect')).toBe('non-scaling-stroke');

    window.pwaRouteRail.cursor().setIndex(null);
    expect(cursorLine()).toBeNull();
  });

  it('moves the cursor on a mouse hover, and not on a touch', () => {
    window.pwaRouteRail.open(feature());
    vi.spyOn(rail.querySelector('[data-route-rail-lane]'), 'getBoundingClientRect')
      .mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 80, width: 1000, height: 80 });

    moveOverLane('touch', 500);
    expect(window.pwaRouteRail.cursor().state().index).toBeNull();

    // 500 of 1000 is sample 12 of 24.
    moveOverLane('mouse', 500);
    expect(window.pwaRouteRail.cursor().state().index).toBe(12);
  });

  it('holds a hover to the open leg', () => {
    window.pwaRouteRail.open(feature());
    vi.spyOn(rail.querySelector('[data-route-rail-lane]'), 'getBoundingClientRect')
      .mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 80, width: 1000, height: 80 });
    legPaths()[0].dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 100 }));

    moveOverLane('mouse', 900);

    expect(window.pwaRouteRail.cursor().state().index).toBe(11);
  });

  it('reports where its cursor meets the profile, for the leader line', () => {
    window.pwaRouteRail.open(feature());
    vi.spyOn(rail.querySelector('[data-route-rail-lane]'), 'getBoundingClientRect')
      .mockReturnValue({ left: 10, top: 100, right: 1010, bottom: 196, width: 1000, height: 96 });
    expect(window.pwaRouteRail.cursorPoint()).toBeNull();

    window.pwaRouteRail.cursor().setIndex(5);
    const point = window.pwaRouteRail.cursorPoint();

    expect(point.x).toBeCloseTo(10 + (5.5 / 24) * 1000);
    // On the climb, so above the lane's floor and below its top.
    expect(point.y).toBeGreaterThan(100);
    expect(point.y).toBeLessThan(196);
  });

  it('announces opening and closing, which the leader line follows', () => {
    const heard = vi.fn();
    document.addEventListener('snowdesk:route-rail-changed', heard);

    window.pwaRouteRail.open(feature());
    window.pwaRouteRail.close();

    expect(heard).toHaveBeenCalledTimes(2);
    document.removeEventListener('snowdesk:route-rail-changed', heard);
  });

  it('clears the cursor when the rail closes', () => {
    window.pwaRouteRail.open(feature());
    const cursor = window.pwaRouteRail.cursor();
    cursor.setIndex(3);
    cursor.select({ kind: 'band', from: 2, to: 4 });

    window.pwaRouteRail.close();

    expect(cursor.state()).toMatchObject({ index: null, selection: null, openLeg: null });
  });
});

describe('the details item', () => {
  it('calls the details callback map.js handed over', () => {
    const details = vi.fn();
    window.pwaRouteRail.open(feature(), { details });

    rail.querySelector('[data-route-rail-details]').click();

    expect(details).toHaveBeenCalledTimes(1);
  });

  it('is hidden when there is no sheet to open', () => {
    window.pwaRouteRail.open(feature());

    expect(visibleMenuItems()).not.toContain('Terrain and bulletin');
  });
});

describe('lifetime', () => {
  it('stays open while the sheet opens and closes over it', async () => {
    window.pwaRouteRail.open(feature(), {
      details: () => sheet.removeAttribute('hidden'),
    });

    rail.querySelector('[data-route-rail-details]').click();
    await Promise.resolve();
    expect(rail.hidden).toBe(false);

    sheet.setAttribute('hidden', '');
    await Promise.resolve();
    expect(rail.hidden).toBe(false);
    expect(window.pwaRouteRail.cursor()).not.toBeNull();
  });

  it('closes on its own ×', () => {
    window.pwaRouteRail.open(feature());

    rail.querySelector('[data-route-rail-close]').click();

    expect(rail.hidden).toBe(true);
    expect(mapEl.hasAttribute('data-route-rail-open')).toBe(false);
    expect(window.pwaRouteRail.cursor()).toBeNull();
  });

  it('closes the open leg before it lets the cursor go', () => {
    // SNOW-1017: the map follows the cursor to dim every leg but the open
    // one, and never sees the rail's own ×, Escape or backdrop closes. So
    // close() has to say `openLeg: null` to whoever is still listening.
    window.pwaRouteRail.open(feature());
    const cursor = window.pwaRouteRail.cursor();
    const heard = [];
    cursor.subscribe((state) => heard.push(state.openLeg));
    legPaths()[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));

    window.pwaRouteRail.close();

    expect(heard.map((leg) => (leg ? leg.i : null))).toEqual([1, null]);
  });

  it('closes on Escape when nothing else is open', () => {
    window.pwaRouteRail.open(feature());

    pressEscape();

    expect(rail.hidden).toBe(true);
  });

  it('leaves Escape to an open sheet', () => {
    window.pwaRouteRail.open(feature());
    sheet.removeAttribute('hidden');

    pressEscape();

    expect(rail.hidden).toBe(false);
  });

  it('leaves Escape to a field being edited', () => {
    window.pwaRouteRail.open(feature());
    const input = rail.querySelector('[data-row-rename-input]');

    pressEscape(input);

    expect(rail.hidden).toBe(false);
  });

  it('replaces its contents when another route opens', () => {
    window.pwaRouteRail.open(feature());
    window.pwaRouteRail.open(feature({ name: 'Rosablanche' }));

    expect(rail.querySelector('[data-route-rail-name]').textContent).toBe('Rosablanche');
    expect(rail.hidden).toBe(false);
  });
});

describe('Delete', () => {
  it('posts nothing when the confirmation is declined', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    window.pwaRouteRail.open(feature());

    rail.querySelector('[data-route-rail-delete]').click();

    expect(window.confirm).toHaveBeenCalledWith(
      "Delete Mont Fort? You'll need the .gpx file again to put it back.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('posts, closes and announces once confirmed', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const changed = vi.fn();
    document.addEventListener('snowdesk:routes-changed', changed);
    window.pwaRouteRail.open(feature());

    rail.querySelector('[data-route-rail-delete]').click();
    await vi.waitFor(() => expect(changed).toHaveBeenCalled());

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`/routes/partials/${UUID}/delete/`);
    expect(init.method).toBe('POST');
    expect(init.headers['X-CSRFToken']).toBe('tok');
    expect(rail.hidden).toBe(true);
    document.removeEventListener('snowdesk:routes-changed', changed);
  });
});
