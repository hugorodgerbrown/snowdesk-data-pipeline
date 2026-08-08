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
 * side effects (it self-wires delegated `click`/`keydown` listeners at
 * import time) is enough, mirroring test_overlays.js's own pattern for
 * the same shape of module.
 */

import { beforeEach, describe, expect, it } from 'vitest';

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

describe('Escape', () => {
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
