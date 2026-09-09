/*
 * tests/js/test_panel_rows_cache.js — the map UGC panels' row cache
 * (SNOW-879, static/js/panel_rows_cache.js).
 *
 * The module exists so that opening the favourites / routes / observations
 * sheet does not wait on a round trip it has already made. Everything it
 * does is jsdom's to hold — a Map, an innerHTML write, and one htmx
 * lifecycle event — so this is the whole of its coverage and none of it
 * belongs in a browser.
 *
 * Four behaviours, and the last two are the ones worth being careful about:
 *
 *   a cold load paints nothing and caches what comes back;
 *   a warm load paints the cached rows BEFORE the request goes out, which
 *   is the entire point of the module;
 *   an identical revalidation is suppressed at htmx:beforeSwap — not to
 *   save work, but because rebuilding the rows under a user who has already
 *   started an inline rename would throw that rename away;
 *   a CHANGED revalidation still swaps, so the cache can never pin a stale
 *   list on screen.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const LIST_URL = '/favourites/partials/list/';

document.body.innerHTML = '<div id="rows"></div>';

// The response the next htmx.ajax will carry, and the swap it performs —
// held open so a test can assert what is on screen BETWEEN the call and the
// response, which is the window this module exists to fill.
let nextBody = '<ul><li>Verbier</li></ul>';
let nextIsError = false;
/** @type {Array<{detail: object, target: Element, resolve: Function}>} */
let pending = [];

globalThis.htmx = {
  process: vi.fn(),
  ajax: vi.fn((verb, url, opts) => new Promise((resolve) => {
    pending.push({ target: opts.target, body: nextBody, isError: nextIsError, resolve });
  })),
};

/** Deliver every outstanding htmx response, the way htmx 2.0.4 does. */
async function flush() {
  const outstanding = pending;
  pending = [];
  for (const req of outstanding) {
    const detail = {
      target: req.target,
      serverResponse: req.body,
      isError: req.isError,
      shouldSwap: !req.isError,
    };
    document.body.dispatchEvent(new CustomEvent('htmx:beforeSwap', { detail }));
    // htmx reads shouldSwap back off the detail after the event — this is
    // the hook the module suppresses the redundant swap through.
    if (detail.shouldSwap) req.target.innerHTML = detail.serverResponse;
    req.swapped = detail.shouldSwap;
    req.resolve();
  }
  await Promise.resolve();
  return outstanding;
}

await import('../../static/js/panel_rows_cache.js');

const rows = document.getElementById('rows');

beforeEach(() => {
  rows.innerHTML = '';
  pending = [];
  nextIsError = false;
  nextBody = '<ul><li>Verbier</li></ul>';
  globalThis.htmx.ajax.mockClear();
  globalThis.htmx.process.mockClear();
  window.pwaPanelRows.invalidate('favourites');
});

describe('a cold load', () => {
  it('paints nothing, because there is nothing yet to paint', async () => {
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });

    expect(rows.innerHTML).toBe('');
    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);

    await flush();
    expect(rows.innerHTML).toContain('Verbier');
  });
});

describe('a warm load', () => {
  it('paints the cached rows before the request goes out', async () => {
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    await flush();
    rows.innerHTML = '';

    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });

    // Synchronously — no await between the call and this assertion. That is
    // the claim: the rows are up on the frame the roundel is pressed.
    expect(rows.innerHTML).toContain('Verbier');
    // And they are LIVE: each row carries its own Remove form, so markup
    // htmx has not processed would be a delete button that does nothing.
    expect(globalThis.htmx.process).toHaveBeenCalledWith(rows);
  });

  it('is skipped for a re-read that follows a mutation', async () => {
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    await flush();
    rows.innerHTML = '';

    // No `cached` — this is the post-delete re-read, and painting the cache
    // here would flash the row the user just deleted back onto the screen.
    window.pwaPanelRows.load('favourites', LIST_URL, rows);

    expect(rows.innerHTML).toBe('');
  });
});

describe('revalidation', () => {
  it('does not re-swap an unchanged list', async () => {
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    await flush();

    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    const [request] = await flush();

    expect(request.swapped).toBe(false);
    expect(rows.innerHTML).toContain('Verbier');
  });

  it('swaps a changed list, so the cache cannot pin a stale one', async () => {
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    await flush();

    nextBody = '<ul><li>Verbier</li><li>Zermatt</li></ul>';
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    const [request] = await flush();

    expect(request.swapped).toBe(true);
    expect(rows.innerHTML).toContain('Zermatt');

    // And the new list is what a later open paints, not the one before it.
    rows.innerHTML = '';
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    expect(rows.innerHTML).toContain('Zermatt');
  });

  it('still swaps when a warm filled the cache after the panel opened', async () => {
    // The race this suite exists to pin down. The panel is opened with an
    // EMPTY cache, so nothing is painted and the rows sit on their loading
    // line. The idle warm then lands first and caches a body identical to
    // the response still in flight. Suppressing on "the cache matches"
    // would drop the only swap that could ever fill this panel, and leave
    // it loading for good.
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    expect(rows.innerHTML).toBe('');

    // The warm arrives while the panel's own request is still out.
    const fetchMock = vi.fn(() => Promise.resolve({
      ok: true,
      text: () => Promise.resolve(nextBody),
    }));
    vi.stubGlobal('fetch', fetchMock);
    vi.useFakeTimers();
    window.pwaPanelRows.warm('favourites', LIST_URL);
    await vi.advanceTimersByTimeAsync(1300);
    vi.useRealTimers();
    await Promise.resolve();
    vi.unstubAllGlobals();

    const [request] = await flush();

    expect(request.swapped).toBe(true);
    expect(rows.innerHTML).toContain('Verbier');
  });

  it('never caches an error response as content', async () => {
    nextIsError = true;
    nextBody = '<p>500</p>';
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    await flush();

    rows.innerHTML = '';
    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });

    // Nothing painted — the panel draws its own error line from the
    // matching htmx:responseError instead.
    expect(rows.innerHTML).toBe('');
  });
});

describe('the idle warm', () => {
  it('fills the cache over a plain fetch that declares itself to htmx', async () => {
    const fetchMock = vi.fn(() => Promise.resolve({
      ok: true,
      text: () => Promise.resolve('<ul><li>Warmed</li></ul>'),
    }));
    vi.stubGlobal('fetch', fetchMock);
    vi.useFakeTimers();

    window.pwaPanelRows.warm('favourites', LIST_URL);
    // jsdom ships no requestIdleCallback, so the module's timeout fallback
    // is the path under test here.
    await vi.advanceTimersByTimeAsync(1300);
    vi.useRealTimers();
    await Promise.resolve();

    const [, options] = fetchMock.mock.calls[0];
    // The endpoint is @require_htmx and this is not an htmx request.
    expect(options.headers['HX-Request']).toBe('true');
    expect(options.credentials).toBe('same-origin');

    window.pwaPanelRows.load('favourites', LIST_URL, rows, { cached: true });
    expect(rows.innerHTML).toContain('Warmed');

    vi.unstubAllGlobals();
  });
});
