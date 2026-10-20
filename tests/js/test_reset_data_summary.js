/*
 * tests/js/test_reset_data_summary.js — Vitest unit tests for
 * static/js/reset_data_summary.js (SNOW-860).
 *
 * The DOM half of the "Reset local data" breakdown, tested the way
 * tests/js/test_sync_log.js tests its twin: the loading→painted swap, and
 * the fact that every failure mode resolves to a specific sentence rather
 * than leaving the panel on "Loading…" — which reads as "still working"
 * when it means "IndexedDB is gone", beside a control whose whole job is
 * recovering from exactly that.
 *
 * Two assertions here are load-bearing beyond the painting:
 *
 *   - it counts `queue:mutations`, SNOW-376's pending mutations, and NOT
 *     `queue:events`, which is the SNOW-385 telemetry buffer. The two
 *     stores are one line apart in db.js's header and only one of them is
 *     "unsent changes"; the other is analytics payloads;
 *   - `confirmLines()` is what pwa_reset.js's dialog quotes, so it must
 *     report the total that was painted, and nothing at all when the paint
 *     failed.
 *
 * The module renders on DOMContentLoaded (its script tag runs before
 * db.js's, so an eager render would race window.pwaDb into existence), so
 * each test dispatches that event itself.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/basemap_download_core.js';
import '../../static/js/basemap_manage_core.js';
import '../../static/js/reset_data_summary_core.js';

const MB = 1024 * 1024;

document.body.innerHTML = `
  <ul id="reset-data-summary-list"><li>Loading…</li></ul>
`;

await import('../../static/js/reset_data_summary.js');

const list = document.getElementById('reset-data-summary-list');

/** Fire the event the module renders on, and let its async body settle. */
async function renderPanel() {
  document.dispatchEvent(new Event('DOMContentLoaded'));
  await vi.waitFor(() => expect(list.textContent).not.toContain('Loading'));
}

/** The single placeholder row's text, or null when real rows are painted. */
function placeholderText() {
  const el = list.querySelector('[data-role="reset-data-summary-placeholder"]');
  return el ? el.textContent : null;
}

/** One category row's `{label, value, note}`, by its `data-category`. */
function category(name) {
  const li = list.querySelector(`[data-category="${name}"]`);
  if (!li) return null;
  return {
    label: li.querySelector('span').textContent,
    value: li.querySelector('[data-role="reset-data-summary-value"]').textContent,
    note: li.querySelector('p') ? li.querySelector('p').textContent : null,
    warned: li
      .querySelector('[data-role="reset-data-summary-value"]')
      .className.includes('status-warning'),
  };
}

/**
 * Stub the three sources the panel reads.
 *
 * @param {{areas?: Array<Object>, mutations?: number,
 *   usage?: number|null}} options
 */
function stubDevice(options) {
  const opts = options || {};
  window.pwaBasemapAreas = {
    downloadedAreas: async () => opts.areas || [],
  };
  window.pwaDb = {
    count: vi.fn(async () => opts.mutations || 0),
  };
  if (opts.usage === null) {
    delete navigator.storage;
  } else {
    Object.defineProperty(navigator, 'storage', {
      value: { estimate: async () => ({ usage: opts.usage, quota: 4000 * MB }) },
      configurable: true,
      writable: true,
    });
  }
}

beforeEach(() => {
  list.innerHTML = '<li>Loading…</li>';
});

afterEach(() => {
  delete window.pwaDb;
  delete window.pwaBasemapAreas;
  delete navigator.storage;
  vi.restoreAllMocks();
});

describe('when the device cannot answer at all', () => {
  it('says so, rather than sitting on "Loading…" forever', async () => {
    await renderPanel();
    expect(placeholderText()).toBe('Storage details unavailable on this device.');
  });

  it('reports a failed read as a failed read', async () => {
    stubDevice({});
    window.pwaBasemapAreas = {
      downloadedAreas: async () => {
        throw new Error('IDB is on fire');
      },
    };
    await renderPanel();
    expect(placeholderText()).toBe('Could not read what is stored on this device.');
  });
});

describe('painting the four categories', () => {
  beforeEach(() => {
    stubDevice({
      areas: [
        { id: 'region-ch-4115', name: 'Martigny', bytes: 100 * MB, savedAt: '', basemapKey: 'x' },
        {
          id: 'base-swisstopo_winter',
          name: 'Overview map',
          bytes: 20 * MB,
          savedAt: '',
          basemapKey: 'swisstopo_winter',
        },
      ],
      mutations: 2,
      usage: 130 * MB,
    });
  });

  it('replaces the loading placeholder with one row per category', async () => {
    await renderPanel();
    // Five: the four categories, and the shared basemap tiles this device
    // holds (SNOW-1138 — a line of their own).
    expect(list.querySelectorAll('[data-testid="reset-data-summary-row"]')).toHaveLength(5);
    expect(placeholderText()).toBeNull();
  });

  it('sizes the downloaded maps from the recorded bytes, base tiles excluded', async () => {
    // SNOW-1138: the same figure the Manage downloads sheet's total states
    // — that sheet excludes the shared tiles, so this does too.
    await renderPanel();
    expect(category('maps').value).toBe('100 MB');
  });

  it('names the shared basemap tiles on a line of their own', async () => {
    // SNOW-867 took them off the Manage downloads sheet — the app's own map
    // data, not the user's downloads, and not in their budget. This panel
    // is where that decision points, so they have to be here; SNOW-1138
    // moved them out of the "Downloaded maps" list and figure, which they
    // made larger than the sheet's own total.
    await renderPanel();
    const rows = [...list.querySelectorAll('[data-testid="reset-data-summary-map"]')];
    expect(rows.map((row) => row.firstChild.textContent)).toEqual(['Martigny']);
    expect(category('shared').label).toBe('Shared basemap tiles');
    expect(category('shared').value).toBe('20.0 MB');
    expect(category('shared').note).toContain('Manage downloads');
    expect(list.textContent).not.toContain('Overview map');
  });

  it('has no shared line on a device with no base layers', async () => {
    stubDevice({
      areas: [{ id: 'region-ch-4115', name: 'Martigny', bytes: 100 * MB, basemapKey: 'x' }],
      usage: 130 * MB,
    });
    await renderPanel();
    expect(category('shared')).toBeNull();
    expect(list.querySelectorAll('[data-testid="reset-data-summary-row"]')).toHaveLength(4);
  });

  it('carries no note on the maps row once there is a list under it', async () => {
    // The list IS the disclosure; a sentence above it saying these are
    // areas you downloaded restated the heading.
    await renderPanel();
    expect(category('maps').note).toBeNull();
  });

  it('warns on the unsent changes, and only on those', async () => {
    await renderPanel();
    expect(category('unsent').value).toBe('2');
    expect(category('unsent').warned).toBe(true);
    expect(category('maps').warned).toBe(false);
  });

  it('attributes the remaining origin usage to cached pages', async () => {
    await renderPanel();
    expect(category('cached').value).toBe('10.0 MB');
  });

  it('names what reverts to the default, and shows no figure for it', async () => {
    await renderPanel();
    // The row carries no right-hand value at all: a handful of web-storage
    // keys is not a size, and "Back to defaults" in the figure column said
    // less than naming the basemap and viewport does.
    expect(category('preferences').label).toBe('Map Viewport');
    expect(category('preferences').value).toBe('');
    expect(category('preferences').note).toContain('OpenFreeMap');
  });

  it('totals the lot for the line pwa_reset.js quotes', async () => {
    await renderPanel();
    // The panel shows no total of its own — the confirmation dialog is the
    // one place a total is stated, and it reads the same summary.
    expect(list.textContent).not.toContain('Total on this device');
    expect(window.pwaResetDataSummary.confirmLines()).toEqual([
      'This deletes about 130 MB from this device.',
      '2 changes have not reached the server yet, and will be lost.',
    ]);
  });
});

describe('an orphaned bucket (SNOW-1138)', () => {
  it('asks the shared reader to measure it, as the Manage downloads sheet does', async () => {
    // Called without `measureBytes`, an orphan read 0 MB here while the
    // sheet sized it by walking the bucket.
    stubDevice({ usage: 50 * MB });
    const measureBucketBytes = vi.fn(async () => 3 * MB);
    const downloadedAreas = vi.fn(async (options) => [
      {
        id: 'region-ch-9999',
        orphaned: true,
        basemapKey: null,
        savedAt: '',
        bytes: await options.measureBytes('region-ch-9999'),
      },
    ]);
    window.pwaBasemapAreas = { downloadedAreas, measureBucketBytes };

    await renderPanel();

    expect(measureBucketBytes).toHaveBeenCalledWith('region-ch-9999');
    expect(category('maps').value).toBe('3.0 MB');
  });

  it('is sized by the real reader’s walk of the bucket', async () => {
    // End to end through basemap_downloaded_areas.js: no record, one
    // bucket, entries carrying Content-Length.
    await import('../../static/js/basemap_downloaded_areas.js');
    const reader = window.pwaBasemapAreas;
    stubDevice({ usage: 50 * MB });
    window.pwaBasemapAreas = reader;
    window.pwaDb.get = vi.fn(async () => undefined);
    const entries = [
      { url: 'https://t/12/1/1.pbf', length: 2 * MB },
      { url: 'https://t/12/1/2.pbf', length: 1 * MB },
    ];
    vi.stubGlobal('caches', {
      keys: async () => ['snowdesk-basemap-pinned-region-ch-9999'],
      open: async () => ({
        keys: async () => entries.map((entry) => ({ url: entry.url })),
        match: async (request) => {
          const found = entries.find((entry) => entry.url === request.url);
          return { headers: { get: () => String(found.length) } };
        },
      }),
    });

    await renderPanel();

    const rows = [...list.querySelectorAll('[data-testid="reset-data-summary-map"]')];
    expect(rows.map((row) => row.firstChild.textContent)).toEqual(['region-ch-9999']);
    expect(category('maps').value).toBe('3.0 MB');
    vi.unstubAllGlobals();
  });
});

describe('the unsent-changes count', () => {
  it('reads queue:mutations, never queue:events', async () => {
    // db.js:12-13: `queue:mutations` is SNOW-376's pending mutations;
    // `queue:events` is the SNOW-385 telemetry buffer. Counting the latter
    // would put a number of analytics payloads under a heading reading
    // "unsent changes" — a warning about data the user never made.
    stubDevice({ mutations: 3, usage: 0 });
    await renderPanel();
    expect(window.pwaDb.count).toHaveBeenCalledWith('queue:mutations');
    expect(window.pwaDb.count).not.toHaveBeenCalledWith('queue:events');
  });

  it('reads as none, unwarned, when the queue cannot be counted', async () => {
    stubDevice({ usage: 0 });
    window.pwaDb.count = async () => {
      throw new Error('no such store');
    };
    await renderPanel();
    expect(category('unsent').value).toBe('0');
    expect(category('unsent').warned).toBe(false);
    expect(category('unsent').note).toBe('Nothing is waiting to be sent.');
  });
});

describe('a device with nothing downloaded', () => {
  it('says so on the maps row rather than leaving it bare', async () => {
    stubDevice({ usage: 4 * MB });
    await renderPanel();
    expect(category('maps').value).toBe('0 MB');
    expect(category('maps').note).toBe('Nothing downloaded on this device.');
    expect(list.querySelectorAll('[data-testid="reset-data-summary-map"]')).toHaveLength(0);
  });
});

describe('a browser with no storage.estimate()', () => {
  it('keeps the category and drops the figure when the size is unknown', async () => {
    stubDevice({
      areas: [{ id: 'region-ch-4115', name: 'Martigny', bytes: 40 * MB, basemapKey: 'x' }],
      usage: null,
    });
    await renderPanel();
    expect(category('cached').value).toBe('Size unknown');
    expect(category('cached').note).toContain('saved by the device as you browse');
    // No panel total to go partial, but the dialog still states a floor.
    expect(window.pwaResetDataSummary.confirmLines()[0]).toContain('40.0 MB');
  });
});

describe('the strings contract', () => {
  it('reads its copy from the template the partial renders', async () => {
    // window.pwaStrings.read() — makemessages never scans JavaScript, so
    // the literals in the module are the English fallback and the template
    // is what a locale actually translates.
    const template = document.createElement('template');
    template.id = 'reset-data-summary-strings-template';
    template.innerHTML = '<span data-string="maps">Cartes téléchargées</span>';
    document.body.appendChild(template);
    vi.resetModules();
    stubDevice({ usage: 0 });

    await import('../../static/js/reset_data_summary.js');
    await renderPanel();

    expect(category('maps').label).toBe('Cartes téléchargées');
    template.remove();
  });
});

describe('when the device does not answer at all', () => {
  /*
   * The file header already says a failed read must not leave the panel
   * on "Loading…" — but every test above it makes the read FAIL, and
   * storage in trouble does not fail. It hangs.
   *
   * This panel was photographed reading "Loading…" beside the offline
   * check reading "Checking…", on the same iPad, in the same session, for
   * exactly that reason: a `catch` written against a rejection the device
   * never produced. See docs/decisions/bounded-offline-read-paths.md —
   * the same rule sw.js applies to the network, applied to storage.
   */

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Fire the render, run the clock past every budget, and settle. */
  async function renderPanelUnderFakeClock() {
    document.dispatchEvent(new Event('DOMContentLoaded'));
    await vi.advanceTimersByTimeAsync(60000);
  }

  it('gives up on a read that never comes back', async () => {
    stubDevice({});
    window.pwaBasemapAreas = {
      downloadedAreas: () => new Promise(() => {}),
    };

    await renderPanelUnderFakeClock();

    expect(placeholderText()).toBe('Could not read what is stored on this device.');
  });

  it('still paints the downloads when only the byte total hangs', async () => {
    // `storage.estimate()` walks every store on the origin, so it is the
    // slowest read on the page by a distance — and it answers nothing the
    // reader came for. Losing it must not cost them the list.
    stubDevice({ areas: [] });
    Object.defineProperty(navigator, 'storage', {
      value: { estimate: () => new Promise(() => {}) },
      configurable: true,
      writable: true,
    });

    await renderPanelUnderFakeClock();

    expect(placeholderText()).toBeNull();
    expect(category('maps')).not.toBeNull();
  });

  it('still paints the downloads when sizing an orphan hangs', async () => {
    // SNOW-1138 review: a large orphan's walk is one cache.match() per
    // entry. It must cost the orphan its figure, not the panel its list.
    stubDevice({ usage: 50 * MB });
    window.pwaBasemapAreas = {
      measureBucketBytes: () => new Promise(() => {}),
      downloadedAreas: async (options) => [
        {
          id: 'region-ch-9999',
          orphaned: true,
          basemapKey: null,
          savedAt: '',
          bytes: await options.measureBytes('region-ch-9999'),
        },
      ],
    };

    await renderPanelUnderFakeClock();

    expect(placeholderText()).toBeNull();
    const rows = [...list.querySelectorAll('[data-testid="reset-data-summary-map"]')];
    expect(rows.map((row) => row.firstChild.textContent)).toEqual(['region-ch-9999']);
    expect(category('maps').value).toBe('0 MB');
  });
});
