/*
 * tests/js/test_map_layer_sync_status_i18n.js — translated sync-dot labels
 * (SNOW-620).
 *
 * Its own file, not a block in test_map_layer_sync_status.js, for the same
 * reason test_pwa_version_check_escalation.js is: the module reads its
 * strings ONCE, at IIFE time. A fixture built in `beforeEach` — which is
 * how the main suite works — is built after the import has already
 * happened, so the template has to be mounted before the dynamic import or
 * it is never seen.
 *
 * Ten labels moved out of JS literals here, the largest single group in the
 * ticket. What matters is that the label a screen-reader user hears comes
 * from the catalogue, so it is asserted on the rendered `aria-label`, not
 * on a module export.
 *
 * The no-template case lives in test_map_layer_sync_status_i18n_fallback.js
 * rather than here: the module publishes `window.pwaLayerSyncStatus` with a
 * non-configurable `defineProperty`, so a second import in the same file
 * throws "Cannot redefine property" — one import per file is the ceiling.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const TEMPLATE_ID = 'map-sync-status-strings-template';

/** One overlay row — the minimum needed to observe a painted label.
 *  Shape mirrors buildFixture() in test_map_layer_sync_status.js. */
const ROW_HTML = `
  <ul id="basemap-menu">
    <li role="none">
      <button type="button" class="basemap-menu-item basemap-menu-item--overlay"
              data-overlay-key="l1">
        <span class="sync-dot" data-sync-state="unknown" aria-hidden="true"></span>
        l1
      </button>
    </li>
  </ul>
`;

/**
 * Mount the fixture, optionally with a strings template, then import.
 *
 * @param {Object<string, string>|null} entries
 */
async function mount(entries) {
  document.body.innerHTML = ROW_HTML;
  if (entries) {
    const tpl = document.createElement('template');
    tpl.id = TEMPLATE_ID;
    tpl.innerHTML = Object.entries(entries)
      .map(([key, value]) => `<span data-string="${key}">${value}</span>`)
      .join('');
    document.body.appendChild(tpl);
  }
  vi.resetModules();
  await import('../../static/js/map_layer_sync_status.js');
}

/** A CacheStorage whose every lookup misses, so rows resolve `uncached`. */
function missingCaches() {
  return {
    match: () => Promise.resolve(undefined),
    keys: () => Promise.resolve([]),
    open: () => Promise.resolve({ keys: () => Promise.resolve([]) }),
  };
}

const dotLabel = () =>
  document.querySelector('[data-overlay-key="l1"] .sync-dot').getAttribute('aria-label');

beforeEach(() => {
  vi.stubGlobal('caches', missingCaches());
});

describe('sync-dot labels', () => {
  it('paints the label from the strings template when it is present', async () => {
    await mount({ uncached: 'Nicht zwischengespeichert' });

    await window.pwaLayerSyncStatus.refresh();

    expect(dotLabel()).toBe('Nicht zwischengespeichert');
  });

});
