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
 * one-finger drag scrubs the cursor while two fingers or a mouse drag pan
 * it, a null bank draws no tick, and the readout reads the
 * terrain under the cursor or the stretch selected, stepped left, centred
 * or right under its anchor (SNOW-1024).
 *
 * jsdom lays nothing out, so the lane measures 0 px and rail two falls
 * back to 600 px; a pointer's lane x is its clientX. Pointer events are
 * built as PointerEvent where jsdom has it and as a MouseEvent carrying a
 * `pointerId` where it does not.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

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
      <button type="button" data-route-rail-two-zoom="out" aria-label="Zoom out"></button>
      <button type="button" data-route-rail-two-zoom="in" aria-label="Zoom in"></button>
      <button type="button" data-route-rail-two-close aria-label="Close the leg"></button>
      <p data-route-rail-two-figures></p>
      <svg data-route-rail-two-lane role="slider" tabindex="0"></svg>
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

/** @returns {Array<string>} The readout's lines. */
const readoutLines = () => Array.from(readout.children).map((line) => line.textContent);

/** 300 samples over 15 km: 50 m a sample, so 2 km is 40 samples. */
const N = 300;
const SPAN_M = 15000;
const LEGS = [
  { i: 1, from: 0, to: 99, climbing: true },
  { i: 2, from: 100, to: 299, climbing: false },
];

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
 * Tap an element at its left edge plus one px.
 *
 * @param {Element} el A band or passage rect.
 */
function tap(el) {
  const x = Number(el.getAttribute('x')) + 1;
  pointer(el, 'pointerdown', { x });
  pointer(lane, 'pointerup', { x });
}

/** @returns {Array<Element>} The band rects drawn. */
function bandRects() {
  return Array.from(lane.querySelectorAll('.route-rail-two-band'));
}

afterEach(() => {
  two.detach();
});

describe('following the cursor', () => {
  it('draws openLeg with 2 km of ground and empties on closeLeg', () => {
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
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(200);

    // Sample 200's centre, 200.5, in the middle of a 40-sample window.
    expect(two.view()).toEqual({ from: 180.5, to: 220.5 });
  });

  it('still scrolls the least distance for an arrow-key step past the edge', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(139);
    expect(two.view()).toEqual({ from: 100, to: 140 });

    lane.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));

    expect(cursor.state().index).toBe(140);
    expect(two.view()).toEqual({ from: 101, to: 141 });
  });

  it('centres a selection from elsewhere that fits the window', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    cursor.select({ kind: 'passage', from: 200, to: 209 });

    // The range 200–210 centred in 40 samples.
    expect(two.view()).toEqual({ from: 185, to: 225 });
  });

  it('aligns a selection longer than the window with its left edge', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    cursor.select({ kind: 'band', from: 180, to: 260 });

    expect(two.view()).toEqual({ from: 180, to: 220 });
    expect(lane.querySelector('[data-route-rail-two-selection]')).not.toBeNull();
  });
});

describe('pressing a band or a passage', () => {
  it('selects a band on a tap, and a second tap clears it', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    const band = bandRects().find((rect) => rect.getAttribute('data-from') === '105');

    tap(band);
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 105, to: 109 });

    tap(bandRects().find((rect) => rect.getAttribute('data-from') === '105'));
    expect(cursor.state().selection).toBeNull();
  });

  it('selects a passage as a passage', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    tap(lane.querySelector('.route-rail-two-passage'));

    expect(cursor.state().selection).toEqual({ kind: 'passage', from: 110, to: 114 });
    expect(readoutLines()).toEqual(['No-fall passage · 250 m']);
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

  it('scrubs the cursor on a one-finger drag, as the idle hint says', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    const from = two.view().from;

    pointer(lane, 'pointerdown', { x: 300 });
    pointer(lane, 'pointermove', { x: 150 });
    // 150 px of 600 is a quarter of the 40-sample window.
    expect(cursor.state().index).toBe(from + 10);
    pointer(lane, 'pointermove', { x: 450 });
    expect(cursor.state().index).toBe(from + 30);
    pointer(lane, 'pointerup', { x: 450 });

    expect(two.view().from).toBe(from);
    expect(cursor.state().selection).toBeNull();
  });

  it('pans on a two-finger drag', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(101);
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

  it('pans on a mouse drag and selects nothing', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(101);

    pointer(lane, 'pointerdown', { x: 300, pointerType: 'mouse' });
    pointer(lane, 'pointermove', { x: 150, pointerType: 'mouse' });
    pointer(lane, 'pointerup', { x: 150, pointerType: 'mouse' });

    // 150 px of 600 is a quarter of the 40-sample window.
    expect(two.view().from).toBeCloseTo(110);
    expect(cursor.state().selection).toBeNull();
    // The cursor was pulled into the window after the pan.
    expect(cursor.state().index).toBe(110);
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
    zoomOut.click();
    zoomOut.click();
    zoomOut.click();
    expect(span()).toBe(200);
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
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 105, to: 109 });
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
  it('draws no profile and no distance scale: bands, ribbon and passages only', () => {
    const { cursor } = attach();
    cursor.openLeg(LEGS[1]);

    expect(lane.querySelectorAll('path')).toHaveLength(0);
    // Every line in the lane is a ribbon tick or the cursor; no tick marks.
    lane.querySelectorAll('line').forEach((line) => {
      expect(
        line.classList.contains('route-rail-two-tick') || line.hasAttribute('data-route-rail-two-cursor'),
      ).toBe(true);
    });
    expect(row.querySelectorAll('[data-route-rail-two-ticks]')).toHaveLength(0);
    expect(lane.getAttribute('viewBox')).toBe(`0 0 600 ${self.pwaRouteRailTwoCore.ROWS.height}`);
  });

  it('lays a 44 px lane: band 10, a 4 px gap, the bank row, then 4 px', () => {
    const rows = self.pwaRouteRailTwoCore.ROWS;

    expect(rows.height).toBe(44);
    expect(rows.bandTop).toBe(0);
    expect(rows.bandHeight).toBe(10);
    // The bank row runs 14–40: ribbon ticks, then the passage bars.
    expect(rows.ribbonY - rows.ribbonHalf).toBeGreaterThanOrEqual(14);
    expect(rows.passageTop).toBeGreaterThan(rows.ribbonY + rows.ribbonHalf);
    expect(rows.passageTop + rows.passageHeight).toBe(rows.height - 4);
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

describe('the ribbon', () => {
  it('draws no tick for a null bank', () => {
    const banks = BANKS.slice();
    banks[103] = null;
    const { cursor } = attach({ banks });
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(103);
    row.querySelector('[data-route-rail-two-zoom="in"]').click();

    const drawn = Array.from(lane.querySelectorAll('.route-rail-two-tick')).map((line) =>
      Number(line.getAttribute('data-index')),
    );
    expect(drawn).toContain(102);
    expect(drawn).toContain(104);
    expect(drawn).not.toContain(103);
  });

  it('inks a strong bank and mutes the rest', () => {
    const banks = BANKS.slice();
    banks[102] = 40;
    banks[104] = 5;
    const { cursor } = attach({ banks });
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(103);
    row.querySelector('[data-route-rail-two-zoom="in"]').click();

    const stroke = (index) =>
      lane.querySelector(`.route-rail-two-tick[data-index="${index}"]`).getAttribute('stroke');
    expect(stroke(102)).toBe('var(--color-text-1)');
    expect(stroke(104)).toBe('var(--color-text-3)');
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
