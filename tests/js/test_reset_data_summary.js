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
  <p id="reset-data-summary-total"></p>
`;

await import('../../static/js/reset_data_summary.js');

const list = document.getElementById('reset-data-summary-list');
const totalEl = document.getElementById('reset-data-summary-total');

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
    note: li.querySelector('p').textContent,
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
  totalEl.textContent = '';
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
    expect(totalEl.textContent).toBe('');
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
    expect(list.querySelectorAll('[data-testid="reset-data-summary-row"]')).toHaveLength(4);
    expect(placeholderText()).toBeNull();
  });

  it('sizes the downloaded maps from the recorded bytes', async () => {
    await renderPanel();
    expect(category('maps').value).toBe('120 MB');
  });

  it('names the shared overview map, which nothing else lists', async () => {
    // SNOW-867 took it off the Manage downloads sheet — it is the app's own
    // map data, not one of the user's downloads, and not in their budget.
    // This panel is where that decision points, so the row has to be here
    // AND has to be marked as shared rather than passed off as a download.
    await renderPanel();
    const rows = [...list.querySelectorAll('[data-testid="reset-data-summary-map"]')];
    expect(rows.map((row) => row.firstChild.textContent)).toEqual([
      'Martigny',
      'Overview mapShared',
    ]);
    expect(rows[1].getAttribute('data-shared')).toBe('true');
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

  it('states the preferences with no figure rather than omitting them', async () => {
    await renderPanel();
    expect(category('preferences').value).toBe('Back to defaults');
    expect(category('preferences').note).toContain('basemap choice');
  });

  it('totals the lot for the line pwa_reset.js quotes', async () => {
    await renderPanel();
    // The full stop is the assertion, not decoration: the total and the
    // "approximate" caveat are two strings joined by a space, and without
    // it they painted as "130 MB Approximate — the browser reports ...".
    expect(totalEl.textContent).toContain('Total on this device: 130 MB.');
    expect(totalEl.textContent).not.toMatch(/\d\s*MB\s+Approximate/);
    expect(window.pwaResetDataSummary.confirmLines()).toEqual([
      'This deletes about 130 MB from this device.',
      '2 changes have not reached the server yet, and will be lost.',
    ]);
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
  it('keeps the category, drops the figure, and flags the total partial', async () => {
    stubDevice({
      areas: [{ id: 'region-ch-4115', name: 'Martigny', bytes: 40 * MB, basemapKey: 'x' }],
      usage: null,
    });
    await renderPanel();
    expect(category('cached').value).toBe('Size unknown');
    expect(category('cached').note).toContain('costs you a reload');
    expect(totalEl.textContent).toContain('At least 40.0 MB');
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
