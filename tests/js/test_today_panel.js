/*
 * tests/js/test_today_panel.js — the Today panel behind the date row's sun
 * (SNOW-1366).
 *
 * static/js/today_panel.js against a reduced copy of the roundel, the sheet
 * and its template, with fetch, localStorage, htmx and ``window.MapSheet``
 * stubbed — the sheet controller's own behaviour is map_sheet.js's, tested
 * there; what is pinned here is this module's:
 *   - the roundel toggles the panel, and each open re-renders the body and
 *     asks favourites:today for the rows;
 *   - a failed load says so instead of leaving the loading line up;
 *   - a followed row closes the panel, so the place it opens is the one
 *     panel up, and moves a map on another day to today first;
 *   - the bell's dot, now the sun's: shown while the newest alert is newer
 *     than the last opening, cleared by opening, hidden on a failed read;
 *   - window.pwaTodayPanel, the back pill's way back in.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MARKUP = `
  <button id="map-today-panel-toggle" type="button" aria-expanded="false"
          data-alerts-url="/account/alerts/recent/">
    <span data-today-dot hidden></span>
  </button>
  <div id="today-sheet" hidden></div>
  <div id="season-scrubber" data-today="2026-11-02"></div>
  <template id="today-sheet-template">
    <div data-today-rows data-today-url="/favourites/partials/today/"><p>Loading today…</p></div>
  </template>
  <template id="today-strings-template">
    <span data-string="error">Today could not be loaded.</span>
  </template>`;

const ALERT = { sent_at: '2026-11-02T07:05:00+01:00' };

let added = [];
let controller;

const $ = (id) => document.getElementById(id);
const dot = () => document.querySelector('[data-today-dot]');
const rows = () => document.querySelector('#today-sheet [data-today-rows]');

/** A stand-in for MapSheet.attach: shows and hides, runs the teardown. */
function stubMapSheet() {
  window.MapSheet = {
    attach: vi.fn((el, opts) => {
      controller = {
        open: vi.fn(() => el.removeAttribute('hidden')),
        close: vi.fn(() => {
          el.setAttribute('hidden', '');
          opts.onTeardown?.();
        }),
        isOpen: () => !el.hasAttribute('hidden'),
      };
      return controller;
    }),
  };
}

/**
 * Load a fresh copy of the module against a stubbed alerts response.
 *
 * @param {?object} body The alerts JSON, or null for a failed read.
 */
async function load(body) {
  globalThis.fetch = vi.fn(() =>
    body === null
      ? Promise.reject(new Error('offline'))
      : Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
  );
  vi.resetModules();
  await import('../../static/js/today_panel.js');
  await new Promise((r) => setTimeout(r, 0));
}

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  window.localStorage.clear();
  stubMapSheet();
  globalThis.htmx = { ajax: vi.fn() };
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    added.push([type, fn]);
    return original(type, fn, opts);
  });
});

afterEach(() => {
  for (const [type, fn] of added) document.removeEventListener(type, fn);
  added = [];
  delete window.MapSheet;
  delete window.pwaTodayPanel;
  delete globalThis.htmx;
  vi.restoreAllMocks();
});

describe('the roundel', () => {
  it('opens the panel and loads today', async () => {
    await load({ alerts: [] });
    $('map-today-panel-toggle').click();
    expect($('today-sheet').hidden).toBe(false);
    expect($('map-today-panel-toggle').getAttribute('aria-expanded')).toBe('true');
    expect(globalThis.htmx.ajax).toHaveBeenCalledWith(
      'GET',
      '/favourites/partials/today/',
      expect.objectContaining({ target: rows(), swap: 'innerHTML' })
    );
  });

  it('closes it on a second press, and empties the sheet', async () => {
    await load({ alerts: [] });
    $('map-today-panel-toggle').click();
    $('map-today-panel-toggle').click();
    expect($('today-sheet').hidden).toBe(true);
    expect($('today-sheet').children.length).toBe(0);
    expect($('map-today-panel-toggle').getAttribute('aria-expanded')).toBe('false');
  });

  it('is what the back pill reopens', async () => {
    await load({ alerts: [] });
    window.pwaTodayPanel.open();
    expect(window.pwaTodayPanel.isOpen()).toBe(true);
  });
});

describe('the list', () => {
  it('says a failed load failed', async () => {
    await load({ alerts: [] });
    $('map-today-panel-toggle').click();
    document.dispatchEvent(
      new CustomEvent('htmx:sendError', { detail: { target: rows() } })
    );
    expect(rows().textContent).toContain('could not be loaded');
  });

  it('steps aside when a row is followed', async () => {
    await load({ alerts: [] });
    $('map-today-panel-toggle').click();
    const link = document.createElement('a');
    link.href = '/bulletin/ch-4115/';
    link.setAttribute('data-panel-open', 'region');
    link.addEventListener('click', (e) => e.preventDefault());
    rows().append(link);
    link.click();
    expect($('today-sheet').hidden).toBe(true);
  });

  it('moves the map to today before a followed row opens its place', async () => {
    await load({ alerts: [] });
    const scrubs = [];
    const onScrub = (e) => scrubs.push(e.detail.date);
    document.addEventListener('snowdesk:scrub-to', onScrub);
    document.dispatchEvent(
      new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-11-01' } })
    );
    $('map-today-panel-toggle').click();
    const link = document.createElement('a');
    link.setAttribute('data-panel-open', 'region');
    link.addEventListener('click', (e) => e.preventDefault());
    rows().append(link);
    link.click();
    expect(scrubs).toEqual(['2026-11-02']);

    document.dispatchEvent(
      new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-11-02' } })
    );
    $('map-today-panel-toggle').click();
    const again = document.createElement('a');
    again.setAttribute('data-panel-open', 'region');
    again.addEventListener('click', (e) => e.preventDefault());
    rows().append(again);
    again.click();
    expect(scrubs).toEqual(['2026-11-02']);
    document.removeEventListener('snowdesk:scrub-to', onScrub);
  });
});

describe('the dot', () => {
  it('shows for an alert not yet seen, and clears on opening', async () => {
    await load({ alerts: [ALERT] });
    expect(dot().hidden).toBe(false);
    $('map-today-panel-toggle').click();
    expect(dot().hidden).toBe(true);
    expect(window.localStorage.getItem('snowdesk:alerts-seen')).toBe(ALERT.sent_at);
  });

  it('stays clear for an alert seen in the bell before it', async () => {
    window.localStorage.setItem('snowdesk:alerts-seen', ALERT.sent_at);
    await load({ alerts: [ALERT] });
    expect(dot().hidden).toBe(true);
  });

  it('stays hidden on an empty week or a failed read', async () => {
    await load({ alerts: [] });
    expect(dot().hidden).toBe(true);
    await load(null);
    expect(dot().hidden).toBe(true);
  });
});
