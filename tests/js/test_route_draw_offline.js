/*
 * tests/js/test_route_draw_offline.js — saving a drawn route offline
 * (SNOW-1262, static/js/route_draw.js against the real mutation queue).
 *
 * The draft in localStorage and the row in ``queue:mutations`` are tied by
 * one Idempotency-Key, minted by route_draw.js and written onto the draft
 * BEFORE the save goes anywhere. Everything here is about that pair:
 *
 *   - an offline Save queues once, under the draft's key, stamped with the
 *     account, and keeps the draft;
 *   - reopening draw mode while it waits shows it locked, and a second
 *     Save queues nothing;
 *   - the draft clears once the queue acknowledges the key — including an
 *     acknowledgement the service worker left with no tab open, read at
 *     load (the first test, seeded before the module is imported);
 *   - with neither row nor acknowledgement the draft keeps its key, and an
 *     unchanged re-save sends it again — an edited one gets a new key;
 *   - a refused save untags the draft; Edit and Discard;
 *   - another account's draft is discarded;
 *   - online, Save is still the direct post, under the same key, and falls
 *     back to the queue when the post never gets an answer.
 *
 * The real db.js / mutation_queue.js stack is loaded once, under the
 * signed-in account 'me' (db.js memoises the meta tag — see
 * test_mutation_queue.js's header).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ME = 'me';
const CREATE_URL = '/routes/drawn/';
const DRAFT_KEY = 'snowdesk.route-draft';
const POINTS = [[7, 46], [7, 46.001]];

const meta = document.createElement('meta');
meta.setAttribute('name', 'pwa-user-id');
meta.setAttribute('content', ME);
document.head.appendChild(meta);

document.body.innerHTML = `
  <div id="route-draw-sheet" hidden></div>
  <form id="route-draw-config" hidden data-preview-url="" data-create-url="${CREATE_URL}"
        data-signin-url="/signin/" data-save-eligible="true">
    <input name="csrfmiddlewaretoken" value="csrf-token">
  </form>
  <div id="route-draw-crosshair" hidden></div>
  <div id="map"></div>
  <template id="route-draw-template">
    <p data-route-draw-hint></p>
    <p data-route-draw-stats></p>
    <p data-route-draw-notice hidden></p>
    <button type="button" data-route-draw-undo>Undo</button>
    <button type="button" data-route-draw-add>Add point</button>
    <form data-route-draw-save>
      <input id="route-draw-name">
      <button type="submit" data-route-draw-submit>Save</button>
    </form>
    <button type="button" data-route-draw-clear>Start over</button>
  </template>`;

// The worker replayed a save with every tab closed: its acknowledgement is
// in meta:app and its row is gone. Seeded before route_draw.js loads, so
// the module's load-time reconcile is what reads it.
localStorage.setItem(
  DRAFT_KEY,
  JSON.stringify({ v: 1, points: POINTS, name: 'Synced', taps: [0, 1], key: 'k-sw', principal: ME }),
);
await import('../../static/js/i18n_strings.js');
await import('../../static/js/db.js');
await window.pwaDb.put('meta:app', { key: 'mutations.principal', value: ME });
await window.pwaDb.put('meta:app', {
  key: 'mutations.ack:k-sw',
  value: { kind: 'route-create', uuid: 'u-sw', at: Date.now() },
});
await import('../../static/js/mutation_queue_core.js');
await import('../../static/js/mutation_queue.js');
await import('../../static/js/route_draw_core.js');

const toast = vi.fn();
const sheet = document.getElementById('route-draw-sheet');
let drawController = null;
window.MapSheet = {
  toast,
  attach: (el, options) => {
    let open = false;
    drawController = {
      open: () => {
        open = true;
        el.hidden = false;
      },
      close: () => {
        if (!open) return;
        open = false;
        el.hidden = true;
        options.onTeardown();
      },
      isOpen: () => open,
    };
    return drawController;
  },
};
const sources = new Map();
window.snowdeskMapState = {
  map: {
    getSource: (id) => sources.get(id) || null,
    addSource: (id) => sources.set(id, { setData: () => {} }),
    addLayer: () => {},
    removeLayer: () => {},
    removeSource: (id) => sources.delete(id),
    getStyle: () => ({ layers: [], sources: {} }),
    getContainer: () => document.getElementById('map'),
    on: () => {},
    off: () => {},
    getZoom: () => 10,
    getCenter: () => ({ lng: 7, lat: 46 }),
    unproject: () => ({ lng: 7, lat: 46 }),
  },
};
await import('../../static/js/route_draw.js');

/** Poll `predicate` until truthy, or fail. */
async function waitFor(predicate, { timeoutMs = 2000, intervalMs = 5 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await predicate();
    if (last) return last;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`condition never became true (last=${JSON.stringify(last)})`);
}

function draft() {
  const raw = localStorage.getItem(DRAFT_KEY);
  return raw ? JSON.parse(raw) : null;
}

function storeDraft(value) {
  localStorage.setItem(DRAFT_KEY, JSON.stringify({ v: 1, points: POINTS, name: 'Mont Fort', taps: [0, 1], ...value }));
}

function rows() {
  return window.pwaDb.getAll('queue:mutations');
}

function setOnline(online) {
  Object.defineProperty(navigator, 'onLine', { value: online, configurable: true });
}

/** Press Save in the open panel. */
function pressSave() {
  sheet
    .querySelector('[data-route-draw-save]')
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

/** Let the queue's change announcements and the reconciles they start settle. */
function settle() {
  return new Promise((resolve) => setTimeout(resolve, 30));
}

beforeEach(async () => {
  setOnline(false);
  toast.mockClear();
});

afterEach(async () => {
  vi.unstubAllGlobals();
  // Leave draw mode, and empty the queue and the draft for the next test.
  drawController.close();
  await window.pwaDb.clear('queue:mutations');
  await settle();
  localStorage.removeItem(DRAFT_KEY);
});

describe('a save the worker replayed with no tab open', () => {
  it('clears the draft on the next load', async () => {
    await waitFor(() => draft() === null);
  });
});

describe('an offline save', () => {
  it('queues once, under the key the draft is tagged with, and keeps the draft', async () => {
    storeDraft({ principal: ME });
    window.pwaRouteDraw.start();
    pressSave();

    const [row] = await waitFor(async () => {
      const all = await rows();
      return all.length ? all : null;
    });
    expect(row.kind).toBe('route-create');
    expect(row.url).toBe(CREATE_URL);
    expect(row.headers['X-CSRFToken']).toBe('csrf-token');
    expect(JSON.parse(row.body)).toEqual({ points: POINTS, name: 'Mont Fort' });
    expect(draft().key).toBe(row.idempotency_key);
    expect(draft().principal).toBe(ME);
    expect(draft().points).toEqual(POINTS);
    expect(window.pwaRouteDraw.isActive()).toBe(false);
    expect(toast).toHaveBeenCalledWith("Saved on this device. It will sync when you're back online.");
  });

  it('reopens locked while it waits, and a second Save queues nothing', async () => {
    storeDraft({ principal: ME });
    window.pwaRouteDraw.start();
    pressSave();
    await waitFor(async () => (await rows()).length === 1);
    await settle();

    window.pwaRouteDraw.start();
    await waitFor(() => sheet.querySelector('[data-route-draw-submit]')?.disabled);
    expect(sheet.querySelector('[data-route-draw-undo]').disabled).toBe(true);
    expect(sheet.querySelector('[data-route-draw-clear]').disabled).toBe(true);
    expect(sheet.querySelector('#route-draw-name').disabled).toBe(true);
    expect(sheet.querySelector('[data-route-draw-notice]').textContent).toMatch(/waiting to sync/);

    pressSave();
    await settle();
    expect(await rows()).toHaveLength(1);
  });

  it('clears the draft once the page drain lands it', async () => {
    storeDraft({ principal: ME });
    window.pwaRouteDraw.start();
    pressSave();
    await waitFor(async () => (await rows()).length === 1);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"uuid": "u-1", "bounds": [7, 46, 7, 46.001]}', { status: 200 })),
    );
    setOnline(true);

    await window.pwaMutationQueue.drain();

    await waitFor(() => draft() === null);
  });
});

describe('a tagged draft with neither row nor acknowledgement', () => {
  /** Re-save it online and resolve with the key the post carried. */
  async function resave() {
    const calls = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url, init) => {
        calls.push(init);
        return new Response('{"uuid": "u-2", "bounds": [7, 46, 7, 46.001]}', { status: 200 });
      }),
    );
    setOnline(true);
    pressSave();
    await waitFor(() => draft() === null);
    return calls[0].headers['Idempotency-Key'];
  }

  it('is kept with its key, editable, and an unchanged re-save sends the same key', async () => {
    const sent = self.pwaRouteDrawCore.bodyFingerprint(POINTS, 'Mont Fort');
    storeDraft({ key: 'k-unsent', sent, principal: ME });
    document.dispatchEvent(new CustomEvent('pwa:mutation-queue-changed'));
    await settle();
    expect(draft().key).toBe('k-unsent');

    window.pwaRouteDraw.start();
    await settle();
    expect(sheet.querySelector('[data-route-draw-submit]').disabled).toBe(false);

    // The same body under the same key: the server answers with the route
    // the first post made, rather than making a second.
    expect(await resave()).toBe('k-unsent');
  });

  it('gets a new key once it has been edited, so the server never sees an old key with a new body', async () => {
    // Sent as "Mont Fort"; renamed since.
    const sent = self.pwaRouteDrawCore.bodyFingerprint(POINTS, 'Mont Fort');
    storeDraft({ key: 'k-unsent', sent, principal: ME, name: 'Mont Fort by the couloir' });

    window.pwaRouteDraw.start();
    await settle();

    const key = await resave();
    expect(key).not.toBe('k-unsent');
    expect(key).toMatch(/.+/);
  });
});

describe('a save refused for good', () => {
  async function refused() {
    storeDraft({ principal: ME });
    window.pwaRouteDraw.start();
    pressSave();
    await waitFor(async () => (await rows()).length === 1);
    const key = draft().key;
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"error": "route_limit_reached"}', { status: 409 })));
    setOnline(true);
    await window.pwaMutationQueue.drain();
    await waitFor(() => draft() && draft().failed === key);
    return key;
  }

  it('untags the draft, so it is editable again', async () => {
    const key = await refused();

    expect(draft().key).toBeUndefined();
    window.pwaRouteDraw.start();
    await settle();
    expect(sheet.querySelector('[data-route-draw-submit]').disabled).toBe(false);
    expect((await rows())[0].idempotency_key).toBe(key);
  });

  it('Edit takes it back into the draft and opens draw mode on it', async () => {
    const key = await refused();

    await window.pwaRouteDraw.edit(key);

    expect(await rows()).toEqual([]);
    expect(window.pwaRouteDraw.isActive()).toBe(true);
    expect(draft()).toMatchObject({ points: POINTS, name: 'Mont Fort' });
    expect(draft().failed).toBeUndefined();
  });

  it('Edit never mints a new key for a save that landed after all', async () => {
    const key = await refused();
    // Another drain replayed it: its acknowledgement sits beside the row.
    await window.pwaDb.put('meta:app', {
      key: 'mutations.ack:' + key,
      value: { kind: 'route-create', uuid: 'u-landed', at: Date.now() },
    });

    await window.pwaRouteDraw.edit(key);

    expect(await rows()).toEqual([]);
    expect(draft()).toBe(null);
    expect(window.pwaRouteDraw.isActive()).toBe(false);
    expect(toast).toHaveBeenCalledWith('Route saved.');
  });

  it('Discard throws away the row and the draft', async () => {
    const key = await refused();

    await window.pwaRouteDraw.discard(key);

    expect(await rows()).toEqual([]);
    expect(draft()).toBe(null);
  });
});

describe("another account's draft", () => {
  it('is discarded', async () => {
    storeDraft({ principal: 'someone-else' });

    document.dispatchEvent(new CustomEvent('pwa:mutation-queue-changed'));

    await waitFor(() => draft() === null);
  });
});

describe('an online save', () => {
  it('posts directly under the draft key and clears the draft', async () => {
    const calls = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url, init) => {
        calls.push([url, init]);
        return new Response('{"uuid": "u-3", "bounds": [7, 46, 7, 46.001]}', { status: 200 });
      }),
    );
    setOnline(true);
    storeDraft({ principal: ME });
    window.pwaRouteDraw.start();
    pressSave();

    await waitFor(() => draft() === null);
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe(CREATE_URL);
    expect(calls[0][1].headers['Idempotency-Key']).toMatch(/.+/);
    expect(await rows()).toEqual([]);
    expect(toast).toHaveBeenCalledWith('Route saved.');
  });

  it('queues the same save under the same key when the post gets no answer', async () => {
    const keys = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url, init) => {
        keys.push(init.headers['Idempotency-Key']);
        throw new TypeError('Failed to fetch');
      }),
    );
    setOnline(true);
    storeDraft({ principal: ME });
    window.pwaRouteDraw.start();
    pressSave();

    const [row] = await waitFor(async () => {
      const all = await rows();
      return all.length ? all : null;
    });
    expect(row.idempotency_key).toBe(keys[0]);
    expect(draft().key).toBe(keys[0]);
  });

  it('untags the draft when the server refuses it, and names the cap', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"error": "route_limit_reached"}', { status: 409 })));
    setOnline(true);
    storeDraft({ principal: ME });
    window.pwaRouteDraw.start();
    pressSave();

    await waitFor(() => toast.mock.calls.length > 0);
    expect(toast).toHaveBeenCalledWith("You've reached your saved-route limit. Delete one to save another.");
    expect(draft().key).toBeUndefined();
    expect(await rows()).toEqual([]);
  });
});
