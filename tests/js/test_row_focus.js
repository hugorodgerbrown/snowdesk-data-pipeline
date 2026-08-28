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
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

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
    focus = { point: vi.fn(), bounds: vi.fn() };
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
