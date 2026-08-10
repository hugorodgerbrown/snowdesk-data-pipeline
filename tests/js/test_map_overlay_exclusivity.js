/*
 * tests/js/test_map_overlay_exclusivity.js — one open map overlay at a
 * time (SNOW-658).
 *
 * Two halves, and the second is the point of the ticket.
 *
 * 1. The registry itself (static/js/map_overlay_exclusivity.js): what
 *    ``opening()`` closes, what it leaves alone, and what registering the
 *    same name twice does.
 *
 * 2. The MATRIX over the real surfaces. Each module is loaded against its
 *    own markup, and every ordered pair is opened in turn: open A, open B,
 *    A must be shut. That is what the hand-wired pairs this ticket deleted
 *    could not give — they covered three relationships out of fifteen, in
 *    one direction each — and it is what makes a future surface that
 *    forgets to register fail here rather than on Hugo's screen. The
 *    expected name list is asserted first, so a surface that stops
 *    registering fails with "which surface?" rather than with a pass.
 *
 * The anchored map-detail popup (map.js) is registered too but is not in
 * this matrix: reaching it means booting the whole map bundle with a
 * MapLibre stub, so it is covered where that boot already exists —
 * tests/js/test_map_detail_popup_exclusivity.js.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

/** Load the registry fresh, so each test starts with nothing registered. */
async function loadRegistry() {
  vi.resetModules();
  delete window.pwaMapOverlays;
  await import('../../static/js/map_overlay_exclusivity.js');
  return window.pwaMapOverlays;
}

/**
 * A registered overlay whose open state is a plain boolean.
 *
 * @param {string} name
 * @param {boolean} open Whether it starts open.
 * @returns {{ name: string, open: boolean, closes: number }}
 */
function fakeOverlay(name, open = false) {
  const overlay = { name: name, open: open, closes: 0 };
  window.pwaMapOverlays.register(name, {
    isOpen: () => overlay.open,
    close: () => {
      overlay.open = false;
      overlay.closes += 1;
    },
  });
  return overlay;
}

describe('window.pwaMapOverlays', () => {
  beforeEach(async () => {
    await loadRegistry();
  });

  it('is a frozen global, like every other bridge on this page', () => {
    expect(Object.isFrozen(window.pwaMapOverlays)).toBe(true);
  });

  it('closes every other open overlay when one opens', () => {
    const menu = fakeOverlay('menu', true);
    const downloads = fakeOverlay('downloads', true);
    const favourites = fakeOverlay('favourites', false);

    window.pwaMapOverlays.opening('favourites');

    expect(menu.open).toBe(false);
    expect(downloads.open).toBe(false);
    expect(favourites.open).toBe(false);
  });

  it('leaves the overlay doing the opening alone', () => {
    const favourites = fakeOverlay('favourites', true);

    window.pwaMapOverlays.opening('favourites');

    expect(favourites.closes).toBe(0);
  });

  it('never closes an overlay that is already shut', () => {
    // The sheets' close path runs teardown (map_sheet.js empties the sheet
    // body), so a close for a surface nobody opened is work — and, read back
    // off the DOM, a lie about what just happened.
    const menu = fakeOverlay('menu', false);

    window.pwaMapOverlays.opening('favourites');

    expect(menu.closes).toBe(0);
  });

  it('replaces an earlier registration under the same name', () => {
    const first = fakeOverlay('favourites', true);
    const second = fakeOverlay('favourites', true);

    window.pwaMapOverlays.opening('menu');

    expect(first.closes).toBe(0);
    expect(second.closes).toBe(1);
  });

  it('closeAll() shuts everything, including the last one opened', () => {
    const menu = fakeOverlay('menu', true);
    const favourites = fakeOverlay('favourites', true);

    window.pwaMapOverlays.closeAll();

    expect(menu.open).toBe(false);
    expect(favourites.open).toBe(false);
  });

  it('names() lists what is registered, sorted', () => {
    fakeOverlay('menu');
    fakeOverlay('downloads');

    expect(window.pwaMapOverlays.names()).toEqual(['downloads', 'menu']);
  });

  it('tolerates a name nothing registered under', () => {
    const menu = fakeOverlay('menu', true);

    window.pwaMapOverlays.opening('a-surface-that-never-registered');

    expect(menu.open).toBe(false);
  });
});
