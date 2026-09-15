/*
 * tests/js/test_map_route_detail_panel.js — what the route detail sheet
 * puts under the figures (SNOW-973, static/js/map_route_detail.js).
 *
 * The module's other half — that a route tap opens this sheet at all, that
 * it joins the exclusivity registry, and that the figures map.js builds
 * arrive in it — is covered in test_map_detail_popup_exclusivity.js, which
 * boots the whole map bundle. THIS file skips the map entirely and drives
 * ``window.pwaRouteDetail.open()`` directly, because what is under test is
 * the bulletin half: one fetch, and the three different things the panel
 * says when it fails.
 *
 * Those three are three different claims, and the middle one is the reason
 * the response's freshness headers are stored at all:
 *
 *   a reading this device holds, inside its horizon → painted, behind an
 *   explicit line saying when it was saved;
 *   a reading past its ``unsafe_after_seconds`` → REPLACED by the expired
 *   sentence, never repainted as a current reading of avalanche terrain;
 *   nothing held at all → the ordinary failure line.
 *
 * It also covers the panel FOLLOWING the map's day. The season scrubber is
 * inside #map, which map_sheet.js excludes from click-outside dismissal, so
 * the sheet genuinely stays open across a date change — and a sheet that
 * stayed open showing the previous day's reading while the map repainted to
 * the new one was the defect.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const UUID = '11111111-2222-3333-4444-555555555555';
const DAY = '2026-03-01';
/** The day the scrubber is moved to, in the date-change cases below. */
const OTHER_DAY = '2026-03-02';
const STORE = 'data:route_bulletins';
const KEY = `${UUID}:${DAY}`;
const OTHER_KEY = `${UUID}:${OTHER_DAY}`;
const HTML = '<div data-testid="route-bulletin">Aletsch · 1.4 km on N</div>';
const CACHED = '<div data-testid="route-bulletin">A saved reading</div>';
const UNSAFE_AFTER = 48 * 60 * 60;

document.body.innerHTML = `
  <div id="map"
       data-route-bulletin-url-template="/routes/__UUID__/bulletin/"></div>
  <div id="route-detail-sheet" hidden tabindex="-1" data-overlay></div>
  <template id="route-detail-template">
    <div>
      <div data-route-detail-figures></div>
      <div data-route-detail-bulletin></div>
    </div>
  </template>
  <template id="route-detail-strings-template">
    <span data-string="loading">Loading this day’s bulletin…</span>
    <span data-string="failed">This day’s bulletin couldn’t be loaded.</span>
    <span data-string="cached-as-of">Showing a saved reading — as of %(time)s.</span>
    <span data-string="expired">This saved reading has expired.</span>
  </template>`;

await import('../../static/js/db.js');
await import('../../static/js/routes_bulletin_offline.js');
await import('../../static/js/map_sheet.js');
await import('../../static/js/map_route_detail.js');

/** The figures map.js would have built. */
function figures() {
  const node = document.createElement('div');
  node.setAttribute('data-route-detail', '');
  node.textContent = 'Haute Route';
  return node;
}

/** The sheet's bulletin slot, whatever is currently in it. */
function bulletinSlot() {
  return document.querySelector('[data-route-detail-bulletin]');
}

/** Stub one JSON answer from routes:bulletin, with its freshness headers. */
function answerWith({ generatedAt, unsafeAfter, day = DAY } = {}) {
  const headers = new Map();
  if (generatedAt) headers.set('X-Data-Generated-At', generatedAt);
  if (unsafeAfter) headers.set('X-Data-Unsafe-After', String(unsafeAfter));
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({
      ok: true,
      headers: { get: (name) => headers.get(name) ?? null },
      json: () => Promise.resolve({ html: HTML, day: day }),
    })),
  );
}

/**
 * One JSON answer from routes:bulletin, with no freshness headers.
 *
 * @param {string} html The fragment the server rendered.
 * @param {string} day The day it answered for.
 * @returns {object} A Response-shaped object.
 */
function jsonAnswer(html, day) {
  return {
    ok: true,
    headers: { get: () => null },
    json: () => Promise.resolve({ html: html, day: day }),
  };
}

/** Move the map's day, exactly as map_scrubber.js commits one. */
function changeDateTo(date) {
  document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
    detail: { date: date, source: 'scrubber' },
  }));
}

/** Stub a fetch that fails the way a dead radio does. */
function answerWithFailure() {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))));
}

/** Poll until `predicate` returns truthy, or fail. */
async function waitFor(predicate, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('condition never became true');
}

beforeEach(async () => {
  window.pwaRouteDetail.close();
  await window.pwaDb.delete(STORE, KEY);
  await window.pwaDb.delete(STORE, OTHER_KEY);
  vi.unstubAllGlobals();
});

describe('opening the panel', () => {
  it('seats the figures and opens before the bulletin resolves', () => {
    answerWith();

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });

    // Synchronously: a tap that shows nothing until the network answers
    // reads as a tap that missed, and the next tap closes what it opened.
    expect(window.pwaRouteDetail.isOpen()).toBe(true);
    expect(document.querySelector('[data-route-detail]').textContent)
      .toContain('Haute Route');
    expect(bulletinSlot().textContent).toContain('Loading');
  });

  it('asks for the day the map is showing', async () => {
    answerWith();

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });

    await waitFor(() => globalThis.fetch.mock.calls.length > 0);
    expect(globalThis.fetch.mock.calls[0][0]).toBe(
      `/routes/${UUID}/bulletin/?d=${DAY}`,
    );
  });

  it('paints the reading the server answered with', async () => {
    answerWith();

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });

    await waitFor(() => bulletinSlot().querySelector(
      '[data-testid="route-bulletin"]',
    ));
    expect(bulletinSlot().textContent).toContain('Aletsch');
  });

  it('asks for nothing at all for a pending share', () => {
    // routes:bulletin is owner-scoped, and a recipient who has not saved
    // the route would only be shown a 404's failure line.
    answerWith();

    window.pwaRouteDetail.open({ node: figures(), uuid: null, day: DAY });

    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(bulletinSlot().textContent.trim()).toBe('');
  });

  it('stores what it painted, under the day the SERVER answered for', async () => {
    answerWith({ generatedAt: '2026-03-01T05:00:00+00:00', unsafeAfter: UNSAFE_AFTER });

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });

    await waitFor(() => bulletinSlot().querySelector(
      '[data-testid="route-bulletin"]',
    ));
    const stored = await window.pwaRoutesBulletinOffline.read(KEY);
    expect(stored.body).toBe(HTML);
    expect(stored.generated_at).toBe('2026-03-01T05:00:00+00:00');
    expect(stored.unsafe_after_seconds).toBe(UNSAFE_AFTER);
  });
});

describe('when the request fails', () => {
  it('repaints a held reading behind an "as of" line', async () => {
    await window.pwaRoutesBulletinOffline.write(
      KEY, CACHED, new Date().toISOString(), UNSAFE_AFTER,
    );
    answerWithFailure();

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });

    await waitFor(() => bulletinSlot().querySelector(
      '[data-testid="route-bulletin-cached-as-of"]',
    ));
    expect(bulletinSlot().textContent).toContain('A saved reading');
    expect(bulletinSlot().textContent).toContain('as of');
  });

  it('refuses to repaint one past its horizon, and says so', async () => {
    // The whole reason the envelope is stored. Three days on, the forecast
    // has turned over four times; this body is not a current reading of
    // anything and must not be shown as one.
    await window.pwaRoutesBulletinOffline.write(
      KEY,
      CACHED,
      new Date(Date.now() - 72 * 60 * 60 * 1000).toISOString(),
      UNSAFE_AFTER,
    );
    answerWithFailure();

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });

    await waitFor(() => bulletinSlot().querySelector(
      '[data-testid="route-bulletin-expired"]',
    ));
    expect(bulletinSlot().textContent).toContain('expired');
    expect(bulletinSlot().textContent).not.toContain('A saved reading');
  });

  it('says so plainly when this device holds nothing', async () => {
    answerWithFailure();

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });

    await waitFor(() => bulletinSlot().querySelector(
      '[data-testid="route-bulletin-failed"]',
    ));
    expect(bulletinSlot().textContent).toContain("couldn’t be loaded");
  });

  it('will not read another day\'s row for this one', async () => {
    // Keyed by (route, day) precisely so this cannot happen: yesterday's
    // reading is not an answer about today.
    await window.pwaRoutesBulletinOffline.write(
      `${UUID}:2026-02-28`, CACHED, new Date().toISOString(), UNSAFE_AFTER,
    );
    answerWithFailure();

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });

    await waitFor(() => bulletinSlot().querySelector(
      '[data-testid="route-bulletin-failed"]',
    ));
    await window.pwaDb.delete(STORE, `${UUID}:2026-02-28`);
  });
});

describe('two taps in flight', () => {
  it('lets the newest tap own the sheet', async () => {
    // Routes cross on the map, so a mis-tap followed by a correction is
    // the ordinary case. The first response must not land on top of the
    // second's panel.
    let release;
    const slow = new Promise((resolve) => { release = resolve; });
    vi.stubGlobal(
      'fetch',
      vi.fn(() => slow.then(() => ({
        ok: true,
        headers: { get: () => null },
        json: () => Promise.resolve({ html: '<p>the first tap</p>', day: DAY }),
      }))),
    );

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });
    window.pwaRouteDetail.open({ node: figures(), uuid: null, day: DAY });
    release();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(bulletinSlot().textContent).not.toContain('the first tap');
  });
});

describe('when the map\'s day moves under an open panel', () => {
  it('asks again, for the day the scrubber moved to', async () => {
    // The defect: the scrubber lives inside #map, which map_sheet.js
    // excludes from click-outside dismissal, so the panel stays open — and
    // it went on showing the previous day's reading while the map
    // repainted to the new one.
    answerWith();
    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });
    await waitFor(() => globalThis.fetch.mock.calls.length > 0);

    changeDateTo(OTHER_DAY);

    await waitFor(() => globalThis.fetch.mock.calls.length > 1);
    expect(globalThis.fetch.mock.calls[1][0]).toBe(
      `/routes/${UUID}/bulletin/?d=${OTHER_DAY}`,
    );
  });

  it('re-fetches rather than closing the panel', async () => {
    // map_region_panel.js's own answer to this event. The reading IS the
    // answer to "what does this day say about this line", so a new day is
    // a new answer rather than a reason to take the surface away.
    answerWith();
    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });

    changeDateTo(OTHER_DAY);

    expect(window.pwaRouteDetail.isOpen()).toBe(true);
  });

  it('leaves the figures exactly where map.js put them', async () => {
    // A route's distance, ascent and terrain are facts about the track and
    // do not move with the calendar — so the node map.js built is not
    // rebuilt, and not even touched.
    answerWith();
    const node = figures();
    window.pwaRouteDetail.open({ node: node, uuid: UUID, day: DAY });

    changeDateTo(OTHER_DAY);
    await waitFor(() => globalThis.fetch.mock.calls.length > 1);

    expect(document.querySelector('[data-route-detail]')).toBe(node);
  });

  it('lets the new day\'s answer beat the old day\'s slow one', async () => {
    // The race the request token exists for, reached by a second route:
    // the previous day's request is still in flight when the new day's
    // lands, and a reading of the wrong day painted over the right one is
    // exactly the defect this fix is about.
    let release;
    const slow = new Promise((resolve) => { release = resolve; });
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => (String(url).includes(OTHER_DAY)
        ? Promise.resolve(jsonAnswer('<p>the new day</p>', OTHER_DAY))
        : slow.then(() => jsonAnswer('<p>the old day</p>', DAY)))),
    );

    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });
    changeDateTo(OTHER_DAY);
    await waitFor(() => bulletinSlot().textContent.includes('the new day'));
    release();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(bulletinSlot().textContent).toContain('the new day');
    expect(bulletinSlot().textContent).not.toContain('the old day');
  });

  it('asks for nothing at all for a pending share', () => {
    // No uuid, so nothing to re-fetch — and a date change over one must be
    // a no-op rather than a request for `undefined`'s bulletin.
    answerWith();
    window.pwaRouteDetail.open({ node: figures(), uuid: null, day: DAY });

    expect(() => changeDateTo(OTHER_DAY)).not.toThrow();
    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(bulletinSlot().textContent.trim()).toBe('');
  });

  it('asks for nothing once the panel is closed', () => {
    answerWith();
    window.pwaRouteDetail.open({ node: figures(), uuid: UUID, day: DAY });
    window.pwaRouteDetail.close();
    globalThis.fetch.mockClear();

    changeDateTo(OTHER_DAY);

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
