/*
 * tests/js/test_route_rail.js — the route rail's DOM half
 * (static/js/route_rail.js, SNOW-1018; one rail since SNOW-1065).
 *
 * The assertions SNOW-1065 names: the subtitle is the routes list's meta
 * line, with the time only when the recording has one; pressing a leg
 * opens it on the route cursor, swaps the title to "… • Leg N" and the
 * subtitle to the leg's own figures, and pressing it again restores both;
 * a leg or a point, never both, so a hover leaves an open leg alone and a
 * drag along the lane places a point. `aria-pressed` follows the CURSOR
 * rather than the click, so a leg closed from elsewhere un-presses here
 * too. Around it: one path per leg with its direction, an unsampled route
 * still cut into legs, a pending share without its menu, the rail closing
 * with the detail sheet, and Delete confirming before it posts.
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

const UUID = '11111111-2222-3333-4444-555555555555';

document.body.innerHTML = `
  <div id="map">
    <section id="route-rail" data-route-rail hidden
             data-route-rename-url-template="/routes/partials/__UUID__/rename/"
             data-route-share-url-template="/routes/__UUID__/share/"
             data-route-delete-url-template="/routes/partials/__UUID__/delete/"
             data-route-plan-trip-url="/trips/new/">
      <div data-row-renameable>
        <h2 id="route-rail-eyebrow">Route</h2>
        <p data-route-rail-title>
          <span data-row-label data-route-rail-name></span>
          <span data-route-rail-leg></span>
        </p>
        <input data-row-rename-input hidden>
        <div data-route-rail-actions>
          <div data-overflow-menu>
            <ul role="menu">
              <li><button role="menuitem" data-route-rail-details>Terrain</button></li>
              <li aria-hidden="true" data-route-rail-owner></li>
              <li data-route-rail-owner><a role="menuitem" data-route-rail-plan-trip>Plan a trip</a></li>
              <li data-route-rail-owner><button role="menuitem" data-route-rail-share>Share</button></li>
              <li aria-hidden="true" data-route-rail-owner></li>
              <li data-route-rail-owner><button role="menuitem" data-row-rename data-route-rename="">Rename</button></li>
              <li data-route-rail-owner><button role="menuitem" data-route-rail-delete>Delete</button></li>
            </ul>
          </div>
        </div>
        <button type="button" data-route-rail-close aria-label="Close the route profile"
                data-label-route="Close the route profile" data-label-point="Clear the point"></button>
        <p data-route-rail-meta></p>
        <div data-route-rail-claim hidden></div>
      </div>
      <svg data-route-rail-lane></svg>
      <div data-route-rail-ticks></div>
      <form data-route-rail-csrf hidden>
        <input type="hidden" name="csrfmiddlewaretoken" value="tok">
      </form>
    </section>
  </div>
  <div id="route-detail-sheet" data-overlay hidden></div>
`;

// jsdom has no ResizeObserver. A stub that records each observed element
// and its callback, so a test can fire a size change by hand.
const observed = [];
window.ResizeObserver = class {
  constructor(callback) {
    this.callback = callback;
  }

  observe(target) {
    observed.push({ target, callback: this.callback });
  }

  disconnect() {}
};

await import('../../static/js/route_rail.js');

const rail = document.getElementById('route-rail');
const mapEl = document.getElementById('map');
const sheet = document.getElementById('route-detail-sheet');
const meta = rail.querySelector('[data-route-rail-meta]');
const legSuffix = rail.querySelector('[data-route-rail-leg]');

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

  it('writes the name and the routes list’s meta line', () => {
    window.pwaRouteRail.open(
      feature({ ascent_m: 337, descent_m: 1906, distance_m: 12900, duration_s: 10260 }),
    );

    expect(rail.querySelector('[data-route-rail-name]').textContent).toBe('Mont Fort');
    expect(meta.textContent).toBe('12.9km · 337m ↑ · 1906m ↓ · 2h51m');
  });

  it('leaves the time off a recording with none', () => {
    window.pwaRouteRail.open(feature({ ascent_m: 337, descent_m: 1906, distance_m: 12900 }));

    expect(meta.textContent).toBe('12.9km · 337m ↑ · 1906m ↓');
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

  it('cuts a pending share into legs', () => {
    window.pwaRouteRail.open(feature({ uuid: undefined, token: 'abc', pending: true }));

    expect(legPaths()).toHaveLength(2);
  });

  it('hands a pending share’s slope record to the point card', () => {
    // A share carries the same slope record as an owned route.
    const calls = [];
    window.pwaRoutePointCard = {
      attach: (options) => calls.push(options),
      detach: () => {},
    };
    try {
      window.pwaRouteRail.open(feature({ uuid: undefined, token: 'abc', pending: true }));
      expect(calls[0].slope.angles).toHaveLength(24);
    } finally {
      delete window.pwaRoutePointCard;
    }
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
    expect(visibleMenuItems()).toEqual(['Terrain']);
  });

  it('offers every item for an owned route, details first', () => {
    window.pwaRouteRail.open(feature(), { details: vi.fn() });

    expect(visibleMenuItems()).toEqual([
      'Terrain',
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

  it('omits an unknown ascent rather than showing zero', () => {
    window.pwaRouteRail.open(feature({ ascent_m: null }));

    expect(meta.textContent).toBe('0.6km · 200m ↓');
  });

  it('drops the steep-terrain figure (SNOW-1065)', () => {
    window.pwaRouteRail.open(feature({ terrain: { surveyed_m: 620, steep_m: 300 } }));

    expect(meta.textContent).not.toMatch(/steep/);
  });
});

describe('pressing a leg', () => {
  it('opens it on the cursor and swaps the header to the leg', () => {
    window.pwaRouteRail.open(feature({ duration_s: 3600 }));
    const [first] = legPaths();

    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(window.pwaRouteRail.cursor().state().openLeg).toMatchObject({
      i: 1,
      from: 0,
      to: 11,
    });
    expect(first.getAttribute('aria-pressed')).toBe('true');
    expect(legSuffix.textContent).toBe('• Leg 1');
    // Half the route's 620 m, the whole 200 m climb, no descent and no
    // time: track points carry no timestamps.
    expect(meta.textContent).toBe('0.3km · 200m ↑ · 0m ↓');
  });

  it('closes when the open leg is pressed again, restoring the header', () => {
    window.pwaRouteRail.open(feature({ duration_s: 3600 }));
    const [first] = legPaths();
    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(window.pwaRouteRail.cursor().state().openLeg).toBeNull();
    expect(first.getAttribute('aria-pressed')).toBe('false');
    expect(legSuffix.textContent).toBe('');
    expect(meta.textContent).toBe('0.6km · 200m ↑ · 200m ↓ · 1h00m');
  });

  it('clears a placed point when it opens', () => {
    window.pwaRouteRail.open(feature());
    window.pwaRouteRail.cursor().setIndex(5);

    legPaths()[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(window.pwaRouteRail.cursor().state().index).toBeNull();
    expect(rail.querySelector('[data-route-rail-cursor]')).toBeNull();
  });

  it('closes when a point is placed', () => {
    window.pwaRouteRail.open(feature());
    legPaths()[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));

    window.pwaRouteRail.cursor().setIndex(5);

    expect(legPaths()[1].getAttribute('aria-pressed')).toBe('false');
    expect(legSuffix.textContent).toBe('');
  });

  it('moves the open leg when another is pressed', () => {
    window.pwaRouteRail.open(feature());
    const [first, second] = legPaths();

    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    second.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(window.pwaRouteRail.cursor().state().openLeg.i).toBe(2);
    expect(first.getAttribute('aria-pressed')).toBe('false');
    expect(second.getAttribute('aria-pressed')).toBe('true');
    expect(legSuffix.textContent).toBe('• Leg 2');
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

describe('Escape', () => {
  it('closes the open leg before the rail', () => {
    window.pwaRouteRail.open(feature());
    legPaths()[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));

    pressEscape();
    expect(window.pwaRouteRail.cursor().state().openLeg).toBeNull();
    expect(rail.hidden).toBe(false);

    pressEscape();
    expect(rail.hidden).toBe(true);
  });
});

describe('the cursor line (SNOW-1019)', () => {
  /** @returns {?Element} The cursor line, if drawn. */
  const cursorLine = () => rail.querySelector('[data-route-rail-cursor]');

  /**
   * Fire a pointer event on the rail's lane.
   *
   * @param {string} type The event type.
   * @param {string} pointerType 'mouse' or 'touch'.
   * @param {number} clientX The pointer's x.
   */
  const pointerOnLane = (type, pointerType, clientX, buttons = 1) => {
    const event = new MouseEvent(type, { bubbles: true, clientX, buttons });
    Object.defineProperty(event, 'pointerType', { value: pointerType });
    Object.defineProperty(event, 'pointerId', { value: 7 });
    rail.querySelector('[data-route-rail-lane]').dispatchEvent(event);
  };
  const moveOverLane = (pointerType, clientX) => pointerOnLane('pointermove', pointerType, clientX, 0);

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

  it('leaves an open leg alone on a hover (SNOW-1065)', () => {
    window.pwaRouteRail.open(feature());
    vi.spyOn(rail.querySelector('[data-route-rail-lane]'), 'getBoundingClientRect')
      .mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 80, width: 1000, height: 80 });
    legPaths()[0].dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 100 }));

    moveOverLane('mouse', 900);

    expect(window.pwaRouteRail.cursor().state()).toMatchObject({ index: null, openLeg: { i: 1 } });
  });

  it('places the point on a drag along the lane, and swallows its click', () => {
    window.pwaRouteRail.open(feature());
    vi.spyOn(rail.querySelector('[data-route-rail-lane]'), 'getBoundingClientRect')
      .mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 80, width: 1000, height: 80 });
    legPaths()[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const [first] = legPaths();

    pointerOnLane('pointerdown', 'touch', 100);
    pointerOnLane('pointermove', 'touch', 104); // under the drag threshold
    expect(window.pwaRouteRail.cursor().state().index).toBeNull();
    pointerOnLane('pointermove', 'touch', 600); // 600 of 1000: sample 14
    pointerOnLane('pointerup', 'touch', 600);
    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(window.pwaRouteRail.cursor().state()).toEqual({ index: 14, openLeg: null });
  });

  it('forgets a press released off the lane before it became a drag', () => {
    // The pointerup landed outside the lane, so only a buttonless move
    // tells it the press is over; a mouse coming back must not drag.
    window.pwaRouteRail.open(feature());
    vi.spyOn(rail.querySelector('[data-route-rail-lane]'), 'getBoundingClientRect')
      .mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 80, width: 1000, height: 80 });
    legPaths()[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));

    pointerOnLane('pointerdown', 'mouse', 100);
    pointerOnLane('pointermove', 'mouse', 600, 0);

    expect(window.pwaRouteRail.cursor().state()).toMatchObject({ index: null, openLeg: { i: 1 } });
  });

  it('leaves a press that does not move to the leg it lands on', () => {
    window.pwaRouteRail.open(feature());
    const [first] = legPaths();

    pointerOnLane('pointerdown', 'touch', 100);
    pointerOnLane('pointerup', 'touch', 102);
    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(window.pwaRouteRail.cursor().state().openLeg.i).toBe(1);
  });

  it('reports the panel’s bottom edge below its cursor, for the leader line', () => {
    window.pwaRouteRail.open(feature());
    vi.spyOn(rail.querySelector('[data-route-rail-lane]'), 'getBoundingClientRect')
      .mockReturnValue({ left: 10, top: 100, right: 1010, bottom: 196, width: 1000, height: 96 });
    vi.spyOn(rail, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, top: 40, right: 1020, bottom: 210, width: 1020, height: 170 });
    expect(window.pwaRouteRail.cursorPoint()).toBeNull();

    window.pwaRouteRail.cursor().setIndex(5);
    const point = window.pwaRouteRail.cursorPoint();

    expect(point.x).toBeCloseTo(10 + (5.5 / 24) * 1000);
    expect(point.y).toBe(210);
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

    window.pwaRouteRail.close();

    expect(cursor.state()).toEqual({ index: null, openLeg: null });
  });
});

describe('the panel resizing (SNOW-1019)', () => {
  it('announces a change in the panel\'s height, and moves nothing', () => {
    window.pwaRouteRail.open(feature());
    const heard = vi.fn();
    document.addEventListener('snowdesk:route-rail-resized', heard);

    for (const { target, callback } of observed) {
      if (target === rail) callback([]);
    }

    expect(heard).toHaveBeenCalledTimes(1);
    // SNOW-1068: pinned top-left, the panel lifts nothing at the foot.
    expect(mapEl.style.getPropertyValue('--route-rail-height')).toBe('');
    document.removeEventListener('snowdesk:route-rail-resized', heard);
  });

  it('observes the rail itself', () => {
    expect(observed.some(({ target }) => target === rail)).toBe(true);
  });
});

describe('the point header (SNOW-1068)', () => {
  const eyebrow = () => document.getElementById('route-rail-eyebrow');
  const title = () => rail.querySelector('[data-route-rail-title]');
  const meta = () => rail.querySelector('[data-route-rail-meta]');
  const closeButton = () => rail.querySelector('[data-route-rail-close]');

  it('shows the route header with no point placed', () => {
    window.pwaRouteRail.open(feature());
    expect(eyebrow().textContent).toBe('Route');
    expect(title().hidden).toBe(false);
    expect(meta().hidden).toBe(false);
    expect(rail.hasAttribute('data-route-rail-point')).toBe(false);
    expect(closeButton().getAttribute('aria-label')).toBe('Close the route profile');
  });

  it('gives the title and meta line way to a point, naming it in the eyebrow', () => {
    window.pwaRouteRail.open(feature());
    window.pwaRouteRail.cursor().setIndex(5);
    expect(rail.hasAttribute('data-route-rail-point')).toBe(true);
    expect(title().hidden).toBe(true);
    expect(meta().hidden).toBe(true);
    // 5.5 of 24 segments along 620 m, to the nearest 10 m.
    expect(eyebrow().textContent).toBe('Mont Fort · 140 m');
    expect(closeButton().getAttribute('aria-label')).toBe('Clear the point');
  });

  it('returns to the leg header when the point clears and a leg opens', () => {
    window.pwaRouteRail.open(feature());
    const cursor = window.pwaRouteRail.cursor();
    cursor.setIndex(5);
    cursor.openLeg({ i: 2, from: 12, to: 23, climbing: false });
    expect(title().hidden).toBe(false);
    expect(eyebrow().textContent).toBe('Route');
    expect(rail.querySelector('[data-route-rail-leg]').textContent).toBe('• Leg 2');
  });

  it('clears the point on the ×, keeping the route, then closes on the next', () => {
    window.pwaRouteRail.open(feature());
    const cursor = window.pwaRouteRail.cursor();
    cursor.setIndex(5);

    closeButton().click();
    expect(cursor.state().index).toBeNull();
    expect(window.pwaRouteRail.isOpen()).toBe(true);
    expect(title().hidden).toBe(false);

    closeButton().click();
    expect(window.pwaRouteRail.isOpen()).toBe(false);
  });

  it('restores the route header when the panel closes on a point', () => {
    window.pwaRouteRail.open(feature());
    window.pwaRouteRail.cursor().setIndex(5);
    window.pwaRouteRail.close();
    expect(eyebrow().textContent).toBe('Route');
    expect(title().hidden).toBe(false);
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

    expect(visibleMenuItems()).not.toContain('Terrain');
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

  it('clears a placed point on the first Escape and closes on the next (SNOW-1064)', () => {
    window.pwaRouteRail.open(feature());
    const cursor = window.pwaRouteRail.cursor();
    cursor.setIndex(5);

    pressEscape();

    expect(cursor.state().index).toBeNull();
    expect(rail.hidden).toBe(false);

    pressEscape();

    expect(rail.hidden).toBe(true);
  });

  it('attaches the point card on open and detaches it on close (SNOW-1064)', () => {
    const calls = [];
    window.pwaRoutePointCard = {
      attach: (options) => calls.push(['attach', options]),
      detach: () => calls.push(['detach']),
    };
    try {
      window.pwaRouteRail.open(feature());
      const attached = calls.find((call) => call[0] === 'attach');
      expect(attached[1].cursor).toBe(window.pwaRouteRail.cursor());
      expect(attached[1].sampleCount).toBe(24);
      expect(attached[1].spanM).toBe(620);
      expect(attached[1].legs).toHaveLength(2);
      calls.length = 0;
      window.pwaRouteRail.close();
      expect(calls).toEqual([['detach']]);
    } finally {
      delete window.pwaRoutePointCard;
    }
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
