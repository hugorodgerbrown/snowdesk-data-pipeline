/*
 * tests/js/test_sync_log.js — Vitest unit tests for static/js/sync_log.js
 * (SNOW-649).
 *
 * The sync log is the panel a user opens when they want to know whether
 * the app has actually talked to the server — the thing you check before
 * heading out with no signal. Its whole contract is that it never leaves
 * the reader guessing: every failure mode has to resolve to a specific
 * sentence, because a panel stuck on "Loading…" reads as "still working"
 * when it means "IndexedDB is gone".
 *
 * That is four distinct outcomes (unavailable / empty / failed / rows) and
 * three of them are error paths, which is exactly the shape a browser test
 * covers badly — tests/e2e/test_sync_log_panel.py managed the flag-off and
 * the two happy-ish cases and could not reach the throwing one at all
 * without a frozen or broken IndexedDB.
 *
 * The module renders on DOMContentLoaded (deliberately — its script tag
 * runs before db.js's, so an eager render would race window.pwaDb into
 * existence), so each test dispatches that event itself.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

document.body.innerHTML = '<ul id="sync-log-list"><li>Loading…</li></ul>';

await import('../../static/js/sync_log.js');

const list = document.getElementById('sync-log-list');

/** Fire the event the module renders on, and let its async body settle. */
async function renderPanel() {
  document.dispatchEvent(new Event('DOMContentLoaded'));
  await vi.waitFor(() => expect(list.textContent).not.toContain('Loading'));
}

/** The single placeholder row's text, or null when real rows are painted. */
function placeholderText() {
  const el = list.querySelector('[data-role="sync-log-placeholder"]');
  return el ? el.textContent : null;
}

beforeEach(() => {
  list.innerHTML = '<li>Loading…</li>';
});

afterEach(() => {
  delete window.pwaDb;
});

describe('when IndexedDB is not available at all', () => {
  it('says so, rather than sitting on "Loading…" forever', async () => {
    await renderPanel();
    expect(placeholderText()).toBe('Sync log unavailable on this device.');
  });

  it('also handles a pwaDb that exists but has no getSyncLog', async () => {
    // A partially-initialised db.js — the Reset Required latch leaves the
    // object in place with a reduced surface.
    window.pwaDb = {};
    await renderPanel();
    expect(placeholderText()).toBe('Sync log unavailable on this device.');
  });
});

describe('when the log is readable but empty', () => {
  it('distinguishes "no syncs yet" from "cannot read the log"', async () => {
    // The distinction matters: one means the app has never reached the
    // server, the other means we cannot tell. They warrant different user
    // action, so they must not share a message.
    window.pwaDb = { getSyncLog: async () => [] };
    await renderPanel();
    expect(placeholderText()).toBe('No syncs yet.');
  });

  it('treats a null reply as empty rather than throwing', async () => {
    window.pwaDb = { getSyncLog: async () => null };
    await renderPanel();
    expect(placeholderText()).toBe('No syncs yet.');
  });
});

describe('when the read throws', () => {
  it('degrades to a message instead of breaking the rest of the page', async () => {
    window.pwaDb = {
      getSyncLog: async () => {
        throw new Error('IDB is on fire');
      },
    };
    await renderPanel();
    expect(placeholderText()).toBe('Could not load the sync log.');
  });
});

describe('painting real rows', () => {
  beforeEach(() => {
    window.pwaDb = {
      getSyncLog: async () => [
        { at: '2026-04-08T09:05:00Z', path: '/api/regions.geojson' },
        { at: '2026-04-08T08:00:00Z', path: '/api/bulletin-groupings/' },
      ],
    };
  });

  it('replaces the loading placeholder with one row per entry', async () => {
    await renderPanel();
    expect(list.querySelectorAll('[data-testid="sync-log-row"]')).toHaveLength(2);
    expect(placeholderText()).toBeNull();
  });

  it('preserves the order it was given — newest first is the caller’s job', async () => {
    await renderPanel();
    const paths = [...list.querySelectorAll('[data-testid="sync-log-row"]')].map(
      (li) => li.firstChild.textContent,
    );
    expect(paths).toEqual(['/api/regions.geojson', '/api/bulletin-groupings/']);
  });

  it('formats the timestamp as HH:MM DD/MM', async () => {
    await renderPanel();
    const at = list.querySelector('[data-testid="sync-log-row"]').lastChild.textContent;
    expect(at).toMatch(/^\d{2}:\d{2} \d{2}\/\d{2}$/);
  });

  it('does not accumulate rows when rendered twice', async () => {
    await renderPanel();
    list.innerHTML = '<li>Loading…</li>';
    await renderPanel();
    expect(list.querySelectorAll('[data-testid="sync-log-row"]')).toHaveLength(2);
  });
});

describe('hostile row data', () => {
  it('falls back to the raw string for an unparseable timestamp', async () => {
    window.pwaDb = {
      getSyncLog: async () => [{ at: 'never', path: '/api/x' }],
    };
    await renderPanel();
    expect(list.querySelector('[data-testid="sync-log-row"]').lastChild.textContent).toBe(
      'never',
    );
  });

  it('renders an empty path rather than "undefined"', async () => {
    window.pwaDb = {
      getSyncLog: async () => [{ at: '2026-04-08T09:05:00Z' }],
    };
    await renderPanel();
    expect(list.querySelector('[data-testid="sync-log-row"]').firstChild.textContent).toBe(
      '',
    );
  });

  it('sets row text via textContent, so a path can never inject markup', async () => {
    window.pwaDb = {
      getSyncLog: async () => [{ at: '2026-04-08T09:05:00Z', path: '<img src=x onerror=1>' }],
    };
    await renderPanel();
    const row = list.querySelector('[data-testid="sync-log-row"]');
    expect(row.querySelector('img')).toBeNull();
    expect(row.firstChild.textContent).toBe('<img src=x onerror=1>');
  });
});
