/*
 * tests/js/test_map_layer_sync_status_i18n_fallback.js — sync-dot labels
 * with no strings template (SNOW-620).
 *
 * The companion to test_map_layer_sync_status_i18n.js, split off for a
 * mechanical reason: `map_layer_sync_status.js` publishes
 * `window.pwaLayerSyncStatus` through a non-configurable
 * `Object.defineProperty`, so importing it twice in one file throws
 * "Cannot redefine property" no matter how the module registry is reset.
 * One import per file, so one scenario per file.
 *
 * The scenario: a page that loads the module but not the surface partial
 * that renders its strings. Every dot must still carry an accessible name.
 */

import { describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

document.body.innerHTML = `
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

vi.stubGlobal('caches', {
  match: () => Promise.resolve(undefined),
  keys: () => Promise.resolve([]),
  open: () => Promise.resolve({ keys: () => Promise.resolve([]) }),
});

await import('../../static/js/map_layer_sync_status.js');

describe('sync-dot labels without a strings template', () => {
  it('falls back to English rather than leaving the dot unlabelled', async () => {
    await window.pwaLayerSyncStatus.refresh();

    const dot = document.querySelector('[data-overlay-key="l1"] .sync-dot');

    expect(dot.getAttribute('aria-label')).toBe('Not cached — view online first');
    expect(dot.title).toBe('Not cached — view online first');
  });
});
