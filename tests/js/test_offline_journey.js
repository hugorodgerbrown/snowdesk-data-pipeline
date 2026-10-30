/*
 * tests/js/test_offline_journey.js — Vitest/jsdom tests for
 * static/js/offline_journey.js (SNOW-1308), the DOM half of the
 * /offline/ panels.
 *
 * The host markup is lifted out of static/offline.html itself, so these
 * tests also hold the fallback page's copy of the templates working: the
 * same module paints both hosts, and downloaded areas must never vanish
 * from the page a stranded reader can always open.
 *
 * The audit is stubbed to its published surface (`latest`, `refresh`,
 * `syncArea`, `syncAll` and the `snowdesk:offline-readings` event): the
 * panels are a reading layer over its collection, and what is under test
 * here is the reading and the wiring, not the collector.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/offline_audit_core.js';
import '../../static/js/offline_journey_core.js';
import '../../static/js/offline_journey.js';

const SOURCE = readFileSync(join(process.cwd(), 'static', 'js', 'offline_journey.js'), 'utf8');
const HOST = /<div\s+class="audit"\s+id="offline-journey-panel"[\s\S]*?<\/template>\s*<\/div>/
  .exec(readFileSync(join(process.cwd(), 'static', 'offline.html'), 'utf8'))[0]
  .replace(/\shidden>/, '>');

const NOW = new Date().toISOString();

/** A region area whose bucket verifies, with today's content. */
function area(overrides) {
  return {
    id: 'region-CH-4115',
    kind: 'region',
    name: 'Martigny',
    template: 'https://t/{z}/{x}/{y}.pbf',
    z: { 14: [1, 1, 1, 1] },
    deps: ['https://t/style.json'],
    bucketPresent: true,
    entries: ['https://t/style.json', 'https://t/14/1/1.pbf'],
    contentAt: NOW,
    items: {
      trips: [{ uuid: 't1', name: 'Saturday tour', urls: ['/trips/t1/'] }],
      pins: [{ uuid: 'p1', name: '', urls: ['/partials/place-sheet/favourite/p1/?d=x'] }],
    },
    ...(overrides || {}),
  };
}

/** Readings for a signed-in reader holding the trip page and not the pin. */
function readings(areas, overrides) {
  return {
    now: NOW,
    currentPrincipal: 'acct-1',
    shellEntries: [{ url: window.location.origin + '/trips/t1/' }],
    areas: areas || [area()],
    ...(overrides || {}),
  };
}

let api;

/** Mount the host and let the module bind it. */
function mount() {
  document.body.innerHTML = HOST;
  window.pwaOfflineJourney.init();
}

/** Announce one collection, as offline_audit.js does after every read. */
function announce(r, extra) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:offline-readings', {
      detail: { readings: r, canFetch: true, syncAllRequested: false, running: false, ...extra },
    }),
  );
}

/** The panel for one area id. */
function panelFor(id) {
  return document.querySelector('[data-journey-panel="' + id + '"]');
}

beforeAll(() => {
  window.pwaCachedStampCore = undefined;
});

beforeEach(() => {
  api = {
    latest: vi.fn(() => null),
    refresh: vi.fn(async () => {}),
    syncArea: vi.fn(async () => true),
    syncAll: vi.fn(async () => {}),
  };
  window.pwaOfflineAudit = api;
  window.pwaBasemapDownloadCore = { blobFullyCached: vi.fn(() => true) };
  window.pwaBasemapAreaRemoval = {
    available: () => true,
    evict: vi.fn(async () => true),
  };
});

afterEach(() => {
  delete window.pwaOfflineAudit;
  delete window.pwaBasemapDownloadCore;
  delete window.pwaBasemapAreaRemoval;
  document.body.innerHTML = '';
});

describe('painting', () => {
  it('asks the audit for one read on load, and waits for it', () => {
    mount();

    expect(api.refresh).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-journey-waiting]').hidden).toBe(false);
  });

  it('paints from a read already taken, without asking for another', () => {
    api.latest.mockReturnValue({ readings: readings(), canFetch: true });
    mount();

    expect(api.refresh).not.toHaveBeenCalled();
    expect(panelFor('region-CH-4115')).not.toBeNull();
  });

  it('draws one panel per area, with each of your things named', () => {
    mount();
    announce(
      readings([
        area(),
        area({ id: 'custom-a', kind: 'custom', type: 'dropzone', name: 'La Chaux', z: null }),
      ]),
    );

    const panels = document.querySelectorAll('[data-journey-panel]');
    expect([...panels].map((el) => el.querySelector('[data-journey-name]').textContent)).toEqual([
      'Martigny',
      'La Chaux (drop zone)',
    ]);
    const things = panelFor('region-CH-4115').querySelectorAll('[data-journey-thing]');
    expect([...things].map((el) => el.querySelector('[data-journey-thing-name]').textContent)).toEqual(
      ['Saturday tour', 'Unnamed pin'],
    );
    expect([...things].map((el) => el.getAttribute('data-journey-status'))).toEqual(['yes', 'no']);
    expect(panelFor('region-CH-4115').querySelector('[data-journey-verdict]').textContent).toBe(
      'Needs a refresh — tap to fix',
    );
    expect(document.querySelector('[data-journey-waiting]').hidden).toBe(true);
  });

  it('checks the tiles with the stored template', () => {
    mount();
    const a = area();
    announce(readings([a]));

    expect(window.pwaBasemapDownloadCore.blobFullyCached).toHaveBeenCalledWith(
      a.template,
      { z: a.z },
      a.entries,
    );
  });

  it("reads Can't tell when a reading failed", () => {
    mount();
    announce(readings(undefined, { failure: 'TypeError: boom' }));

    const panel = panelFor('region-CH-4115');
    expect(panel.getAttribute('data-journey-verdict-state')).toBe('unknown');
    expect(panel.querySelector('[data-journey-verdict]').textContent).toBe('Can’t tell');
    const stages = panel.querySelectorAll('[data-journey-stage]');
    expect([...stages].map((el) => el.getAttribute('data-journey-status'))).toEqual([
      'unknown',
      'unknown',
      'unknown',
    ]);
  });

  it("reads Can't tell for the map where the tile reading is not here", () => {
    delete window.pwaBasemapDownloadCore;
    mount();
    announce(readings(undefined, { shellEntries: [] }), {});
    const map = panelFor('region-CH-4115').querySelector('[data-journey-stage="map"]');

    expect(map.getAttribute('data-journey-status')).toBe('unknown');
    expect(map.querySelector('[data-journey-stage-note]').textContent).toBe(
      'its map tiles could not be checked here',
    );
  });

  it('links a tile gap to the map’s Manage downloads sheet', () => {
    window.pwaBasemapDownloadCore.blobFullyCached.mockReturnValue(false);
    mount();
    announce(readings());

    const link = panelFor('region-CH-4115').querySelector('[data-journey-fix-link]');
    expect(link.getAttribute('href')).toBe('/map/?panel=downloads');
    expect(link.textContent).toBe('Needs a refresh — open the map');
  });

  it('dates stale bulletins on the panel (SNOW-1139)', () => {
    mount();
    announce(readings([area({ contentAt: '2026-09-01T10:00:00.000Z' })]));

    const note = panelFor('region-CH-4115').querySelector(
      '[data-journey-stage="conditions"] [data-journey-stage-note]',
    );
    expect(note.hidden).toBe(false);
    expect(note.textContent).toBe('from 2026-09-01, an earlier day');
  });

  it('shows an orphaned bucket with its Remove and no Sync', () => {
    mount();
    announce(
      readings([
        {
          id: 'region-CH-9999',
          kind: 'region',
          orphaned: true,
          name: '',
          deps: [],
          bucketPresent: true,
          entries: ['https://t/1.pbf'],
        },
      ]),
    );

    const panel = panelFor('region-CH-9999');
    // No record, so no name: the id is the label, as on the sheet.
    expect(panel.querySelector('[data-journey-name]').textContent).toBe('region-CH-9999');
    expect(panel.querySelector('[data-journey-stage="map"] [data-journey-stage-note]').textContent).toBe(
      'an unfinished download taking up space — remove it',
    );
    expect(panel.querySelector('[data-journey-remove]').hidden).toBe(false);
    expect(panel.querySelector('[data-journey-sync]').hidden).toBe(true);
  });

  it('says so when nothing is downloaded', () => {
    mount();
    announce(readings([]));

    expect(document.querySelector('[data-journey-empty]').hidden).toBe(false);
    expect(document.querySelectorAll('[data-journey-panel]')).toHaveLength(0);
  });

  it('shows a signed-out reader two stages and no fault', () => {
    mount();
    announce(readings(undefined, { currentPrincipal: null }));

    const panel = panelFor('region-CH-4115');
    expect(panel.querySelectorAll('[data-journey-stage]')).toHaveLength(2);
    expect(panel.getAttribute('data-journey-verdict-state')).toBe('ready');
  });

  it('writes names with textContent, never as markup', () => {
    mount();
    announce(readings([area({ name: '<img src=x onerror=alert(1)>' })]));

    expect(document.querySelector('[data-journey-output] img')).toBeNull();
    expect(panelFor('region-CH-4115').textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('carries no innerHTML and no class strings in its source', () => {
    expect(SOURCE).not.toMatch(/\.innerHTML\b/);
    expect(SOURCE).not.toMatch(/\.className\b|classList\./);
  });
});

describe('the controls', () => {
  it('runs Sync now through the audit for that area', async () => {
    mount();
    announce(readings());

    panelFor('region-CH-4115').querySelector('[data-journey-sync]').click();
    await vi.waitUntil(() => api.syncArea.mock.calls.length > 0);

    expect(api.syncArea).toHaveBeenCalledWith('region-CH-4115');
  });

  it('runs the same Sync from a "tap to fix" verdict', async () => {
    mount();
    announce(readings([area({ contentAt: '2026-09-01T10:00:00.000Z' })]));

    panelFor('region-CH-4115').querySelector('button[data-journey-fix]').click();
    await vi.waitUntil(() => api.syncArea.mock.calls.length > 0);

    expect(api.syncArea).toHaveBeenCalledWith('region-CH-4115');
  });

  it('runs "Get ready for offline" from the tap on a ?sync=all page', async () => {
    api.latest.mockReturnValue({ readings: readings(), canFetch: true, syncAllRequested: true });
    mount();
    announce(readings([area({ contentAt: '2026-09-01T10:00:00.000Z' })]));

    panelFor('region-CH-4115').querySelector('button[data-journey-fix]').click();
    await vi.waitUntil(() => api.syncAll.mock.calls.length > 0);

    expect(api.syncArea).not.toHaveBeenCalled();
  });

  it('removes through the shared removal, then re-reads storage', async () => {
    mount();
    announce(readings());
    api.refresh.mockClear();

    panelFor('region-CH-4115').querySelector('[data-journey-remove]').click();
    await vi.waitUntil(() => api.refresh.mock.calls.length > 0);

    expect(window.pwaBasemapAreaRemoval.evict).toHaveBeenCalledWith(['region-CH-4115']);
    expect(window.pwaBasemapAreaRemoval.evict.mock.invocationCallOrder[0]).toBeLessThan(
      api.refresh.mock.invocationCallOrder[0],
    );
  });

  it('offers no Remove where the removal cannot run', () => {
    window.pwaBasemapAreaRemoval.available = () => false;
    mount();
    announce(readings());

    expect(panelFor('region-CH-4115').querySelector('[data-journey-remove]').hidden).toBe(true);
  });

  it('offers no Sync on a host that cannot fetch, and links to /offline/ instead', () => {
    mount();
    announce(readings([area({ contentAt: '2026-09-01T10:00:00.000Z' })]), { canFetch: false });

    const panel = panelFor('region-CH-4115');
    expect(panel.querySelector('[data-journey-sync]').hidden).toBe(true);
    expect(panel.querySelector('[data-journey-fix-link]').getAttribute('href')).toBe(
      '/offline/?sync=region-CH-4115',
    );
  });

  it('disables every control while the audit is busy', () => {
    mount();
    announce(readings(), { running: true });

    const sync = panelFor('region-CH-4115').querySelector('[data-journey-sync]');
    expect(sync.disabled).toBe(true);
  });
});
