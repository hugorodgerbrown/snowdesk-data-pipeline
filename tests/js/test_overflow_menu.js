/*
 * tests/js/test_overflow_menu.js — Vitest unit tests for
 * static/js/overflow_menu.js (SNOW-645).
 *
 * The DOM half of includes/_overflow_menu.html — the "Manage downloads"
 * row's "…" kebab menu (Rename / Remove). Delegated and
 * instance-agnostic (see the module's own header): ANY number of
 * `[data-overflow-menu]` wrappers on the page are driven by ONE pair of
 * document-level listeners, which matters because map_downloads_manager.js
 * re-clones every row from its `<template>` on every render(), discarding
 * anything bound to the previous copy.
 *
 * `overflow_menu.js` is a browser IIFE with no exports — importing it for
 * side effects (it self-wires delegated `click`/`keydown`/`scroll`
 * listeners at import time) is enough, mirroring test_overlays.js's own
 * pattern for the same shape of module.
 *
 * SNOW-830 added three behaviours, each because the primitive could not
 * be used on a map panel without it, and each covered below:
 *
 *   - the menu is placed `fixed` from the trigger's own rect, so it
 *     escapes the panel's scroll container and the sheet's
 *     `overflow-hidden`;
 *   - it closes on scroll, because a `fixed` box does not travel with the
 *     row it was opened from;
 *   - its Escape handler runs in CAPTURE phase and stops propagation only
 *     when a menu is open, so one Escape closes the menu without also
 *     closing the sheet — but an Escape with no menu open still reaches
 *     the sheet's own bubble-phase handler (static/js/map_sheet.js).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/overflow_menu.js';

/**
 * One `[data-overflow-menu]` instance, mirroring
 * includes/_overflow_menu.html's rendered shape closely enough to drive
 * the module under test — the trigger's `[data-overflow-trigger]` and the
 * menu's `[role="menu"]`/`hidden` pair are the only two selectors
 * overflow_menu.js itself reads.
 */
function menuFixture(id) {
  return `
    <div data-overflow-menu>
      <button type="button" id="trigger-${id}" data-overflow-trigger
              aria-haspopup="menu" aria-expanded="false" aria-controls="menu-${id}">…</button>
      <ul id="menu-${id}" role="menu" hidden>
        <li role="none"><button type="button" role="menuitem" data-item="rename-${id}">Rename</button></li>
        <li role="none"><button type="button" role="menuitem" data-item="remove-${id}">Remove</button></li>
      </ul>
    </div>`;
}

function trigger(id) {
  return document.getElementById('trigger-' + id);
}

function menu(id) {
  return document.getElementById('menu-' + id);
}

/** Give one instance a measurable box, which jsdom does not.
 *
 * jsdom lays nothing out: every `getBoundingClientRect()` is all-zero and
 * every `offsetWidth`/`offsetHeight` is 0. The placement maths is
 * arithmetic on those four numbers, so supplying them is enough to assert
 * it — and it is the arithmetic, not the layout, that SNOW-830 wrote.
 *
 * @param {string} id The fixture id ('a' or 'b').
 * @param {object} rect Trigger rect: `top`, `bottom`, `left`, `right`.
 * @param {object} box Menu box: `width`, `height`.
 * @returns {void}
 */
function measure(id, rect, box) {
  trigger(id).getBoundingClientRect = () => ({
    top: rect.top,
    bottom: rect.bottom,
    left: rect.left,
    right: rect.right,
    width: rect.right - rect.left,
    height: rect.bottom - rect.top,
    x: rect.left,
    y: rect.top,
  });
  Object.defineProperty(menu(id), 'offsetWidth', {
    configurable: true,
    get: () => box.width,
  });
  Object.defineProperty(menu(id), 'offsetHeight', {
    configurable: true,
    get: () => box.height,
  });
}

beforeEach(() => {
  document.body.innerHTML = menuFixture('a') + menuFixture('b');
});

describe('opening and closing', () => {
  it('opens on a trigger click — the menu is shown and aria-expanded flips', () => {
    trigger('a').click();

    expect(menu('a').hidden).toBe(false);
    expect(trigger('a').getAttribute('aria-expanded')).toBe('true');
  });

  it('closes on a second click of the same trigger', () => {
    trigger('a').click();
    trigger('a').click();

    expect(menu('a').hidden).toBe(true);
    expect(trigger('a').getAttribute('aria-expanded')).toBe('false');
  });

  it('at most one menu is open at once — opening a second closes the first', () => {
    trigger('a').click();
    trigger('b').click();

    expect(menu('a').hidden).toBe(true);
    expect(trigger('a').getAttribute('aria-expanded')).toBe('false');
    expect(menu('b').hidden).toBe(false);
    expect(trigger('b').getAttribute('aria-expanded')).toBe('true');
  });
});

describe('outside click', () => {
  it('closes the open menu on a click elsewhere in the document', () => {
    trigger('a').click();
    expect(menu('a').hidden).toBe(false);

    document.body.click();

    expect(menu('a').hidden).toBe(true);
    expect(trigger('a').getAttribute('aria-expanded')).toBe('false');
  });

  it('closes the menu on a click on one of its own items too, after the item handler runs', () => {
    // overflow_menu.js's own document click listener is delegated the
    // same as a caller's item handler (map_downloads_manager.js's
    // data-downloads-rename/-delete) would be — both are bound on
    // document/an ancestor, so the caller's handler runs first as the
    // event bubbles, and THIS module still closes the menu behind it
    // once its own listener runs. The module's own header explains why:
    // an item click closes the menu it just acted through, the same way
    // a real "pick an action" menu should — this is the "anything else"
    // branch of closeAllExcept, reached by an in-menu click too, not
    // exempted from it.
    trigger('a').click();
    document.querySelector('[data-item="rename-a"]').click();

    expect(menu('a').hidden).toBe(true);
  });
});

describe('placement (SNOW-830)', () => {
  it('is positioned fixed, measured from the trigger, right edge to right edge', () => {
    // `fixed` rather than `absolute` is the whole point: the row sits in
    // includes/_ugc_panel.html's scroll container, inside
    // includes/_overlay_sheet.html's `overflow-hidden` sheet, and an
    // absolutely positioned menu on the last row is clipped out of sight.
    measure('a', { top: 100, bottom: 144, left: 300, right: 344 }, { width: 176, height: 120 });

    trigger('a').click();

    expect(menu('a').style.position).toBe('fixed');
    // 4px under the trigger's bottom edge.
    expect(menu('a').style.top).toBe('148px');
    // Hangs leftwards: right edge (344) minus the menu's own width (176).
    expect(menu('a').style.left).toBe('168px');
  });

  it('flips above the trigger when the menu will not fit below', () => {
    // The case the whole placement exists for — the last row of a full
    // panel, where "below" is off the bottom of the viewport. jsdom's
    // window is 768 tall.
    measure('a', { top: 690, bottom: 734, left: 300, right: 344 }, { width: 176, height: 120 });

    trigger('a').click();

    // 690 - 4 - 120: above the trigger, with the same 4px gap.
    expect(menu('a').style.top).toBe('566px');
  });

  it('never places the menu off the left edge of a narrow viewport', () => {
    // A trigger near the left edge would otherwise put a 176px menu at a
    // negative x, which no amount of scrolling can reach.
    measure('a', { top: 100, bottom: 144, left: 8, right: 52 }, { width: 176, height: 120 });

    trigger('a').click();

    expect(menu('a').style.left).toBe('4px');
  });

  it('clears the placement again on close', () => {
    // A closed menu carrying last open's coordinates is a trap for
    // anyone reading the DOM, and the next open re-measures anyway.
    measure('a', { top: 100, bottom: 144, left: 300, right: 344 }, { width: 176, height: 120 });

    trigger('a').click();
    trigger('a').click();

    expect(menu('a').style.position).toBe('');
    expect(menu('a').style.top).toBe('');
    expect(menu('a').style.left).toBe('');
  });
});

describe('closing on scroll and resize (SNOW-830)', () => {
  it('closes on a scroll anywhere in the document, including a non-bubbling one', () => {
    // `scroll` does not bubble, so the panel's own rows container scrolls
    // without the document ever hearing it in bubble phase — the module
    // listens in CAPTURE. A `fixed` menu does not travel with the row it
    // was opened from, so it closes rather than chasing it.
    trigger('a').click();
    expect(menu('a').hidden).toBe(false);

    document.body.dispatchEvent(new Event('scroll', { bubbles: false }));

    expect(menu('a').hidden).toBe(true);
    expect(trigger('a').getAttribute('aria-expanded')).toBe('false');
  });

  it('closes on a window resize', () => {
    trigger('a').click();

    window.dispatchEvent(new Event('resize'));

    expect(menu('a').hidden).toBe(true);
  });
});

describe('Escape', () => {
  it('closes the menu without reaching the sheet behind it (SNOW-830)', () => {
    // static/js/map_sheet.js binds Escape-closes-the-sheet on `document`
    // in BUBBLE phase. Both listeners on one node in one phase cannot
    // stop each other, so this module's is in CAPTURE — which runs
    // first, on the way down — and stops propagation there.
    const sheetHandler = vi.fn();
    document.addEventListener('keydown', sheetHandler);
    trigger('a').click();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(menu('a').hidden).toBe(true);
    expect(sheetHandler).not.toHaveBeenCalled();

    document.removeEventListener('keydown', sheetHandler);
  });

  it('lets Escape through untouched when no menu is open (SNOW-830)', () => {
    // The other half of the same fix: swallowing every Escape would make
    // the sheet undismissable by keyboard.
    const sheetHandler = vi.fn();
    document.addEventListener('keydown', sheetHandler);

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(sheetHandler).toHaveBeenCalled();

    document.removeEventListener('keydown', sheetHandler);
  });

  it('closes the open menu and returns focus to its trigger', () => {
    trigger('a').click();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(menu('a').hidden).toBe(true);
    expect(trigger('a').getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(trigger('a'));
  });

  it('is a no-op when nothing is open', () => {
    expect(() =>
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })),
    ).not.toThrow();
  });

  it('ignores any other key', () => {
    trigger('a').click();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

    expect(menu('a').hidden).toBe(false);
  });
});

describe('per-instance ids (buildRow rewrites trigger_id/menu_id per clone)', () => {
  it('two instances with distinct ids operate independently', () => {
    trigger('a').click();
    expect(menu('a').hidden).toBe(false);
    expect(menu('b').hidden).toBe(true);

    trigger('b').click();
    expect(menu('a').hidden).toBe(true);
    expect(menu('b').hidden).toBe(false);
  });

  it("does not depend on the ids being unique — scoped by DOM traversal, not getElementById", () => {
    // Mirrors the un-rewritten template default (_map_downloads_sheet.html's
    // row <template> ships "row-overflow-trigger"/"row-overflow-menu" on
    // every row before buildRow rewrites them post-clone) — two wrappers
    // sharing the same ids still open/close independently because
    // overflow_menu.js reads [data-overflow-trigger]/[role="menu"] via
    // closest()/querySelector(), never getElementById.
    document.body.innerHTML = `
      <div data-overflow-menu>
        <button type="button" id="row-overflow-trigger" data-overflow-trigger
                aria-expanded="false">…</button>
        <ul id="row-overflow-menu" role="menu" hidden></ul>
      </div>
      <div data-overflow-menu>
        <button type="button" id="row-overflow-trigger" data-overflow-trigger
                aria-expanded="false">…</button>
        <ul id="row-overflow-menu" role="menu" hidden></ul>
      </div>`;
    const wrappers = document.querySelectorAll('[data-overflow-menu]');
    const [firstTrigger] = wrappers[0].querySelectorAll('[data-overflow-trigger]');
    const [secondTrigger] = wrappers[1].querySelectorAll('[data-overflow-trigger]');

    firstTrigger.click();

    expect(wrappers[0].querySelector('[role="menu"]').hidden).toBe(false);
    expect(wrappers[1].querySelector('[role="menu"]').hidden).toBe(true);

    secondTrigger.click();

    expect(wrappers[0].querySelector('[role="menu"]').hidden).toBe(true);
    expect(wrappers[1].querySelector('[role="menu"]').hidden).toBe(false);
  });
});
