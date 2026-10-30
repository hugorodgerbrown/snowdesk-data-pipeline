/*
 * tests/js/test_map_alerts.js — the account's alerts behind the date row's
 * bell (SNOW-1333).
 *
 * static/js/map_alerts.js against a reduced copy of the bell and its card,
 * with fetch, localStorage and ``window.pwaMapOverlays`` stubbed.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the bell shows only once the list has an alert, and stays hidden on
 *     an empty week or a failed read;
 *   - each alert is a link to where its push went, newest first;
 *   - the dot means "sent since this device last opened the list": it
 *     clears on opening, and stays clear on the next load of the same list;
 *   - the card opens without closing the sheet being read, and closes on
 *     a press outside it and on Escape.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MARKUP = `
  <div id="map-alerts" data-alerts-url="/account/alerts/recent/" hidden>
    <button id="map-alerts-toggle" type="button" aria-expanded="false">
      <span data-alerts-dot hidden></span>
    </button>
    <div id="map-alerts-card" hidden>
      <ul data-alerts-list></ul>
    </div>
  </div>
  <button id="elsewhere" type="button">elsewhere</button>`;

const NEWER = {
  title: 'Verbier bulletin updated',
  body: 'Valid from Friday at 5 pm to Saturday at 5 pm.',
  url: '/alerts/2026-10-31/',
  sent_at: '2026-10-30T17:05:00+01:00',
  sent: 'Friday at 5 pm',
};
const OLDER = {
  title: 'Verbier bulletin',
  body: 'First of the season.',
  url: '/alerts/2026-10-30/',
  sent_at: '2026-10-29T17:05:00+01:00',
  sent: 'Thursday at 5 pm',
};

let added = [];

const $ = (id) => document.getElementById(id);
const dot = () => document.querySelector('[data-alerts-dot]');
const links = () => [...document.querySelectorAll('.map-alerts-link')];

/**
 * Load a fresh copy of the wiring against a stubbed response.
 *
 * @param {?object} body The JSON body, or null for a failed read.
 */
async function load(body) {
  globalThis.fetch = vi.fn(() =>
    body === null
      ? Promise.reject(new Error('offline'))
      : Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
  );
  vi.resetModules();
  await import('../../static/js/map_alerts.js');
  await new Promise((r) => setTimeout(r, 0));
}

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  window.localStorage.clear();
  window.pwaMapOverlays = { opening: vi.fn(), register: vi.fn() };
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    added.push([type, fn]);
    return original(type, fn, opts);
  });
});

afterEach(() => {
  for (const [type, fn] of added) document.removeEventListener(type, fn);
  added = [];
  delete window.pwaMapOverlays;
  vi.restoreAllMocks();
});

describe('the bell', () => {
  it('stays hidden on an empty week', async () => {
    await load({ alerts: [] });
    expect($('map-alerts').hidden).toBe(true);
  });

  it('stays hidden when the read fails', async () => {
    await load(null);
    expect($('map-alerts').hidden).toBe(true);
  });

  it('reads its own url, with the session', async () => {
    await load({ alerts: [] });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/account/alerts/recent/',
      expect.objectContaining({ credentials: 'same-origin' })
    );
  });

  it('lists each alert as a link to where its push went', async () => {
    await load({ alerts: [NEWER, OLDER] });
    expect($('map-alerts').hidden).toBe(false);
    expect(links().map((a) => a.getAttribute('href'))).toEqual([NEWER.url, OLDER.url]);
    const first = links()[0];
    expect(first.querySelector('.map-alerts-item-title').textContent).toBe(NEWER.title);
    expect(first.querySelector('.map-alerts-item-detail').textContent).toBe(NEWER.body);
    expect(first.querySelector('.map-alerts-item-sent').textContent).toBe(NEWER.sent);
  });
});

describe('the dot', () => {
  it('shows for an alert not yet opened and clears on opening', async () => {
    await load({ alerts: [NEWER, OLDER] });
    expect(dot().hidden).toBe(false);
    $('map-alerts-toggle').click();
    expect(dot().hidden).toBe(true);
    expect(window.localStorage.getItem('snowdesk:alerts-seen')).toBe(NEWER.sent_at);
  });

  it('stays clear on the next load of the same list', async () => {
    window.localStorage.setItem('snowdesk:alerts-seen', NEWER.sent_at);
    await load({ alerts: [NEWER, OLDER] });
    expect(dot().hidden).toBe(true);
  });

  it('comes back when a newer alert arrives', async () => {
    window.localStorage.setItem('snowdesk:alerts-seen', OLDER.sent_at);
    await load({ alerts: [NEWER, OLDER] });
    expect(dot().hidden).toBe(false);
  });
});

describe('the card', () => {
  it('opens without closing the sheet being read', async () => {
    await load({ alerts: [NEWER] });
    $('map-alerts-toggle').click();
    expect($('map-alerts-card').hidden).toBe(false);
    expect($('map-alerts-toggle').getAttribute('aria-expanded')).toBe('true');
    expect(window.pwaMapOverlays.opening).toHaveBeenCalledWith('alerts', { spareObjectSheets: true });
    expect(window.pwaMapOverlays.register).toHaveBeenCalledWith('alerts', expect.any(Object));
  });

  it('closes on a press outside it and on Escape', async () => {
    await load({ alerts: [NEWER] });
    $('map-alerts-toggle').click();
    $('elsewhere').click();
    expect($('map-alerts-card').hidden).toBe(true);
    $('map-alerts-toggle').click();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect($('map-alerts-card').hidden).toBe(true);
    expect($('map-alerts-toggle').getAttribute('aria-expanded')).toBe('false');
  });
});
