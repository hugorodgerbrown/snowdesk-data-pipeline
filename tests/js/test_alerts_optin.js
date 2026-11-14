/*
 * tests/js/test_alerts_optin.js — the settings page's push opt-in, run
 * end to end in jsdom: an existing browser subscription is registered
 * again, naming the endpoint pwaDb remembers as the one it replaces, and
 * the new endpoint is stored for next time (SNOW-1494).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/alerts_optin_core.js';

/**
 * Stub the browser push surface, the pwaDb store and fetch, then load
 * alerts_optin.js so it binds to the row on the page.
 *
 * @param {string|undefined} stored the endpoint meta:app remembers
 * @param {string} current the endpoint the browser hands out now
 */
async function loadWith(stored, current) {
  document.body.innerHTML =
    '<div data-alerts-optin><p data-alerts-optin-status></p>' +
    '<button data-alerts-optin-enable hidden></button>' +
    '<button data-alerts-optin-test hidden></button></div>';
  const sub = {
    toJSON: () => ({ endpoint: current, keys: { p256dh: 'p', auth: 'a' } }),
  };
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: {
      ready: Promise.resolve({
        pushManager: { getSubscription: async () => sub },
      }),
    },
  });
  window.PushManager = function PushManager() {};
  globalThis.Notification = { permission: 'granted' };
  const rows = new Map(
    stored === undefined
      ? []
      : [['push.endpoint', { key: 'push.endpoint', value: stored }]],
  );
  self.pwaDb = {
    get: vi.fn(async (_store, key) => rows.get(key)),
    put: vi.fn(async (_store, row) => {
      rows.set(row.key, row);
    }),
  };
  globalThis.fetch = vi.fn(async () => ({ ok: true }));
  vi.resetModules();
  await import('../../static/js/alerts_optin.js');
  await vi.waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  await vi.waitFor(() =>
    expect(rows.get('push.endpoint')?.value).toBe(current),
  );
  return JSON.parse(globalThis.fetch.mock.calls[0][1].body);
}

describe('alerts_optin registration', () => {
  beforeEach(() => {
    delete self.pwaDb;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = '';
  });

  it('names the stored endpoint as the one it replaces and stores the new one', async () => {
    const body = await loadWith(
      'https://push.example/old',
      'https://push.example/new',
    );
    expect(body.endpoint).toBe('https://push.example/new');
    expect(body.replaces).toBe('https://push.example/old');
  });

  it('sends no replaces when nothing was stored', async () => {
    const body = await loadWith(undefined, 'https://push.example/new');
    expect(body).not.toHaveProperty('replaces');
  });

  it('sends no replaces when the endpoint is unchanged', async () => {
    const body = await loadWith(
      'https://push.example/same',
      'https://push.example/same',
    );
    expect(body).not.toHaveProperty('replaces');
  });
});
