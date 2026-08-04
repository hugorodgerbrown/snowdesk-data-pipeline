/*
 * tests/js/test_offline_page_reset.js — the §12.7 "Reset local data" control on
 * the offline fallback page (SNOW-378 escape hatch, SNOW-607 follow-up).
 *
 * SNOW-607 put `@never_cache` on the manage page, which took the only copy of
 * the escape hatch offline with it. `static/offline.html` is pre-cached, so it
 * now carries a second copy of the SAME control — same `[data-pwa-reset-trigger]`
 * contract, same `pwa_reset.js` binding, same `window.confirm` gate. These tests
 * assert that wiring against the shipped file rather than a fixture copy of it,
 * so the two can't drift.
 *
 * Harness notes:
 *   - The page's markup is read off disk and mounted into jsdom. `innerHTML`
 *     never executes scripts, so the page's inline bootstrap is pulled out and
 *     run explicitly — the shipped source, not a paraphrase of it. It registers
 *     a `DOMContentLoaded` listener, which jsdom has already fired by the time a
 *     test runs, so the tests dispatch the event themselves.
 *   - `pwa_reset.js` is imported ONCE, inside the second block's `beforeAll` —
 *     it publishes `window.pwaResetLocalData` non-configurably, so it can be
 *     neither re-imported nor unpublished. That ordering is what lets the first
 *     block observe the "script never arrived" state, which is the state an
 *     offline load lands in today (see the header comment in
 *     `static/offline.html`: the script is a subresource and is not pre-cached).
 *   - jsdom ships neither `navigator.serviceWorker` nor `caches`, so the wipe
 *     reduces to its IndexedDB and web-storage steps here.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

// Resolved from the Vitest root (the repo root, per vitest.config.mjs) rather
// than `import.meta.url` — under the jsdom environment that is an http:// URL,
// which `fileURLToPath` rejects.
const OFFLINE_HTML = readFileSync(resolve(process.cwd(), 'static/offline.html'), 'utf-8');

const PANEL_ID = 'pwa-reset-panel';
const RESET_SCRIPT = '/static/js/pwa_reset.js';

/** Parse the shipped page into an inert document. */
function parseOfflinePage() {
  return new DOMParser().parseFromString(OFFLINE_HTML, 'text/html');
}

/**
 * Mount the shipped page's body into jsdom and return its inline bootstrap.
 *
 * @returns {string} Concatenated source of every inline `<script>` on the page.
 */
function mountOfflinePage() {
  const doc = parseOfflinePage();
  document.body.innerHTML = doc.body.innerHTML;
  return Array.from(doc.querySelectorAll('script:not([src])'))
    .map((script) => script.textContent)
    .join('\n');
}

/**
 * Run the page's own inline bootstrap, then fire the event it waits on.
 *
 * @param {string} source
 */
function runBootstrap(source) {
  // eslint-disable-next-line no-new-func -- executing the shipped page source is the point.
  new Function(source)();
  document.dispatchEvent(new Event('DOMContentLoaded'));
}

/**
 * Stub `indexedDB.deleteDatabase` with a request that reports success, and
 * return the spy so a test can assert whether the wipe ran at all.
 */
function stubDeleteDatabase() {
  const spy = vi.fn(() => {
    const req = {};
    setTimeout(() => {
      if (typeof req.onsuccess === 'function') req.onsuccess();
    }, 0);
    return req;
  });
  vi.spyOn(indexedDB, 'deleteDatabase').mockImplementation(spy);
  return spy;
}

describe('the shipped offline page', () => {
  it('carries the reset trigger, hidden until the mechanism is proven present', () => {
    const doc = parseOfflinePage();
    const panel = doc.getElementById(PANEL_ID);

    expect(panel).not.toBeNull();
    expect(panel.hasAttribute('hidden')).toBe(true);
    expect(panel.querySelector('[data-pwa-reset-trigger]')).not.toBeNull();
  });

  it('loads the shared reset module rather than restating the wipe', () => {
    const doc = parseOfflinePage();
    const sources = Array.from(doc.querySelectorAll('script[src]')).map((s) =>
      s.getAttribute('src'),
    );

    expect(sources).toEqual([RESET_SCRIPT]);
    // The six-step wipe belongs to pwa_reset.js alone — a second copy here
    // would drift the moment either side changed.
    expect(OFFLINE_HTML).not.toContain('deleteDatabase');
    expect(OFFLINE_HTML).not.toContain('caches.keys');
  });
});

describe('the offline page before pwa_reset.js has loaded', () => {
  it('leaves the reset panel hidden rather than showing a dead control', () => {
    const bootstrap = mountOfflinePage();

    runBootstrap(bootstrap);

    expect(document.getElementById(PANEL_ID).hidden).toBe(true);
  });
});

describe('the offline page once pwa_reset.js has loaded', () => {
  /** @type {string} */
  let bootstrap;

  beforeAll(async () => {
    // Mount first: the module binds every trigger in the DOM as it loads.
    bootstrap = mountOfflinePage();
    await import('../../static/js/pwa_reset.js');
  });

  beforeEach(() => {
    document.getElementById(PANEL_ID).hidden = true;
    vi.spyOn(indexedDB, 'databases').mockResolvedValue([{ name: 'snowdesk-pwa-v1' }]);
    // The clean run reloads; jsdom answers navigation with a virtual-console
    // error, which would otherwise be noise on an otherwise-passing run.
    vi.spyOn(console, 'error').mockImplementation(() => {});
    window.pwaTelemetry = { emit: vi.fn() };
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete window.pwaTelemetry;
  });

  it('reveals the reset panel', () => {
    runBootstrap(bootstrap);

    expect(document.getElementById(PANEL_ID).hidden).toBe(false);
  });

  it('binds the trigger to the shared wipe, behind the confirmation dialogue', async () => {
    const deleteDb = stubDeleteDatabase();
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    document.querySelector('[data-pwa-reset-trigger]').click();

    await vi.waitFor(() => expect(deleteDb).toHaveBeenCalledWith('snowdesk-pwa-v1'));
    expect(window.confirm).toHaveBeenCalledTimes(1);
    await vi.waitFor(() =>
      expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('pwa.reset.user_initiated', {
        ok: true,
        failed_steps: [],
      }),
    );
  });

  it('wipes nothing when the confirmation is declined', async () => {
    const deleteDb = stubDeleteDatabase();
    vi.spyOn(window, 'confirm').mockReturnValue(false);

    document.querySelector('[data-pwa-reset-trigger]').click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(deleteDb).not.toHaveBeenCalled();
  });
});
