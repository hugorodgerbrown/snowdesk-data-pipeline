/*
 * tests/js/test_map_region_panel_prefetch.js — how the region + date panel
 * gets its content (SNOW-879, static/js/map_region_panel.js).
 *
 * The panel was the slowest surface on the map to open, and its own header
 * flagged the fetch sequencing as untested. This is that coverage.
 *
 * What was wrong is what these assert against. `render` awaited the region
 * summary, THEN awaited /api/resorts-by-region/, and only then cleared the
 * panel and appended anything — so pressing the chip disclosed an empty box
 * and held it for two round trips that had nothing to do with each other.
 *
 * Four claims:
 *
 *   the resort lookup is fetched without waiting to be asked for, because it
 *   is the same answer for every region;
 *   the summary fetch does not queue behind it;
 *   the panel has words in it on the frame the chip is pressed, and "still
 *   coming" is not the same sentence as "unavailable offline";
 *   a region already looked at is repainted from cache, with no refetch;
 *   and (SNOW-880) a placeholder never replaces an answer already on screen.
 *
 * Scenario: none — this is fetch sequencing and cache state, which jsdom
 * holds exactly; a browser would add a WebGL map and tell us nothing more.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const SUMMARY_URL = '/api/region/XX-0000/summary/';
const RESORTS_URL = '/api/resorts-by-region/';

document.body.innerHTML = `
  <div id="map"
       data-region-summary-url="${SUMMARY_URL}"
       data-resorts-by-region-url="${RESORTS_URL}"></div>
  <div id="season-ribbon">
    <button id="region-readout" aria-expanded="false"></button>
    <div id="region-panel" hidden></div>
    <template id="region-panel-strings-template">
      <span data-string="resorts">Resorts in this region</span>
      <span data-string="unavailable">Region details are unavailable offline.</span>
      <span data-string="loading">Loading region details…</span>
      <span data-string="pinned">Pinned regions</span>
      <span data-string="pinned-failed">Your pinned regions couldn't be loaded.</span>
      <span data-string="pin-region">Pin this region</span>
      <span data-string="unpin-region">Unpin this region</span>
      <span data-string="pin-failed">That region couldn't be pinned. Try again.</span>
    </template>
  </div>
`;

// No data-region-pin-list-url: the pinned section is a signed-in concern and
// is covered by test_map_region_panel_pins.js. This suite is the two fetches.

// Every request is held open, so a test can assert what is IN FLIGHT rather
// than only what settled — which is the whole subject here.
/** @type {Array<{url: string, resolve: Function}>} */
let inFlight = [];

const fetchMock = vi.fn((url) => new Promise((resolve) => {
  inFlight.push({ url, resolve });
}));
vi.stubGlobal('fetch', fetchMock);

/** Answer one held request as the server would. */
function answer(url, body) {
  const hit = inFlight.find((r) => r.url.startsWith(url));
  if (!hit) throw new Error(`no request in flight for ${url}`);
  inFlight = inFlight.filter((r) => r !== hit);
  hit.resolve({ ok: true, json: () => Promise.resolve(body) });
}

/** Whether a request for `url` is currently out. */
const outFor = (url) => inFlight.some((r) => r.url.startsWith(url));

/** Let the module's promise chains run to completion.
 *
 * Microtasks only — the suite runs on fake timers so the idle warm's
 * timeout fallback can be driven deliberately below. */
async function settle() {
  for (let i = 0; i < 12; i += 1) await Promise.resolve();
}

vi.useFakeTimers();
await import('../../static/js/map_region_panel.js');
// The module schedules its resort-lookup warm at import. jsdom ships no
// requestIdleCallback, so the timeout fallback is what fires here. It is
// deliberately left UNANSWERED: `ensureResorts` hands every later caller
// this same in-flight promise, which is what the concurrency assertions
// below are able to observe.
await vi.advanceTimersByTimeAsync(1300);

const chip = document.getElementById('region-readout');
const panel = document.getElementById('region-panel');

/** Announce a region selection the way map.js does. */
function selectRegion(regionId) {
  document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
    detail: { region_id: regionId, region_name: regionId },
  }));
}

/** Open the panel if it is shut. The chip TOGGLES, so a second press on an
 * already-open panel would close it. */
function openPanel() {
  if (panel.hidden) chip.click();
}

beforeEach(() => {
  fetchMock.mockClear();
});

describe('the resort lookup', () => {
  it('is fetched at idle, before any region has been selected', () => {
    // Nothing has been pressed and no region is selected, yet the request
    // is already out. It is the same answer for every region, so waiting
    // for a selection to ask for it only ever cost the user the wait.
    expect(outFor(RESORTS_URL)).toBe(true);
  });
});

describe('opening the panel', () => {
  // One narrative, in file order: the panel is opened once and then driven
  // forward. The module fetches the resort lookup once per page and caches
  // the summary per region, so tearing this down and rebuilding it between
  // assertions would be testing a fresh page rather than a used one.
  it('runs the summary fetch alongside the resort lookup, not behind it', async () => {
    selectRegion('CH-4115');
    openPanel();
    await settle();

    // Neither request has been answered and both are out. Before SNOW-879
    // these were sequential awaits — the resort lookup did not start until
    // the summary had already come back.
    expect(outFor(SUMMARY_URL.replace('XX-0000', 'CH-4115'))).toBe(true);
    expect(outFor(RESORTS_URL)).toBe(true);
  });

  it('says it is loading rather than showing an empty box', () => {
    // Still nothing answered. The panel is open and has words in it, where
    // it used to be a blank box for the length of two round trips.
    expect(panel.hidden).toBe(false);
    expect(panel.textContent).toContain('Loading region details');
    // And it does NOT claim to be offline — that is a different answer, and
    // giving it here would be a wrong one rather than an early one.
    expect(panel.textContent).not.toContain('unavailable offline');
  });

  it('paints the summary and the resorts once both land', async () => {
    answer(RESORTS_URL, { 'CH-4115': ['Verbier', 'La Chaux'] });
    answer(SUMMARY_URL.replace('XX-0000', 'CH-4115'), {
      html: '<p class="summary">Considerable</p>',
    });
    await settle();

    expect(panel.querySelector('.summary')).not.toBeNull();
    expect(panel.textContent).toContain('Verbier');
    expect(panel.textContent).not.toContain('Loading region details');
  });
});

describe('going back to a region already looked at', () => {
  it('repaints from cache without refetching its summary', async () => {
    // Away to a second region...
    selectRegion('CH-4222');
    await settle();
    answer(SUMMARY_URL.replace('XX-0000', 'CH-4222'), {
      html: '<p class="summary">Moderate</p>',
    });
    await settle();
    expect(panel.querySelector('.summary').textContent).toBe('Moderate');

    // ...and back. The resort lookup is long since resolved and this
    // region's summary is cached, so there is nothing left to ask for.
    fetchMock.mockClear();
    selectRegion('CH-4115');
    await settle();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(panel.querySelector('.summary').textContent).toBe('Considerable');
    expect(panel.textContent).toContain('Verbier');
  });
});

describe('changing region with the panel already open', () => {
  // SNOW-880. SNOW-879 pre-painted the placeholder on every render, so this
  // move went body -> placeholder -> body: the panel collapsed to two lines
  // and sprang back within about a tenth of a second. A flash, not loading.
  it('holds the previous answer rather than collapsing to a placeholder', async () => {
    // CH-4115 is on screen from the suite above, with its resorts.
    expect(panel.querySelector('.summary').textContent).toBe('Considerable');

    // A region never looked at before, so this cannot be served from cache.
    selectRegion('CH-9999');
    await settle();

    // The request is out and nothing has come back — and the panel still
    // shows the answer it had. No placeholder, and no collapse.
    expect(outFor(SUMMARY_URL.replace('XX-0000', 'CH-9999'))).toBe(true);
    expect(panel.textContent).not.toContain('Loading region details');
    expect(panel.querySelector('.summary').textContent).toBe('Considerable');

    // One change, when the new answer is ready.
    answer(SUMMARY_URL.replace('XX-0000', 'CH-9999'), {
      html: '<p class="summary">Low</p>',
    });
    await settle();
    expect(panel.querySelector('.summary').textContent).toBe('Low');
  });
});

describe('opening onto a region picked while the panel was shut', () => {
  it('shows the placeholder rather than the region the user has left', async () => {
    // Shut the panel while CH-9999's answer is on screen.
    chip.click();
    expect(panel.hidden).toBe(true);

    // Pick a different region from the map. Nothing renders — the panel is
    // shut — so its body still belongs to CH-9999.
    selectRegion('CH-7777');
    await settle();

    chip.click();
    await settle();

    // The stale body is gone. Keeping it would have put one region's
    // breadcrumb under another region's name in the chip above.
    expect(panel.hidden).toBe(false);
    expect(panel.textContent).not.toContain('Low');
    expect(panel.textContent).toContain('Loading region details');
  });
});
