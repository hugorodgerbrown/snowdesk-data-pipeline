/*
 * tests/js/test_route_rail_two.js — rail two's DOM half
 * (static/js/route_rail_two.js, SNOW-1019).
 *
 * Rail two follows the route cursor: it draws the leg on `openLeg` and
 * shows its empty state on `closeLeg` and on attach (SNOW-1024), and hides
 * on detach. Around that: a band tap selects and a second tap clears, a
 * passage tap selects a passage, a second pointer cancels the press so a
 * pinch selects nothing, the −/+ buttons change the span and disable at
 * the limits, an index published from elsewhere scrolls the window, a
 * one-finger drag scrubs the cursor while two fingers pan it, a null bank
 * draws no tick, and the readout reads the terrain under the cursor or the stretch selected, stepped left, centred
 * or right under its anchor (SNOW-1024). SNOW-1031's revision: a leg opens
 * fitted, a long one shows the bank placeholder, a double-click or a touch
 * double-tap zooms to where the bank row draws and back, and passage bars
 * are 4 px tall and never under 6 px wide. SNOW-1032: a tap picks by row
 * and by nearest extent, the selection box is at least 12 px with the
 * rest of the lane dimmed, a band's selection carries its class, and a
 * touch or mouse drag scrubs and on release selects the band holding the
 * cursor's index, exactly; a mouse drag no longer pans.
 * SNOW-1033: the empty lane is a leg picker, one button per leg opening it
 * through the cursor, and opening or closing a leg runs a WAAPI motion
 * that is skipped where `Element.prototype.animate` is missing or motion
 * is reduced.
 *
 * jsdom lays nothing out, so the lane measures 0 px and rail two falls
 * back to 600 px; a pointer's lane x is its clientX. Pointer events are
 * built as PointerEvent where jsdom has it and as a MouseEvent carrying a
 * `pointerId` where it does not.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/route_cursor_core.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/route_rail_core.js';
import '../../static/js/bank_ribbon_core.js';
import '../../static/js/route_rail_two_core.js';

document.body.innerHTML = `
  <section id="route-rail">
    <div data-route-rail-two hidden>
      <p data-route-rail-two-title></p>
      <div>
        <button type="button" data-route-rail-two-zoom="out" aria-label="Zoom out"></button>
        <button type="button" data-route-rail-two-zoom="in" aria-label="Zoom in"></button>
        <button type="button" data-route-rail-two-close aria-label="Close the leg"></button>
      </div>
      <p data-route-rail-two-figures></p>
      <div>
        <svg data-route-rail-two-lane role="slider" tabindex="0"></svg>
        <div data-route-rail-two-legs hidden></div>
      </div>
      <div data-route-rail-two-readout-box>
        <span data-route-rail-two-stem hidden></span>
        <div data-route-rail-two-readout></div>
      </div>
    </div>
  </section>
`;

await import('../../static/js/route_rail_two.js');

const two = window.pwaRouteRailTwo;
const row = document.querySelector('[data-route-rail-two]');
const lane = row.querySelector('[data-route-rail-two-lane]');
const readout = row.querySelector('[data-route-rail-two-readout]');
const readoutBox = row.querySelector('[data-route-rail-two-readout-box]');
const stem = row.querySelector('[data-route-rail-two-stem]');
const title = row.querySelector('[data-route-rail-two-title]');
const zoomOutButton = row.querySelector('[data-route-rail-two-zoom="out"]');
const zoomInButton = row.querySelector('[data-route-rail-two-zoom="in"]');
const closeButton = row.querySelector('[data-route-rail-two-close]');
const legsLayer = row.querySelector('[data-route-rail-two-legs]');

/** @returns {Array<HTMLButtonElement>} The leg picker's buttons. */
const legButtons = () => Array.from(legsLayer.querySelectorAll('.route-rail-two-leg'));

/** @returns {Array<string>} The readout's lines. */
const readoutLines = () => Array.from(readout.children).map((line) => line.textContent);

/**
 * 460 samples over 23 km: 50 m a sample. Leg 2 is 40 samples, 15 px each
 * fitted across 600 px, so its bank row draws; leg 3 is 320 samples,
 * 1.9 px each fitted, so its bank row shows the placeholder, and three
 * zoom-ins halve it to 40.
 */
const N = 460;
const SPAN_M = 23000;
const LEGS = [
  { i: 1, from: 0, to: 99, climbing: true },
  { i: 2, from: 100, to: 139, climbing: false },
  { i: 3, from: 140, to: 459, climbing: true },
];

/** A clock the tests move, so two taps are a double-tap only on purpose. */
let now = 1e6;

/**
 * Angles in runs of five, 20° then 32°, so every band is five samples wide
 * and starts on a multiple of five.
 */
const ANGLES = Array.from({ length: N }, (_, i) => (Math.floor(i / 5) % 2 ? 32 : 20));
const BANKS = Array.from({ length: N }, (_, i) => (i % 2 ? 20 : -20));

/**
 * A straight track of `count` points with elevation.
 *
 * @param {number} count
 * @returns {Array<Array<number>>}
 */
function track(count) {
  return Array.from({ length: count }, (_, i) => [7.4 + i / 1000, 46.1, 1500 + (i % 50) * 4]);
}

/**
 * Attach rail two to a fresh cursor over the test route.
 *
 * @param {object} [slopeOverrides] Keys replacing the default slope record.
 * @returns {{cursor: object, onView: Function, onResize: Function}}
 */
function attach(slopeOverrides = {}) {
  const cursor = self.pwaRouteCursorCore.createRouteCursor(N);
  const onView = vi.fn();
  const onResize = vi.fn();
  two.attach({
    cursor,
    slope: {
      angles: ANGLES,
      banks: BANKS,
      passages: [{ from: 110, to: 114, m: 250, fall_line: 'across' }],
      ...slopeOverrides,
    },
    profile: self.pwaElevationProfileCore.readProfile(track(200)),
    legs: LEGS,
    sampleCount: N,
    spanM: SPAN_M,
    onView,
    onResize,
  });
  return { cursor, onView, onResize };
}

/**
 * Dispatch a pointer event on an element.
 *
 * @param {Element} target
 * @param {string} type
 * @param {{x?: number, y?: number, id?: number, pointerType?: string}} [init]
 */
function pointer(target, type, { x = 0, y = 0, id = 1, pointerType = 'touch' } = {}) {
  const options = { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 };
  let event;
  if (typeof PointerEvent === 'function') {
    event = new PointerEvent(type, { ...options, pointerId: id, pointerType });
  } else {
    event = new MouseEvent(type, options);
    Object.defineProperty(event, 'pointerId', { value: id });
    Object.defineProperty(event, 'pointerType', { value: pointerType });
  }
  target.dispatchEvent(event);
}

/**
 * Tap an element one px inside its top-left corner.
 *
 * @param {Element} el A band or passage rect.
 */
function tap(el) {
  now += 1000;
  const x = Number(el.getAttribute('x')) + 1;
  const y = Number(el.getAttribute('y')) + 1;
  pointer(el, 'pointerdown', { x, y });
  pointer(lane, 'pointerup', { x, y });
}

/** @returns {Array<Element>} The band rects drawn. */
function bandRects() {
  return Array.from(lane.querySelectorAll('.route-rail-two-band'));
}

/**
 * Open leg 3 and zoom in three times about its centre, to the 40-sample
 * window 280–320.
 *
 * @param {object} cursor
 */
function openLongZoomed(cursor) {
  cursor.openLeg(LEGS[2]);
  zoomInButton.click();
  zoomInButton.click();
  zoomInButton.click();
  expect(two.view()).toEqual({ from: 280, to: 320 });
}

beforeEach(() => {
  vi.spyOn(Date, 'now').mockImplementation(() => now);
});

afterEach(() => {
  two.detach();
  vi.restoreAllMocks();
});

describe('following the cursor', () => {
  it('draws openLeg fitted and empties on closeLeg', () => {
    const { cursor, onView, onResize } = attach();
    expect(row.hidden).toBe(false);
    expect(row.hasAttribute('data-empty')).toBe(true);
    expect(onResize).toHaveBeenCalledTimes(1);

    cursor.openLeg(LEGS[1]);
    expect(row.hasAttribute('data-empty')).toBe(false);
    expect(two.view()).toEqual({ from: 100, to: 140 });
    expect(title.textContent).toBe('Leg 2 — descent');
    expect(onView).toHaveBeenLastCalledWith({ from: 100, to: 140 }, expect.anything());
    expect(onResize).toHaveBeenCalledTimes(2);

    cursor.closeLeg();
    expect(row.hidden).toBe(false);
    expect(row.hasAttribute('data-empty')).toBe(true);
    expect(two.view()).toBeNull();
    expect(onView).toHaveBeenLastCalledWith(null, null);
    expect(onResize).toHaveBeenCalledTimes(3);
  });

  it('hides the row outright on detach', () => {
    attach();

    two.detach();

    expect(row.hidden).toBe(true);
  });

  it('closes the leg on its own ×', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    closeButton.click();

    expect(cursor.state().openLeg).toBeNull();
    expect(row.hasAttribute('data-empty')).toBe(true);
  });

  it('centres the window on an index published elsewhere', () => {
    // A map tap or rail one's hover: the cursor lands mid-lane, not on its
    // edge, where the leader line and the cursor line could barely be seen.
    const { cursor } = attach();
    openLongZoomed(cursor);

    cursor.setIndex(350);

    // Sample 350's centre, 350.5, in the middle of a 40-sample window.
    expect(two.view()).toEqual({ from: 330.5, to: 370.5 });
  });

  it('still scrolls the least distance for an arrow-key step past the edge', () => {
    const { cursor } = attach();
    openLongZoomed(cursor);
    cursor.setIndex(319);
    expect(two.view()).toEqual({ from: 280, to: 320 });

    lane.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));

    expect(cursor.state().index).toBe(320);
    expect(two.view()).toEqual({ from: 281, to: 321 });
  });

  it('centres a selection from elsewhere that fits the window', () => {
    const { cursor } = attach();
    openLongZoomed(cursor);

    cursor.select({ kind: 'passage', from: 330, to: 339 });

    // The range 330–340 centred in 40 samples.
    expect(two.view()).toEqual({ from: 315, to: 355 });
  });

  it('aligns a selection longer than the window with its left edge', () => {
    const { cursor } = attach();
    openLongZoomed(cursor);

    cursor.select({ kind: 'band', from: 330, to: 400 });

    expect(two.view()).toEqual({ from: 330, to: 370 });
    expect(lane.querySelector('[data-route-rail-two-selection]')).not.toBeNull();
  });
});

describe('pressing a band or a passage', () => {
  it('selects a band on a tap, and a second tap clears it', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    const band = bandRects().find((rect) => rect.getAttribute('data-from') === '105');

    tap(band);
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 105, to: 109, classIndex: 1 });

    tap(bandRects().find((rect) => rect.getAttribute('data-from') === '105'));
    expect(cursor.state().selection).toBeNull();
  });

  it('selects a passage as a passage', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    tap(lane.querySelector('.route-rail-two-passage'));

    expect(cursor.state().selection)
      .toEqual({ kind: 'passage', from: 110, to: 114, classIndex: null });
    expect(readoutLines()).toEqual(['No-fall passage · 250 m']);
  });

  it('picks a passage up to 22 px beside its bar, in the passage row (SNOW-1032)', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    // Passage 110–114 is drawn at 150–225 px; 20 px right of its end.
    pointer(lane, 'pointerdown', { x: 245, y: 42 });
    pointer(lane, 'pointerup', { x: 245, y: 42 });

    expect(cursor.state().selection).toEqual({ kind: 'passage', from: 110, to: 114, classIndex: null });
  });

  it('picks nothing in the passage row farther than 22 px from a bar', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    pointer(lane, 'pointerdown', { x: 260, y: 42 });
    pointer(lane, 'pointerup', { x: 260, y: 42 });

    expect(cursor.state().selection).toBeNull();
  });

  it('picks the band under a tap anywhere above the passage row', () => {
    // In the wedge row, which draws nothing a pointer can hit.
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    pointer(lane, 'pointerdown', { x: 80, y: 27 });
    pointer(lane, 'pointerup', { x: 80, y: 27 });

    expect(cursor.state().selection).toEqual({ kind: 'band', from: 105, to: 109, classIndex: 1 });
  });

  it('gives a thin steep band a 44 px target: a tap picks the steepest band within 22 px', () => {
    // Leg 3 fitted: 1.9 px a sample. A one-segment 42° band at 300 sits in
    // 20° ground; a tap 15 px to its left, on the gentle band, takes it.
    const angles = ANGLES.map((angle, i) => (i >= 290 && i <= 310 ? (i === 300 ? 42 : 20) : angle));
    const { cursor } = attach({ angles });
    cursor.openLeg(LEGS[2]);
    const steep = bandRects().find((rect) => rect.getAttribute('data-from') === '300');
    const x = Number(steep.getAttribute('x')) - 15;

    pointer(lane, 'pointerdown', { x, y: 5 });
    pointer(lane, 'pointerup', { x, y: 5 });

    expect(cursor.state().selection).toEqual({ kind: 'band', from: 300, to: 300, classIndex: 3 });
    // The cursor is pulled onto what was selected.
    expect(cursor.state().index).toBe(300);
  });

  it('keeps the band under the tap when nothing steeper is within 22 px', () => {
    const angles = ANGLES.map((angle, i) => (i >= 280 && i <= 320 ? 20 : angle));
    const { cursor } = attach({ angles });
    cursor.openLeg(LEGS[2]);
    const x = (300.5 - 140) * (600 / 320);

    pointer(lane, 'pointerdown', { x, y: 5 });
    pointer(lane, 'pointerup', { x, y: 5 });

    // 320–324 are 20° in the fixture too, so the gentle band runs to 324.
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 280, to: 324, classIndex: 0 });
  });

  it('scales the tap\'s y to the lane\'s rows', () => {
    // A lane laid out twice as tall: client y 60 is lane y 30, the wedges.
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    vi.spyOn(lane, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, top: 0, right: 600, bottom: 88, width: 600, height: 88 });

    pointer(lane, 'pointerdown', { x: 80, y: 60 });
    pointer(lane, 'pointerup', { x: 80, y: 60 });

    expect(cursor.state().selection.kind).toBe('band');
    vi.restoreAllMocks();
  });

  it('selects nothing when a second pointer turns the press into a pinch', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    const band = bandRects().find((rect) => rect.getAttribute('data-from') === '105');
    const x = Number(band.getAttribute('x')) + 1;

    pointer(band, 'pointerdown', { x, id: 1 });
    pointer(lane, 'pointerdown', { x: x + 200, id: 2 });
    pointer(lane, 'pointermove', { x: x + 300, id: 2 });
    pointer(lane, 'pointerup', { x: x + 300, id: 2 });
    pointer(lane, 'pointerup', { x, id: 1 });

    expect(cursor.state().selection).toBeNull();
    expect(two.view().to - two.view().from).toBeLessThan(40);
  });

  it('scrubs the cursor on a one-finger drag, and selects its band on release', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    const from = two.view().from;

    pointer(lane, 'pointerdown', { x: 300 });
    pointer(lane, 'pointermove', { x: 150 });
    // 150 px of 600 is a quarter of the 40-sample window.
    expect(cursor.state().index).toBe(from + 10);
    pointer(lane, 'pointermove', { x: 450 });
    expect(cursor.state().index).toBe(from + 30);
    expect(cursor.state().selection).toBeNull();
    pointer(lane, 'pointerup', { x: 450 });

    expect(two.view().from).toBe(from);
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 130, to: 134, classIndex: 0 });
  });

  it('pans on a two-finger drag', () => {
    const { cursor } = attach();
    openLongZoomed(cursor);
    cursor.setIndex(281);
    const from = two.view().from;

    pointer(lane, 'pointerdown', { x: 200, id: 1 });
    pointer(lane, 'pointerdown', { x: 400, id: 2 });
    pointer(lane, 'pointermove', { x: 50, id: 1 });
    pointer(lane, 'pointermove', { x: 250, id: 2 });
    pointer(lane, 'pointerup', { x: 50, id: 1 });
    pointer(lane, 'pointerup', { x: 250, id: 2 });

    // The fingers kept their spacing, so the span holds and the window
    // follows their midpoint 150 px (10 samples) to the right.
    expect(two.view().to - two.view().from).toBeCloseTo(40);
    expect(two.view().from).toBeCloseTo(from + 10);
    expect(cursor.state().selection).toBeNull();
  });

  it('scrubs on a mouse drag, never pans, and selects on release (SNOW-1032)', () => {
    const { cursor } = attach();
    openLongZoomed(cursor);

    pointer(lane, 'pointerdown', { x: 300, pointerType: 'mouse' });
    pointer(lane, 'pointermove', { x: 150, pointerType: 'mouse' });
    // 150 px of 600 is a quarter of the 40-sample window 280–320.
    expect(cursor.state().index).toBe(290);
    pointer(lane, 'pointerup', { x: 150, pointerType: 'mouse' });

    expect(two.view()).toEqual({ from: 280, to: 320 });
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 290, to: 294, classIndex: 0 });
  });

  it('stops a pan at the leg\'s end', () => {
    const { cursor } = attach();
    openLongZoomed(cursor);

    // A mouse pans with the horizontal wheel, not by dragging.
    lane.dispatchEvent(new WheelEvent('wheel', {
      bubbles: true, cancelable: true, deltaX: -50000, deltaY: 0,
    }));

    expect(two.view()).toEqual({ from: 140, to: 180 });
  });
});

describe('releasing a drag selects (SNOW-1032)', () => {
  /**
   * The default angles with 296–309 alternating 20° and 32° sample by
   * sample: fourteen one-segment bands, 1.875 px each on leg 3 fitted.
   */
  const STRIPED = ANGLES.map((angle, i) => (i >= 296 && i <= 309 ? (i % 2 ? 32 : 20) : angle));
  /** Leg 3 fitted: 320 samples across 600 px from sample 140. */
  const xOfSample = (index) => (index - 140) * (600 / 320) + 0.9;

  it('draws a one-segment band as its own rect, nothing merged', () => {
    const { cursor } = attach({ angles: STRIPED });
    cursor.openLeg(LEGS[2]);

    const rect = bandRects().find((r) => r.getAttribute('data-from') === '301');
    expect(rect.getAttribute('data-to')).toBe('301');
    expect(Number(rect.getAttribute('width'))).toBeLessThan(2);
    // Its neighbours are one segment each too.
    for (const from of ['300', '302']) {
      const other = bandRects().find((r) => r.getAttribute('data-from') === from);
      expect(other.getAttribute('data-to')).toBe(from);
    }
  });

  for (const pointerType of ['touch', 'mouse']) {
    it(`selects the one-segment band holding the index a ${pointerType} drag ends on`, () => {
      const { cursor } = attach({ angles: STRIPED });
      cursor.openLeg(LEGS[2]);

      pointer(lane, 'pointerdown', { x: 100, y: 5, pointerType });
      pointer(lane, 'pointermove', { x: 200, y: 5, pointerType });
      pointer(lane, 'pointermove', { x: xOfSample(301), y: 5, pointerType });
      expect(cursor.state().index).toBe(301);
      pointer(lane, 'pointerup', { x: xOfSample(301), y: 5, pointerType });

      expect(cursor.state().selection).toEqual({ kind: 'band', from: 301, to: 301, classIndex: 1 });
      expect(two.view()).toEqual({ from: 140, to: 460 });
    });
  }

  it('selects the band, not a passage, when a drag ends in the passage foot', () => {
    const { cursor } = attach({ angles: STRIPED });
    cursor.openLeg(LEGS[2]);

    pointer(lane, 'pointerdown', { x: 100, y: 42 });
    pointer(lane, 'pointermove', { x: xOfSample(302), y: 42 });
    pointer(lane, 'pointerup', { x: xOfSample(302), y: 42 });

    expect(cursor.state().selection).toEqual({ kind: 'band', from: 302, to: 302, classIndex: 0 });
  });

  it('keeps the band selected when a drag is released onto it', () => {
    const { cursor } = attach({ angles: STRIPED });
    cursor.openLeg(LEGS[2]);
    cursor.select({ kind: 'band', from: 301, to: 301, classIndex: 1 });

    pointer(lane, 'pointerdown', { x: 100 });
    pointer(lane, 'pointermove', { x: xOfSample(301) });
    pointer(lane, 'pointerup', { x: xOfSample(301) });

    expect(cursor.state().selection).toEqual({ kind: 'band', from: 301, to: 301, classIndex: 1 });
  });

  it('selects nothing when a drag is cancelled rather than released', () => {
    const { cursor } = attach({ angles: STRIPED });
    cursor.openLeg(LEGS[2]);

    pointer(lane, 'pointerdown', { x: 100 });
    pointer(lane, 'pointermove', { x: xOfSample(301) });
    pointer(lane, 'pointercancel', { x: xOfSample(301) });

    expect(cursor.state().index).toBe(301);
    expect(cursor.state().selection).toBeNull();
  });

  it('still toggles on a tap, and picks by nearest extent within 22 px', () => {
    const { cursor } = attach({ angles: STRIPED });
    cursor.openLeg(LEGS[2]);

    now += 1000;
    pointer(lane, 'pointerdown', { x: xOfSample(301), y: 5 });
    pointer(lane, 'pointerup', { x: xOfSample(301), y: 5 });
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 301, to: 301, classIndex: 1 });

    now += 1000;
    pointer(lane, 'pointerdown', { x: xOfSample(301), y: 5 });
    pointer(lane, 'pointerup', { x: xOfSample(301), y: 5 });
    expect(cursor.state().selection).toBeNull();
  });
});

describe('the selection box (SNOW-1032)', () => {
  it('draws a one-sample selection at least 12 px wide', () => {
    const { cursor } = attach();
    // Leg 3 opens fitted: 320 samples across 600 px, 1.875 px each.
    cursor.openLeg(LEGS[2]);

    cursor.select({ kind: 'passage', from: 300, to: 300 });

    const box = lane.querySelector('[data-route-rail-two-selection]');
    expect(Number(box.getAttribute('width'))).toBe(12);
    // Centred on sample 300's 1.875 px, 300 to 301.875 px.
    expect(Number(box.getAttribute('x'))).toBeCloseTo(300.9375 - 6);
  });

  it('dims the lane either side of the box, and nowhere without a selection', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    expect(lane.querySelectorAll('[data-route-rail-two-dim]')).toHaveLength(0);

    cursor.select({ kind: 'band', from: 110, to: 114, classIndex: 0 });

    const left = lane.querySelector('[data-route-rail-two-dim="left"]');
    const right = lane.querySelector('[data-route-rail-two-dim="right"]');
    expect(Number(left.getAttribute('x'))).toBe(0);
    expect(Number(left.getAttribute('width'))).toBe(150);
    expect(Number(right.getAttribute('x'))).toBe(225);
    expect(Number(right.getAttribute('width'))).toBe(375);
    for (const veil of [left, right]) {
      expect(veil.getAttribute('fill')).toBe('var(--color-card)');
      expect(veil.getAttribute('fill-opacity')).toBe('0.62');
      expect(veil.getAttribute('pointer-events')).toBe('none');
      expect(veil.getAttribute('height')).toBe('44');
    }
  });
});

describe('zoom', () => {
  it('halves and doubles the span on −/+, disabling each at its limit', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    const zoomIn = row.querySelector('[data-route-rail-two-zoom="in"]');
    const zoomOut = row.querySelector('[data-route-rail-two-zoom="out"]');
    const span = () => two.view().to - two.view().from;

    zoomIn.click();
    expect(span()).toBe(20);
    zoomIn.click();
    zoomIn.click();
    expect(span()).toBe(6);
    expect(zoomIn.disabled).toBe(true);

    zoomOut.click();
    zoomOut.click();
    zoomOut.click();
    expect(span()).toBe(40);
    expect(zoomOut.disabled).toBe(true);
    expect(zoomIn.disabled).toBe(false);
  });

  it('zooms on Ctrl-wheel', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    lane.dispatchEvent(new WheelEvent('wheel', {
      bubbles: true,
      cancelable: true,
      ctrlKey: true,
      deltaY: -100,
      clientX: 300,
    }));

    expect(two.view().to - two.view().from).toBeCloseTo(40 * Math.exp(-1));
  });

  it('zooms on the −/+ keys', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    lane.dispatchEvent(new KeyboardEvent('keydown', { key: '+', bubbles: true }));

    expect(two.view().to - two.view().from).toBe(20);
  });
});

describe('keys', () => {
  it('moves the cursor with the arrows and selects the band under it', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(104);

    lane.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(cursor.state().index).toBe(105);

    lane.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 105, to: 109, classIndex: 1 });
  });
});

describe('the cursor point (SNOW-1019)', () => {
  it('is the cursor at the band strip\'s top, and null while empty', () => {
    const { cursor } = attach();
    vi.spyOn(lane, 'getBoundingClientRect')
      .mockReturnValue({ left: 20, top: 200, right: 620, bottom: 244, width: 600, height: 44 });
    expect(two.cursorPoint()).toBeNull();

    cursor.openLeg(LEGS[1]);
    cursor.setIndex(110);
    const point = two.cursorPoint();

    // Sample 110 of the 100–140 window: its centre is 10.5/40 across.
    expect(point.x).toBeCloseTo(20 + (10.5 / 40) * 600);
    // The lane is drawn at ROWS.height, so the band strip's top is 1:1.
    expect(point.y).toBe(200 + self.pwaRouteRailTwoCore.ROWS.bandTop);

    cursor.closeLeg();
    expect(two.cursorPoint()).toBeNull();
  });

  it('announces each redraw, so the leader line follows a pan', () => {
    const heard = vi.fn();
    document.addEventListener('snowdesk:route-rail-two-drawn', heard);
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    row.querySelector('[data-route-rail-two-zoom="in"]').click();

    expect(heard).toHaveBeenCalled();
    document.removeEventListener('snowdesk:route-rail-two-drawn', heard);
  });
});

describe('the rows (SNOW-1019, SNOW-1024)', () => {
  it('draws no profile and no distance scale: bands, wedges and passages only', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    expect(lane.querySelectorAll('path')).toHaveLength(0);
    // Every line in the lane is a wedge's ground line or the cursor.
    lane.querySelectorAll('line').forEach((line) => {
      expect(
        line.classList.contains('route-rail-two-wedge') || line.hasAttribute('data-route-rail-two-cursor'),
      ).toBe(true);
    });
    expect(row.querySelectorAll('[data-route-rail-two-ticks]')).toHaveLength(0);
    expect(lane.getAttribute('viewBox')).toBe(`0 0 600 ${self.pwaRouteRailTwoCore.ROWS.height}`);
  });

  it('lays a 44 px lane: band 10, a 4 px gap, the wedges 14–40, passages 4 px under them', () => {
    const rows = self.pwaRouteRailTwoCore.ROWS;

    expect(rows.height).toBe(44);
    expect(rows.bandTop).toBe(0);
    expect(rows.bandHeight).toBe(10);
    // A capped wedge spans 14–40; the passage bars sit below it (SNOW-1031).
    expect(rows.ribbonHalf).toBe(self.pwaBankRibbonCore.CAP_PX);
    expect(rows.ribbonY - rows.ribbonHalf).toBe(14);
    expect(rows.ribbonY + rows.ribbonHalf).toBe(40);
    expect(rows.passageTop).toBe(40);
    expect(rows.passageHeight).toBe(4);
    expect(rows.passageTop + rows.passageHeight).toBe(rows.height);
  });
});

describe('the empty state (SNOW-1024)', () => {
  it('shows the placeholder with zoom, close and the readout hidden', () => {
    attach();

    expect(title.textContent).toBe('Select a route leg to view terrain');
    expect(title.classList.contains('text-text-2')).toBe(true);
    expect(row.querySelector('[data-route-rail-two-figures]').textContent).toBe('');
    expect(zoomOutButton.hidden).toBe(true);
    expect(zoomInButton.hidden).toBe(true);
    expect(closeButton.hidden).toBe(true);
    expect(readoutBox.hidden).toBe(true);
    expect(lane.children).toHaveLength(0);
    expect(lane.getAttribute('tabindex')).toBe('-1');
    expect(lane.getAttribute('aria-hidden')).toBe('true');
  });

  it('restores them when a leg opens', () => {
    const { cursor } = attach();

    cursor.openLeg(LEGS[1]);

    expect(title.textContent).toBe('Leg 2 — descent');
    expect(title.classList.contains('text-text-1')).toBe(true);
    expect(title.classList.contains('text-text-2')).toBe(false);
    expect(zoomOutButton.hidden).toBe(false);
    expect(zoomInButton.hidden).toBe(false);
    expect(closeButton.hidden).toBe(false);
    expect(readoutBox.hidden).toBe(false);
    expect(lane.getAttribute('tabindex')).toBe('0');
    expect(lane.hasAttribute('aria-hidden')).toBe(false);
  });
});

describe('the leg picker (SNOW-1033)', () => {
  it('lays one button per leg on rail one\'s scale, named from the strings', () => {
    attach();

    expect(legsLayer.hidden).toBe(false);
    const buttons = legButtons();
    expect(buttons).toHaveLength(3);
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Leg 1 — climb',
      'Leg 2 — descent',
      'Leg 3 — climb',
    ]);
    expect(buttons.map((b) => b.getAttribute('data-climbing'))).toEqual(['true', 'false', 'true']);
    expect(buttons.every((b) => b.type === 'button')).toBe(true);
    // Leg 1 is samples 0–99 of 460, leg 2 100–139 and leg 3 the rest;
    // 1 px inset each side.
    expect(buttons[0].style.left).toBe('calc(0% + 1px)');
    expect(buttons[0].style.width).toBe('calc(21.7391% - 2px)');
    expect(buttons[1].style.left).toBe('calc(21.7391% + 1px)');
    expect(buttons[1].style.width).toBe('calc(8.6957% - 2px)');
    expect(buttons[2].style.left).toBe('calc(30.4348% + 1px)');
    expect(buttons[2].style.width).toBe('calc(69.5652% - 2px)');
  });

  it('shows the leg number as the only text, in a 24 px fill', () => {
    attach();

    const [first] = legButtons();
    expect(first.textContent).toBe('1');
    const fill = first.querySelector('[data-route-rail-two-leg-fill]');
    expect(fill.classList.contains('h-6')).toBe(true);
    expect(first.classList.contains('h-11')).toBe(true);
  });

  it('opens the pressed leg through the cursor', () => {
    // The cursor is frozen, so the spy wraps a copy of its methods.
    const real = self.pwaRouteCursorCore.createRouteCursor(N);
    const openLeg = vi.fn(real.openLeg);
    const cursor = { ...real, openLeg };
    two.attach({
      cursor,
      slope: { angles: ANGLES, banks: BANKS, passages: [] },
      profile: self.pwaElevationProfileCore.readProfile(track(200)),
      legs: LEGS,
      sampleCount: N,
      spanM: SPAN_M,
    });

    legButtons()[1].click();

    expect(openLeg).toHaveBeenCalledWith(LEGS[1]);
    expect(cursor.state().openLeg).toMatchObject({ from: 100, to: 139 });
    expect(title.textContent).toBe('Leg 2 — descent');
    // It opens fitted, as a leg opened anywhere else does (SNOW-1031).
    expect(two.view()).toEqual({ from: 100, to: 140 });
  });

  it('opens the leg under a press in a gap between segments', () => {
    const { cursor } = attach();

    // Legs 1 and 2 meet at 130.4 px on the 600 px fallback lane (sample
    // 100 of 460), and leg 2's button starts 1 px later: 131 px is in the
    // 2 px gap, on the layer rather than on a button. The leg holding it
    // wins over the nearer edge of its neighbour, as a band does
    // (nearestRange).
    legsLayer.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 131 }));

    expect(cursor.state().openLeg).toMatchObject({ from: 100, to: 139 });
  });

  it('picks the nearest leg within 22 px of a press, and none farther', () => {
    const { cursor } = attach();

    legsLayer.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 630 }));
    expect(cursor.state().openLeg).toBeNull();

    legsLayer.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 615 }));
    expect(cursor.state().openLeg).toMatchObject({ from: 140, to: 459 });
  });

  it('opens the leg fitted on a double-click, and does not zoom it', () => {
    const { cursor } = attach();

    // The first click opens leg 3 from the picker; the second lands on the
    // lane the picker has just uncovered, and the browser fires dblclick.
    legButtons()[2].click();
    pointer(lane, 'pointerdown', { x: 300, pointerType: 'mouse' });
    pointer(lane, 'pointerup', { x: 300, pointerType: 'mouse' });
    lane.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, clientX: 300 }));

    expect(cursor.state().openLeg).toMatchObject({ from: 140, to: 459 });
    expect(two.view()).toEqual({ from: 140, to: 460 });
  });

  it('hides the picker with a leg open and restores it on close', () => {
    const { cursor } = attach();

    cursor.openLeg(LEGS[0]);
    expect(legsLayer.hidden).toBe(true);

    cursor.closeLeg();
    expect(legsLayer.hidden).toBe(false);
    expect(legButtons()).toHaveLength(3);
  });

  it('offers no picker for a route without legs, and none once detached', () => {
    const cursor = self.pwaRouteCursorCore.createRouteCursor(N);
    two.attach({
      cursor,
      slope: { angles: ANGLES, banks: BANKS, passages: [] },
      profile: self.pwaElevationProfileCore.readProfile(track(200)),
      legs: [],
      sampleCount: N,
      spanM: SPAN_M,
    });
    expect(legsLayer.hidden).toBe(true);
    expect(legButtons()).toHaveLength(0);

    attach();
    two.detach();
    expect(legsLayer.hidden).toBe(true);
  });
});

describe('focus on close (SNOW-1033)', () => {
  it('moves focus from rail two\'s × to the closed leg\'s segment', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    closeButton.focus();

    closeButton.click();

    expect(document.activeElement).toBe(legButtons()[1]);
  });

  it('moves focus from the lane or a zoom button the same way', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[0]);
    lane.focus();
    cursor.closeLeg();
    expect(document.activeElement).toBe(legButtons()[0]);

    cursor.openLeg(LEGS[1]);
    zoomInButton.focus();
    cursor.closeLeg();
    expect(document.activeElement).toBe(legButtons()[1]);
  });

  it('falls back to the first segment when the closed leg has none', () => {
    const { cursor } = attach();
    // A leg the picker does not offer: not one of the route's legs.
    cursor.openLeg({ i: 9, from: 40, to: 60, climbing: true });
    closeButton.focus();

    cursor.closeLeg();

    expect(document.activeElement).toBe(legButtons()[0]);
  });

  it('leaves focus alone for a close from outside rail two', () => {
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    outside.focus();

    cursor.closeLeg();

    expect(document.activeElement).toBe(outside);
    outside.remove();
  });
});

describe('the opening motion (SNOW-1033)', () => {
  /** @type {?{calls: Array<object>, restore: Function}} */
  let stub = null;

  /**
   * Stand in for WAAPI, which jsdom lacks: record each call and hand back
   * an animation the test finishes by hand.
   *
   * @returns {{calls: Array<object>, restore: Function}}
   */
  function stubAnimate() {
    const calls = [];
    Element.prototype.animate = function animate(keyframes, timing) {
      const animation = {
        el: this,
        keyframes,
        timing,
        onfinish: null,
        cancelled: false,
        cancel() { this.cancelled = true; },
        finish() { if (this.onfinish) this.onfinish(); },
      };
      calls.push(animation);
      return animation;
    };
    return {
      calls,
      restore() { delete Element.prototype.animate; },
    };
  }

  /** @param {Array<object>} calls */
  const finishAll = (calls) => calls.slice().forEach((a) => a.finish());

  /** @returns {Array<Element>} The ghosts in the picker layer. */
  const ghosts = () => Array.from(legsLayer.querySelectorAll('[data-route-rail-two-ghost]'));

  afterEach(() => {
    if (stub) stub.restore();
    stub = null;
    window.matchMedia = (query) => ({
      matches: false,
      media: query,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    });
  });

  it('plays press, stretch and fill from the pressed segment', () => {
    const { onResize } = attach();
    stub = stubAnimate();

    legButtons()[1].click();

    const timings = stub.calls.map((a) => a.timing);
    expect(timings).toContainEqual({ delay: 0, duration: 80, easing: 'linear', fill: 'both' });
    expect(timings).toContainEqual({ delay: 80, duration: 140, easing: 'ease-out', fill: 'both' });
    expect(Math.max(...timings.map((t) => t.delay + t.duration))).toBe(340);
    // The ghost stretches from the segment's slot to the lane.
    const [ghost] = ghosts();
    const stretch = stub.calls.find((a) => a.el === ghost && a.keyframes[0].left);
    expect(stretch.keyframes).toEqual([
      { left: 'calc(21.7391% + 1px)', width: 'calc(8.6957% - 2px)' },
      { left: '0px', width: '100%' },
    ]);
    // The leg is drawn under it already; the map hears the height at the end.
    expect(title.textContent).toBe('Leg 2 — descent');
    expect(legsLayer.hidden).toBe(false);
    expect(onResize).toHaveBeenCalledTimes(1);

    finishAll(stub.calls);

    expect(ghosts()).toHaveLength(0);
    expect(row.querySelectorAll('[data-route-rail-two-snapshot]')).toHaveLength(0);
    expect(legsLayer.hidden).toBe(true);
    expect(stub.calls.every((a) => a.cancelled)).toBe(true);
    expect(onResize).toHaveBeenCalledTimes(2);
  });

  it('keeps the played-out picker and every copy out of the tab order until the end', () => {
    attach();
    stub = stubAnimate();

    legButtons()[1].click();

    // Mid-motion: the picker is shown only to be played out, and each copy
    // (the ghost, the header and control snapshots) is inert.
    expect(legsLayer.hasAttribute('inert')).toBe(true);
    const copies = Array.from(row.querySelectorAll('[data-route-rail-two-snapshot]'));
    expect(copies.length).toBeGreaterThan(0);
    copies.forEach((copy) => expect(copy.hasAttribute('inert')).toBe(true));

    finishAll(stub.calls);

    expect(legsLayer.hasAttribute('inert')).toBe(false);
  });

  it('leaves the picker live while a leg closes, so focus can return to it', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    stub = stubAnimate();

    cursor.closeLeg();

    expect(legsLayer.hidden).toBe(false);
    expect(legsLayer.hasAttribute('inert')).toBe(false);
    Array.from(row.querySelectorAll('[data-route-rail-two-snapshot]'))
      .forEach((copy) => expect(copy.hasAttribute('inert')).toBe(true));
  });

  it('plays the same motion for a leg opened from rail one or the map', () => {
    const { cursor } = attach();
    stub = stubAnimate();

    cursor.openLeg(LEGS[0]);

    expect(ghosts()).toHaveLength(1);
    const stretch = stub.calls.find((a) => a.el === ghosts()[0] && a.keyframes[0].left);
    expect(stretch.keyframes[0]).toEqual({ left: 'calc(0% + 1px)', width: 'calc(21.7391% - 2px)' });
  });

  it('closes in reverse: fill, stretch, then press', () => {
    const { cursor, onResize } = attach();
    cursor.openLeg(LEGS[1]);
    stub = stubAnimate();
    const before = onResize.mock.calls.length;

    cursor.closeLeg();

    const timings = stub.calls.map((a) => a.timing);
    expect(timings).toContainEqual({ delay: 120, duration: 140, easing: 'ease-in', fill: 'both' });
    expect(timings).toContainEqual({ delay: 260, duration: 80, easing: 'linear', fill: 'both' });
    const stretch = stub.calls.find((a) => a.el === ghosts()[0] && a.keyframes[0].left);
    expect(stretch.keyframes[1]).toEqual({
      left: 'calc(21.7391% + 1px)',
      width: 'calc(8.6957% - 2px)',
    });
    expect(row.hasAttribute('data-empty')).toBe(true);
    expect(onResize).toHaveBeenCalledTimes(before);

    finishAll(stub.calls);

    expect(ghosts()).toHaveLength(0);
    expect(row.querySelectorAll('[data-route-rail-two-snapshot]')).toHaveLength(0);
    expect(legsLayer.hidden).toBe(false);
    expect(legButtons().every((b) => b.style.opacity === '')).toBe(true);
    expect(onResize).toHaveBeenCalledTimes(before + 1);
  });

  it('cancels a running motion cleanly when the leg changes mid-way', () => {
    const { cursor } = attach();
    stub = stubAnimate();
    legButtons()[1].click();
    const opening = stub.calls.slice();

    cursor.closeLeg();

    expect(opening.every((a) => a.cancelled)).toBe(true);
    // Only the closing motion's ghost is left, and nothing of the opening's.
    expect(ghosts()).toHaveLength(1);
    expect(row.style.overflow).toBe('');
    finishAll(stub.calls);
    expect(ghosts()).toHaveLength(0);
    expect(legButtons()).toHaveLength(3);
  });

  it('cancels the motion on detach', () => {
    attach();
    stub = stubAnimate();
    legButtons()[0].click();

    two.detach();

    expect(stub.calls.every((a) => a.cancelled)).toBe(true);
    expect(ghosts()).toHaveLength(0);
    expect(row.hidden).toBe(true);
  });

  it('cuts straight to the end state when motion is reduced', () => {
    const { onResize } = attach();
    stub = stubAnimate();
    window.matchMedia = (query) => ({ matches: query.includes('reduce'), media: query });

    legButtons()[1].click();

    expect(stub.calls).toHaveLength(0);
    expect(ghosts()).toHaveLength(0);
    expect(legsLayer.hidden).toBe(true);
    expect(onResize).toHaveBeenCalledTimes(2);
  });

  it('cuts straight to the end state without WAAPI', () => {
    const { cursor } = attach();

    legButtons()[1].click();
    expect(ghosts()).toHaveLength(0);
    expect(legsLayer.hidden).toBe(true);

    cursor.closeLeg();
    expect(ghosts()).toHaveLength(0);
    expect(legsLayer.hidden).toBe(false);
  });
});

describe('the wedges (SNOW-1031)', () => {
  /** Open leg 2 zoomed in on sample 103, with the given banks. */
  function openZoomed(banks) {
    const { cursor } = attach({ banks });
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(103);
    row.querySelector('[data-route-rail-two-zoom="in"]').click();
  }

  /** The drawn marks of one kind ('up', 'down' or 'ground') at a sample. */
  function marks(kind, index) {
    return lane.querySelectorAll(`.route-rail-two-wedge[data-wedge="${kind}"][data-index="${index}"]`);
  }

  it('draws no glyph for a null bank', () => {
    const banks = BANKS.slice();
    banks[103] = null;
    openZoomed(banks);

    const drawn = Array.from(lane.querySelectorAll('.route-rail-two-wedge[data-wedge="ground"]')).map(
      (line) => Number(line.getAttribute('data-index')),
    );
    expect(drawn).toContain(102);
    expect(drawn).toContain(104);
    expect(drawn).not.toContain(103);
  });

  it('fills the uphill wedge solid and the downhill one pale, in tokens', () => {
    const banks = BANKS.slice();
    banks[102] = 40;
    openZoomed(banks);

    const [up] = marks('up', 102);
    const [down] = marks('down', 102);
    const [ground] = marks('ground', 102);
    expect(up.tagName).toBe('polygon');
    expect(up.getAttribute('fill')).toBe('var(--color-text-2)');
    expect(up.hasAttribute('fill-opacity')).toBe(false);
    expect(down.getAttribute('fill')).toBe('var(--color-text-2)');
    expect(down.getAttribute('fill-opacity')).toBe('0.3');
    expect(ground.tagName).toBe('line');
    expect(ground.getAttribute('stroke')).toBe('var(--color-text-1)');
    for (const mark of [up, down, ground]) {
      expect(mark.getAttribute('pointer-events')).toBe('none');
    }
  });

  it('puts the pale wedge on the side the ground falls away to', () => {
    const banks = BANKS.slice();
    banks[102] = 30;
    banks[104] = -30;
    openZoomed(banks);

    const xs = (el) => el.getAttribute('points').split(' ').map((p) => Number(p.split(',')[0]));
    const centre = (index) => {
      const g = marks('ground', index)[0];
      return (Number(g.getAttribute('x1')) + Number(g.getAttribute('x2'))) / 2;
    };
    expect(Math.min(...xs(marks('down', 102)[0]))).toBeGreaterThanOrEqual(centre(102) - 0.01);
    expect(Math.max(...xs(marks('down', 104)[0]))).toBeLessThanOrEqual(centre(104) + 0.01);
  });

  it('draws the fall line as a flat ground line with no fills', () => {
    const banks = BANKS.slice();
    banks[102] = 0;
    openZoomed(banks);

    expect(marks('up', 102)).toHaveLength(0);
    expect(marks('down', 102)).toHaveLength(0);
    const [ground] = marks('ground', 102);
    expect(ground.getAttribute('y1')).toBe(ground.getAttribute('y2'));
  });

  it('keeps every glyph inside the bank row, clear of the passages', () => {
    const banks = BANKS.slice();
    banks[102] = 80;
    banks[104] = -80;
    openZoomed(banks);

    const rows = self.pwaRouteRailTwoCore.ROWS;
    lane.querySelectorAll('.route-rail-two-wedge[data-wedge="ground"]').forEach((line) => {
      for (const y of [line.getAttribute('y1'), line.getAttribute('y2')].map(Number)) {
        expect(y).toBeGreaterThanOrEqual(14);
        expect(y).toBeLessThanOrEqual(40);
        expect(y).toBeLessThanOrEqual(rows.passageTop);
      }
    });
  });
});

describe('the bank row follows the zoom (SNOW-1031)', () => {
  /** @returns {?Element} The placeholder group, or null. */
  const placeholder = () => lane.querySelector('[data-route-rail-two-bank-placeholder]');
  /** @returns {number} Glyph ground lines drawn. */
  const glyphCount = () => lane.querySelectorAll('.route-rail-two-wedge[data-wedge="ground"]').length;

  /**
   * Two touch taps at x, `gap` ms apart. The first goes down on `target`;
   * the second on the lane, since the first tap's redraw replaces it.
   *
   * @param {number} x
   * @param {number} [gap]
   * @param {Element} [target]
   */
  function doubleTap(x, gap = 100, target = lane) {
    now += 1000;
    pointer(target, 'pointerdown', { x });
    pointer(lane, 'pointerup', { x });
    now += gap;
    pointer(lane, 'pointerdown', { x: x + 10 });
    pointer(lane, 'pointerup', { x: x + 10 });
  }

  it('opens a long leg fitted, with the placeholder in place of glyphs', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[2]);

    expect(two.view()).toEqual({ from: 140, to: 460 });
    expect(glyphCount()).toBe(0);
    const group = placeholder();
    expect(group).not.toBeNull();
    expect(group.getAttribute('pointer-events')).toBe('none');
    expect(group.querySelector('text').textContent).toBe('Zoom in to see the bank');
    expect(group.querySelector('text').getAttribute('fill')).toBe('var(--color-text-3)');
    const line = group.querySelector('line');
    expect(line.getAttribute('stroke-dasharray')).toBe('3 3');
    expect(line.getAttribute('y1')).toBe(String(self.pwaRouteRailTwoCore.ROWS.ribbonY));
    expect(group.querySelector('rect').getAttribute('fill')).toBe('var(--color-card)');
    // The bands are still drawn per segment, and the passages marked.
    expect(bandRects().length).toBeGreaterThan(0);
  });

  it('draws glyphs, not the placeholder, on a leg that resolves fitted', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    expect(placeholder()).toBeNull();
    expect(glyphCount()).toBe(40);
  });

  it('zooms to the resolved span on a double-click, and back on another', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[2]);
    // A mouse press first, as a real double-click has.
    pointer(lane, 'pointerdown', { x: 300, pointerType: 'mouse' });
    pointer(lane, 'pointerup', { x: 300, pointerType: 'mouse' });

    lane.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, clientX: 300 }));

    // resolveSpan at 600 px is 180, centred on sample 300.
    expect(two.view()).toEqual({ from: 210, to: 390 });
    expect(placeholder()).toBeNull();
    expect(glyphCount()).toBeGreaterThan(0);

    lane.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, clientX: 300 }));

    expect(two.view()).toEqual({ from: 140, to: 460 });
    expect(placeholder()).not.toBeNull();
  });

  it('zooms on a touch double-tap without toggling the selection', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[2]);
    const band = bandRects().find((rect) => rect.getAttribute('data-from') === '300');
    const x = Number(band.getAttribute('x')) + 0.5;

    doubleTap(x, 100, band);

    // Fitted, the 32° band 295–299 ends within 22 px of the tap on the 20°
    // band 300–304, and a tap takes the steepest band in reach.
    expect(cursor.state().selection)
      .toEqual({ kind: 'band', from: 295, to: 299, classIndex: 1 });
    expect(two.view().to - two.view().from).toBe(180);

    now += 1000;
    doubleTap(300);
    expect(two.view()).toEqual({ from: 140, to: 460 });
  });

  it('treats two taps too far apart in time as two taps', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[2]);

    doubleTap(300, 400);

    expect(two.view()).toEqual({ from: 140, to: 460 });
  });

  it('marks a passage 4 px tall and at least 6 px wide at every zoom', () => {
    const { cursor } = attach({ passages: [{ from: 300, to: 300, m: 50, fall_line: 'across' }] });
    cursor.openLeg(LEGS[2]);

    // One 1.9 px sample, widened to 6 px.
    let bar = lane.querySelector('.route-rail-two-passage');
    expect(bar.getAttribute('height')).toBe('4');
    expect(bar.getAttribute('y')).toBe('40');
    expect(Number(bar.getAttribute('width'))).toBe(6);

    zoomInButton.click();
    zoomInButton.click();
    zoomInButton.click();
    // 15 px a sample at the 40-sample window: its real extent.
    bar = lane.querySelector('.route-rail-two-passage');
    expect(Number(bar.getAttribute('width'))).toBe(15);
  });
});

describe('the readout (SNOW-1024)', () => {
  it('reads the terrain, then the slope and the bank', () => {
    // Sample 101: 20° ground banked 20° to the right, so the track runs
    // straight across it.
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(101);

    expect(readoutLines()).toEqual(['Traverse · falls away right', '20° slope · 20° bank']);
    expect(lane.getAttribute('aria-valuetext')).toContain(
      'Traverse · falls away right. 20° slope · 20° bank',
    );
  });

  it('reads the fall line on either leg', () => {
    const banks = BANKS.slice();
    banks[101] = 0;
    banks[5] = 0;
    const { cursor } = attach({ banks });
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(101);
    expect(readoutLines()).toEqual(['Fall line', '20° slope · 0° bank']);

    cursor.openLeg(LEGS[0]);
    cursor.setIndex(5);
    expect(readoutLines()).toEqual(['Fall line', '32° slope · 0° bank']);
  });

  it('says which way the ground falls away, with the bank unsigned', () => {
    // 20° ground banked 12° is 35.7° off the fall line.
    const banks = BANKS.slice();
    banks[101] = -12;
    const { cursor } = attach({ banks });
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(101);

    expect(readoutLines()).toEqual(['Ground falls away left', '20° slope · 12° bank']);
  });

  it('reads gentle ground by the leg\'s direction', () => {
    const angles = ANGLES.slice();
    angles[101] = 7;
    angles[1] = 7;
    const { cursor } = attach({ angles });
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(101);
    expect(readoutLines()).toEqual(['Gentle descent', '7° slope · 20° bank']);

    cursor.openLeg(LEGS[0]);
    cursor.setIndex(1);
    expect(readoutLines()).toEqual(['Gentle ascent', '7° slope · 20° bank']);
  });

  it('reads flat ground as flat', () => {
    const angles = ANGLES.slice();
    angles[101] = 3;
    const { cursor } = attach({ angles });
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(101);

    expect(readoutLines()).toEqual(['Flat', '3° slope']);
  });

  it('says the slope is not known where the angle is unknown', () => {
    const angles = ANGLES.slice();
    angles[101] = null;
    const { cursor } = attach({ angles });
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(101);

    expect(readoutLines()).toEqual(['slope not known']);
  });

  it('keeps the slope but not the terrain where only the bank is unknown', () => {
    const banks = BANKS.slice();
    banks[101] = null;
    const { cursor } = attach({ banks });
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(101);

    expect(readoutLines()).toEqual(['20° slope']);
  });

  it('reads a band as its length to the nearest 25 m and its class, on one line', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    // Five 50 m samples of 32° ground.
    cursor.select({ kind: 'band', from: 105, to: 109 });

    expect(readoutLines()).toEqual(['250 m 30–35°']);
  });

  it('offers the hint with nothing under the cursor, spanning the lane so it wraps', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    expect(readoutLines()).toEqual([
      'Drag to read a point. Tap a band or passage to select it.',
    ]);
    expect(readout.classList.contains('text-left')).toBe(true);
    expect(readout.classList.contains('inset-x-0')).toBe(true);
    expect(readout.classList.contains('whitespace-nowrap')).toBe(false);
    expect(readout.style.left).toBe('');
    expect(stem.hidden).toBe(true);
  });

  it('steps left, centred and right with the cursor, the stem on the line', () => {
    // The 100–140 window across 600 px: 15 px a sample.
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(102);
    expect(readout.classList.contains('text-left')).toBe(true);
    expect(readout.classList.contains('-translate-x-1/2')).toBe(false);
    expect(parseFloat(readout.style.left)).toBe(37.5);
    expect(stem.hidden).toBe(false);
    expect(parseFloat(stem.style.left)).toBe(37);

    cursor.setIndex(120);
    expect(readout.classList.contains('text-center')).toBe(true);
    expect(readout.classList.contains('-translate-x-1/2')).toBe(true);
    expect(parseFloat(readout.style.left)).toBe(307.5);

    cursor.setIndex(138);
    expect(readout.classList.contains('text-right')).toBe(true);
    expect(readout.classList.contains('-translate-x-full')).toBe(true);
    expect(parseFloat(readout.style.left)).toBe(577.5);
  });

  it('centres a selection\'s readout under its box, with no stem', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    cursor.select({ kind: 'band', from: 115, to: 119 });

    // 115–120 of the 100–140 window is 225–300 px; its middle is 262.5.
    expect(readout.classList.contains('text-center')).toBe(true);
    expect(parseFloat(readout.style.left)).toBe(262.5);
    expect(stem.hidden).toBe(true);
  });
});
