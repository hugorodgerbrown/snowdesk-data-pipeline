/*
 * tests/js/test_map_region_sheet.js — the region sheet
 * (static/js/map_region_sheet.js, SNOW-1098; every width since SNOW-1173).
 *
 * A tap opens it at peek, painted from the page at once, and fetches its
 * body, which is then kept and reused for the same region and day; a
 * selection that was not a tap opens it too; while it is open it follows the selection and the
 * date, and a deselect closes it; a failed fetch says so; and the pin and
 * download proxies press, and mirror, the roundels they stand for. A
 * resort named in it opens the resort's own sheet rather than leaving the
 * map (SNOW-1161). While the timeline plays (SNOW-1239) the sheet stays open
 * under a "Playback in progress" band, fetches nothing, and loads once on
 * stop.
 *
 * The module wires itself at import against the elements present then, and
 * its listeners live on `document` for the rest of the file, so the page is
 * built and the modules imported ONCE; each test starts from a deselected,
 * closed sheet and fresh roundels instead.
 * jsdom's `matchMedia` stub (tests/js/setup.js) answers `matches: false`,
 * which map_sheet.js reads as a phone; the 'on a desktop' cases stub it
 * to `matches: true`, where the same sheet is the inset panel.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const BODY = `
  <div data-sheet-peek>
    <h2>Martigny-Verbier</h2>
    <button type="button" data-region-sheet-proxy="map-region-pin-control" aria-label="Pin this region"></button>
    <button type="button" data-region-sheet-proxy="map-download-control" aria-label="Download this region"></button>
  </div>
  <div data-testid="region-sheet-bulletin"><h3>Avalanche problems</h3></div>
  <a href="/resorts/verbier/" data-resort-slug="verbier">Verbier</a>
`;

document.body.innerHTML = `
  <div id="map" data-region-sheet-url="/partials/region-sheet/XX-0000/"></div>
  <button id="map-region-pin-control" aria-pressed="false" aria-label="Pin this region"></button>
  <button id="map-download-control" aria-label="Download this region"></button>
  <div id="region-sheet" data-overlay hidden tabindex="-1"
       data-detents="peek,half,full" data-detent-initial="peek">
    <button type="button" data-sheet-handle aria-label="Resize panel"></button>
  </div>
  <template id="sheet-playback-band-template"><div role="status"><p>Playback in progress</p></div></template>
  <template id="region-sheet-strings-template">
    <span data-string="loading">Loading region details…</span>
    <span data-string="failed">Region details are unavailable offline.</span>
  </template>
  <template id="region-sheet-peek-template"><div data-sheet-peek>
    <span class="danger-tile" data-level="no_rating" data-region-sheet-slot="rating">–</span>
    <h2 data-region-sheet-slot="name"></h2>
    <button type="button" data-region-sheet-proxy="map-region-pin-control" aria-label="Pin this region"></button>
    <button type="button" data-region-sheet-proxy="map-download-control" aria-label="Download this region"></button>
  </div></template>
`;
await import('../../static/js/i18n_strings.js');
await import('../../static/js/sheet_detents_core.js');
await import('../../static/js/map_sheet.js');
await import('../../static/js/sheet_playback_core.js');
await import('../../static/js/map_region_sheet.js');

const sheet = /** @type {HTMLElement} */ (document.getElementById('region-sheet'));

/** The sheet, closed and deselected, as each test expects to find it. */
async function boot() {
  return sheet;
}

/**
 * Announce a selection the way map.js does.
 *
 * @param {?string} regionId
 * @param {string} [via]
 * @param {object} [extra] More of the event's detail (region_name, …).
 */
function select(regionId, via, extra = {}) {
  document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
    detail: { region_id: regionId, via, ...extra },
  }));
}

// The module keeps fetched bodies for ten minutes. Each test starts an hour
// after the last, so none inherits another's kept bodies.
let now = Date.UTC(2026, 9, 19);

/** Let the fetch chain settle. */
async function settle() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

let fetchMock;

beforeEach(() => {
  window.snowdeskMapState = { isPlaying: false };
  document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
    detail: { region_id: null },
  }));
  document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
    detail: { date: null },
  }));
  const pin = document.getElementById('map-region-pin-control');
  pin.setAttribute('aria-pressed', 'false');
  pin.setAttribute('aria-label', 'Pin this region');
  const download = document.getElementById('map-download-control');
  download.removeAttribute('aria-disabled');
  download.removeAttribute('data-download-state');
  download.removeAttribute('style');
  download.className = '';
  download.innerHTML = '';
  fetchMock = vi.fn(() =>
    Promise.resolve({ ok: true, text: () => Promise.resolve(BODY) }),
  );
  vi.stubGlobal('fetch', fetchMock);
  now += 60 * 60 * 1000;
  vi.spyOn(Date, 'now').mockImplementation(() => now);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete window.pwaRegionLevel;
});

describe('the peek painted from the page', () => {
  it('shows the region and its level before the body arrives', async () => {
    window.pwaRegionLevel = { forRegion: () => 'considerable' };
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const sheet = await boot();
    select('CH-4115', 'tap', { region_name: 'Martigny-Verbier', subregion_name: 'Lower Valais' });
    const slot = (name) => sheet.querySelector(`[data-region-sheet-slot="${name}"]`);
    expect(slot('name').textContent).toBe('Martigny-Verbier');
    expect(slot('rating').textContent).toBe('3');
    expect(slot('rating').getAttribute('data-level')).toBe('considerable');
  });

  it("keeps the tile's dash when the map has no level for the day", async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const sheet = await boot();
    select('CH-4115', 'tap', { region_name: 'Martigny-Verbier' });
    const tile = sheet.querySelector('[data-region-sheet-slot="rating"]');
    expect(tile.getAttribute('data-level')).toBe('no_rating');
    expect(tile.textContent).toBe('–');
  });
});

describe('a body fetched once', () => {
  it('is reused for the same region and day, with no second request', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    select(null);
    select('CH-4115', 'tap');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sheet.querySelector('[data-testid="region-sheet-bulletin"]')).not.toBeNull();
  });

  it('is asked for again once ten minutes have passed', async () => {
    await boot();
    select('CH-4115', 'tap');
    await settle();
    select(null);
    now += 11 * 60 * 1000;
    select('CH-4115', 'tap');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('is kept per day', async () => {
    await boot();
    select('CH-4115', 'tap');
    await settle();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: '2026-02-03' },
    }));
    await settle();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: null },
    }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('opening', () => {
  it('opens at peek on a tap and fetches the region', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(sheet.getAttribute('data-detent')).toBe('peek');
    expect(fetchMock).toHaveBeenCalledWith(
      '/partials/region-sheet/ch-4115/',
      expect.objectContaining({ headers: { 'HX-Request': 'true' } }),
    );
    await settle();
    expect(sheet.textContent).toContain('Martigny-Verbier');
  });

  it('opens for a selection that was not a tap (search, pinned row, deep link)', async () => {
    const sheet = await boot();
    select('CH-4115');
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(fetchMock).toHaveBeenCalled();
  });

  it('opens on a desktop too, as a resort or weather tap does (SNOW-1173)', async () => {
    const sheet = await boot();
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    select('CH-4115', 'tap');
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(fetchMock).toHaveBeenCalled();
  });

  it('says so when the region cannot be fetched, under the peek it keeps', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    const sheet = await boot();
    select('CH-4115', 'tap', { region_name: 'Martigny-Verbier' });
    await settle();
    expect(sheet.textContent).toContain('Region details are unavailable offline.');
    expect(sheet.textContent).toContain('Martigny-Verbier');
  });
});

describe('reopening (2026-10-19)', () => {
  it('reopens for the region still selected once its sheet was closed', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    // The × closes it the way overlays.js's shared handler does: by
    // hiding it. The region stays selected.
    sheet.setAttribute('hidden', '');
    expect(window.pwaRegionSheet.reopen()).toBe(true);
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(sheet.getAttribute('data-detent')).toBe('peek');
    expect(sheet.textContent).toContain('Martigny-Verbier');
  });

  it('does nothing while the sheet is open or with no selection', async () => {
    const sheet = await boot();
    expect(window.pwaRegionSheet.reopen()).toBe(false);
    select('CH-4115', 'tap');
    expect(window.pwaRegionSheet.reopen()).toBe(false);
    expect(sheet.hasAttribute('hidden')).toBe(false);
  });

  it('reopens on a desktop too (SNOW-1173)', async () => {
    const sheet = await boot();
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    select('CH-4115', 'tap');
    sheet.setAttribute('hidden', '');
    expect(window.pwaRegionSheet.reopen()).toBe(true);
    expect(sheet.hasAttribute('hidden')).toBe(false);
  });
});

describe('while open', () => {
  it('follows another selection, tap or not, at the detent it rests at', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    sheet.querySelector('[data-sheet-handle]').click();
    expect(sheet.getAttribute('data-detent')).toBe('half');
    select('CH-4116');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/partials/region-sheet/ch-4116/',
      expect.anything(),
    );
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('refetches for the day the map moves to', async () => {
    await boot();
    select('CH-4115', 'tap');
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: '2026-02-03' },
    }));
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/partials/region-sheet/ch-4115/?d=2026-02-03',
      expect.anything(),
    );
  });

  it('closes when the region is deselected', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    select(null);
    expect(sheet.hasAttribute('hidden')).toBe(true);
  });

  it('marks the root while open, so the ribbon header steps out', async () => {
    await boot();
    select('CH-4115', 'tap');
    await settle();
    expect(document.documentElement.hasAttribute('data-region-sheet')).toBe(true);
    select(null);
    await settle();
    expect(document.documentElement.hasAttribute('data-region-sheet')).toBe(false);
  });

  it('drops an answer for a region the reader has moved on from', async () => {
    let resolveFirst;
    fetchMock
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementationOnce(() =>
        Promise.resolve({ ok: true, text: () => Promise.resolve('<p>second</p>') }));
    const sheet = await boot();
    select('CH-4115', 'tap');
    select('CH-4116');
    await settle();
    resolveFirst({ ok: true, text: () => Promise.resolve('<p>first</p>') });
    await settle();
    expect(sheet.textContent).toContain('second');
    expect(sheet.textContent).not.toContain('first');
  });

});

describe('from one region to the next (Hugo, 27 Oct 2026)', () => {
  // The body's real shape: the peek partial's root inside the body root.
  const bodyFor = (name, extra) => `
    <div data-testid="region-sheet-body" data-panel-key="region:${name}">
      <div><div data-sheet-peek><h2 data-region-sheet-slot="name">${name}</h2></div></div>
      <div data-testid="region-sheet-bulletin">${extra}</div>
    </div>
  `;

  /** Open Martigny-Verbier, then select Val d'Hérens with its body held. */
  async function hopWithBodyHeld() {
    let release;
    fetchMock
      .mockImplementationOnce(() =>
        Promise.resolve({ ok: true, text: () => Promise.resolve(bodyFor('Martigny-Verbier', 'Old problems')) }))
      .mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const sheet = await boot();
    select('CH-4115', 'tap', { region_name: 'Martigny-Verbier' });
    await settle();
    sheet.querySelector('[data-sheet-handle]').click();
    select('CH-4116', 'tap', { region_name: "Val d'Hérens" });
    return { sheet, release: (r) => release(r) };
  }

  it('repaints the peek row in place and keeps the rest up, dimmed', async () => {
    const { sheet } = await hopWithBodyHeld();
    const names = [...sheet.querySelectorAll('[data-region-sheet-slot="name"]')].map((n) => n.textContent);
    expect(names).toEqual(["Val d'Hérens"]);
    // The old body stays rather than the sheet dropping to its peek row.
    expect(sheet.querySelector('[data-testid="region-sheet-bulletin"]').textContent).toContain('Old problems');
    expect(sheet.querySelector('[data-testid="region-sheet-bulletin"]').hasAttribute('data-sheet-stale')).toBe(true);
    expect(sheet.querySelector('[data-sheet-peek]').closest('[data-sheet-stale]')).toBeNull();
    expect(sheet.getAttribute('aria-busy')).toBe('true');
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('swaps in the new body when it lands', async () => {
    const { sheet, release } = await hopWithBodyHeld();
    release({ ok: true, text: () => Promise.resolve(bodyFor("Val d'Hérens", 'New problems')) });
    await settle();
    expect(sheet.textContent).toContain('New problems');
    expect(sheet.textContent).not.toContain('Old problems');
    expect(sheet.hasAttribute('aria-busy')).toBe(false);
    expect(sheet.querySelector('[data-sheet-stale]')).toBeNull();
  });

  it('takes the old body down when the new one cannot be fetched', async () => {
    const { sheet, release } = await hopWithBodyHeld();
    release({ ok: false });
    await settle();
    expect(sheet.textContent).not.toContain('Old problems');
    expect(sheet.querySelector('[data-region-sheet-slot="name"]').textContent).toBe("Val d'Hérens");
    expect(sheet.textContent).toContain('unavailable offline');
  });
});

describe('the problem-terrain switch', () => {
  const TERRAIN_BODY = `${BODY}
    <div data-testid="region-sheet-terrain">
      <input type="checkbox" id="region-sheet-terrain-toggle">
      <div data-region-sheet-terrain-key hidden></div>
      <span data-panel-overlay-toggle-detail hidden></span>
    </div>`;

  /** A bridge whose state the test drives, as map.js's would. */
  function bridge(on, where = 'ok') {
    const state = { on, where };
    window.pwaBulletinTerrain = {
      isEnabled: () => state.on,
      availability: () => state.where,
      setEnabled: vi.fn((next) => { state.on = next; }),
    };
    return state;
  }

  afterEach(() => {
    delete window.pwaBulletinTerrain;
  });

  it('starts at the layer\'s state, key shown and zoom line when the map is too far out', async () => {
    bridge(true, 'zoom-in');
    fetchMock.mockImplementation(() => Promise.resolve({ ok: true, text: () => Promise.resolve(TERRAIN_BODY) }));
    select('CH-4115', 'tap');
    await settle();
    const input = /** @type {HTMLInputElement} */ (sheet.querySelector('#region-sheet-terrain-toggle'));
    expect(input.checked).toBe(true);
    expect(sheet.querySelector('[data-region-sheet-terrain-key]').hidden).toBe(false);
    expect(sheet.querySelector('[data-panel-overlay-toggle-detail]').hidden).toBe(false);
  });

  it('turns the layer on with the region, and follows it back off', async () => {
    const state = bridge(false);
    fetchMock.mockImplementation(() => Promise.resolve({ ok: true, text: () => Promise.resolve(TERRAIN_BODY) }));
    select('CH-4115', 'tap');
    await settle();
    const input = /** @type {HTMLInputElement} */ (sheet.querySelector('#region-sheet-terrain-toggle'));
    expect(input.checked).toBe(false);
    input.checked = true;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    expect(window.pwaBulletinTerrain.setEnabled).toHaveBeenCalledWith(true, 'CH-4115');
    // The layers menu's row switches it off: the switch here follows.
    state.on = false;
    document.dispatchEvent(new CustomEvent('snowdesk:problem-terrain-changed', {
      detail: { enabled: false },
    }));
    expect(input.checked).toBe(false);
    expect(sheet.querySelector('[data-region-sheet-terrain-key]').hidden).toBe(true);
  });

  it('moves the zoom line with the camera, which the terrain filter announces', async () => {
    const state = bridge(true, 'ok');
    fetchMock.mockImplementation(() => Promise.resolve({ ok: true, text: () => Promise.resolve(TERRAIN_BODY) }));
    select('CH-4115', 'tap');
    await settle();
    const zoom = sheet.querySelector('[data-panel-overlay-toggle-detail]');
    expect(zoom.hidden).toBe(true);
    state.where = 'zoom-in';
    document.dispatchEvent(new CustomEvent('snowdesk:terrain-filter-changed', {
      detail: { enabled: false, availability: 'zoom-in' },
    }));
    expect(zoom.hidden).toBe(false);
  });
});

describe('the proxies', () => {
  it('mirror the roundel they stand for', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const roundel = document.getElementById('map-region-pin-control');
    roundel.setAttribute('aria-pressed', 'true');
    roundel.setAttribute('aria-label', 'Unpin this region');
    await settle();
    const proxy = sheet.querySelector('[data-region-sheet-proxy="map-region-pin-control"]');
    expect(proxy.getAttribute('aria-pressed')).toBe('true');
    expect(proxy.getAttribute('aria-label')).toBe('Unpin this region');
  });

  it('take the roundel\'s look: its classes, state, fill and glyphs', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const roundel = document.getElementById('map-download-control');
    roundel.className = 'map-glass-roundel map-download-control';
    roundel.setAttribute('data-download-state', 'busy');
    roundel.style.setProperty('--download-progress', '40%');
    roundel.innerHTML = '<svg class="map-download-control-icon--busy"></svg>';
    await settle();
    const proxy = sheet.querySelector('[data-region-sheet-proxy="map-download-control"]');
    expect(proxy.className).toBe('map-glass-roundel map-download-control');
    expect(proxy.getAttribute('data-download-state')).toBe('busy');
    expect(proxy.style.getPropertyValue('--download-progress')).toBe('40%');
    expect(proxy.querySelector('.map-download-control-icon--busy')).not.toBeNull();
    expect(proxy.getAttribute('data-region-sheet-proxy')).toBe('map-download-control');
  });

  it('press the roundel, and not when it is disabled', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const roundel = document.getElementById('map-download-control');
    const pressed = vi.fn();
    roundel.addEventListener('click', pressed);
    const proxy = sheet.querySelector('[data-region-sheet-proxy="map-download-control"]');
    proxy.click();
    expect(pressed).toHaveBeenCalledTimes(1);

    roundel.setAttribute('aria-disabled', 'true');
    await settle();
    proxy.click();
    expect(pressed).toHaveBeenCalledTimes(1);
  });

  // SNOW-1130: on a phone the proxy is the only download button, and a tap
  // on a downloaded ('done') or half-refreshed ('partial') region refreshes
  // its bulletins and weather. The roundel announces both as actionable, so
  // the proxy must pass the press through.
  it.each(['done', 'partial'])('presses the roundel in the %s state', async (state) => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const roundel = document.getElementById('map-download-control');
    const pressed = vi.fn();
    roundel.addEventListener('click', pressed);
    roundel.setAttribute('data-download-state', state);
    roundel.setAttribute('aria-disabled', 'false');
    await settle();
    const proxy = sheet.querySelector('[data-region-sheet-proxy="map-download-control"]');
    expect(proxy.getAttribute('aria-disabled')).toBe('false');
    proxy.click();
    expect(pressed).toHaveBeenCalledTimes(1);
  });
});

describe('a resort named in the sheet (SNOW-1161)', () => {
  it('asks the map for the resort instead of navigating', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const asked = vi.fn();
    document.addEventListener('snowdesk:resort-open', asked);
    const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
    sheet.querySelector('a[data-resort-slug]').dispatchEvent(click);
    document.removeEventListener('snowdesk:resort-open', asked);
    expect(click.defaultPrevented).toBe(true);
    expect(asked).toHaveBeenCalledTimes(1);
    expect(asked.mock.calls[0][0].detail).toEqual({
      slug: 'verbier',
      href: new URL('/resorts/verbier/', document.baseURI).href,
    });
  });

  it('leaves a modified click to the link, for a new tab', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const asked = vi.fn();
    document.addEventListener('snowdesk:resort-open', asked);
    // Read whether the sheet let the click through, then stop jsdom
    // following the link.
    let leftToTheLink = false;
    const record = (ev) => {
      leftToTheLink = !ev.defaultPrevented;
      ev.preventDefault();
    };
    document.addEventListener('click', record);
    const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, metaKey: true });
    sheet.querySelector('a[data-resort-slug]').dispatchEvent(click);
    document.removeEventListener('click', record);
    document.removeEventListener('snowdesk:resort-open', asked);
    expect(leftToTheLink).toBe(true);
    expect(asked).not.toHaveBeenCalled();
  });
});

// SNOW-1239: the timeline plays five frames a second; the sheet must not
// fetch for each.
describe('during playback', () => {
  /** @param {boolean} playing */
  const announceState = (playing) => {
    window.snowdeskMapState.isPlaying = playing;
    document.dispatchEvent(new CustomEvent('snowdesk:timelapse-state', { detail: { playing } }));
  };
  const frame = (date) =>
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date, source: 'timelapse' },
    }));
  const band = () => sheet.querySelector('[data-sheet-playback-band]');
  /** A fetch that never answers, and rejects as an abort when aborted. */
  const hang = () =>
    fetchMock.mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    }));

  it('fetches nothing for a hundred frames, then once for the last day', async () => {
    select('CH-4115', 'tap');
    await settle();
    fetchMock.mockClear();
    announceState(true);
    for (let i = 0; i < 100; i += 1) frame(`2026-02-${String((i % 28) + 1).padStart(2, '0')}`);
    frame('2026-03-15');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(band()?.textContent).toContain('Playback in progress');
    expect(sheet.hasAttribute('data-sheet-paused')).toBe(true);
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/region-sheet/ch-4115/?d=2026-03-15');
    expect(band()).toBeNull();
  });

  it('reloads for a day picked by hand, and the stop that follows adds none', async () => {
    select('CH-4115', 'tap');
    await settle();
    announceState(true);
    frame('2026-01-02');
    fetchMock.mockClear();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: '2026-01-20', source: 'scrubber' },
    }));
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/region-sheet/ch-4115/?d=2026-01-20');
  });

  it('opens on the peek during playback, with no request, and loads when it stops', async () => {
    announceState(true);
    select('CH-4115', 'tap', { region_name: 'Martigny-Verbier' });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(sheet.querySelector('[data-region-sheet-slot="name"]').textContent).toBe('Martigny-Verbier');
    expect(band()).not.toBeNull();
    // The peek row stays the peek.
    expect(band().hasAttribute('data-sheet-peek')).toBe(false);
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await settle();
    expect(sheet.querySelector('[data-testid="region-sheet-bulletin"]')).not.toBeNull();
  });

  it('a report tapped during playback waits for it to stop (SNOW-1295)', async () => {
    select('CH-4115', 'tap');
    await settle();
    fetchMock.mockClear();
    announceState(true);
    document.dispatchEvent(new CustomEvent('snowdesk:observation-focus', {
      detail: { submission: 'not-in-the-body', region_id: 'CH-4115' },
    }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(band()).not.toBeNull();
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(band()).toBeNull();
  });

  it('paints a kept body under the band for a region picked during playback', async () => {
    select('CH-4115', 'tap');
    await settle();
    select('CH-4116', 'tap');
    await settle();
    fetchMock.mockClear();
    announceState(true);
    select('CH-4115', 'search');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sheet.querySelector('[data-testid="region-sheet-bulletin"]')).not.toBeNull();
    expect(band()).not.toBeNull();
    announceState(false);
    // The stop answers from the kept body too.
    expect(fetchMock).not.toHaveBeenCalled();
    expect(band()).toBeNull();
  });

  it('aborts the request a newer selection supersedes, and on close', () => {
    hang();
    select('CH-4115', 'tap');
    select('CH-4116', 'tap');
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    select(null);
    expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(true);
  });

  it('aborts on Escape, and keeps no body from it', async () => {
    let resolveLate;
    fetchMock.mockImplementation((_url, init) => new Promise((resolve, reject) => {
      resolveLate = resolve;
      init.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    }));
    select('CH-4115', 'tap');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    resolveLate({ ok: true, text: () => Promise.resolve(BODY) });
    await settle();
    fetchMock.mockClear();
    fetchMock.mockImplementation(() => new Promise(() => {}));
    window.pwaRegionSheet.reopen();
    // Nothing was kept, so the reopen asks again.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('shows no failure line for an aborted request', async () => {
    hang();
    select('CH-4115', 'tap');
    announceState(true);
    await settle();
    expect(sheet.textContent).not.toContain('unavailable offline');
    expect(band()).not.toBeNull();
  });
});

describe('a tap on a report\'s pin (SNOW-1291)', () => {
  const SUBMISSION = '0b6f2c1e-0000-4000-8000-000000000001';
  const GROUND_BODY = `
    <div data-sheet-peek><h2>Martigny-Verbier</h2></div>
    <div data-on-the-ground>
      <details data-testid="on-the-ground-day">
        <summary>Observed on 28 Oct 2026 · 2</summary>
        <ul>
          <li data-observation-submission="0b6f2c1e-0000-4000-8000-000000000002">Powder</li>
          <li data-observation-submission="${SUBMISSION}">Whumpfing</li>
        </ul>
      </details>
    </div>
  `;

  /** Ask for a report's row, the way map.js does. */
  function focus(regionId, submission = SUBMISSION) {
    document.dispatchEvent(new CustomEvent('snowdesk:observation-focus', {
      detail: { submission, region_id: regionId },
    }));
  }

  const row = () => sheet.querySelector(`[data-observation-submission="${SUBMISSION}"]`);

  beforeEach(() => {
    fetchMock.mockImplementation(() => Promise.resolve({ ok: true, text: () => Promise.resolve(GROUND_BODY) }));
    Element.prototype.scrollIntoView = vi.fn();
  });

  it('opens the panel at the report\'s row once the body paints', async () => {
    focus('CH-4115');
    select('CH-4115', 'observation');
    // The peek paints first: the request waits for the body.
    expect(row()).toBeNull();
    await settle();
    expect(row().hasAttribute('data-focused')).toBe(true);
    expect(row().closest('details').open).toBe(true);
    // Scrolled once the phone sheet's rise to half has settled.
    await vi.waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1));
  });

  it('applies once: the next paint of the region does not jump back', async () => {
    focus('CH-4115');
    select('CH-4115', 'observation');
    await settle();
    await vi.waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
    Element.prototype.scrollIntoView.mockClear();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-10-28' } }));
    await settle();
    await new Promise((resolve) => { setTimeout(resolve, 400); });
    expect(row().hasAttribute('data-focused')).toBe(false);
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it('is dropped when another region is selected first', async () => {
    focus('CH-4115');
    select('CH-4116', 'tap');
    await settle();
    select('CH-4115', 'tap');
    await settle();
    expect(row().hasAttribute('data-focused')).toBe(false);
  });

  it('waits for a closed sheet to open, then raises it to half at the row', async () => {
    // A phone sheet closed over the selected region: its kept body paints
    // before the open, and a closed sheet ignores a detent. The request is
    // applied once the sheet is open, to the body the open painted.
    select('CH-4115', 'tap');
    await settle();
    sheet.setAttribute('hidden', '');
    const stale = row();
    focus('CH-4115');
    expect(stale.hasAttribute('data-focused')).toBe(false);
    window.pwaRegionSheet.reopen();
    // SNOW-1295: a tap reads the region afresh, so the row lands with the body.
    await settle();
    expect(row().hasAttribute('data-focused')).toBe(true);
    expect(row().closest('details').open).toBe(true);
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('reads an open sheet again when its body lacks the tapped report (SNOW-1295)', async () => {
    // The open body predates the report: the tap must not leave it as it is.
    fetchMock.mockImplementation(() => Promise.resolve({ ok: true, text: () => Promise.resolve(BODY) }));
    select('CH-4115', 'tap');
    await settle();
    expect(row()).toBeNull();
    fetchMock.mockClear();
    fetchMock.mockImplementation(() => Promise.resolve({ ok: true, text: () => Promise.resolve(GROUND_BODY) }));
    focus('CH-4115');
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(row().hasAttribute('data-focused')).toBe(true);
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('raises the sheet to the section when a fresh body still lacks the row (SNOW-1295)', async () => {
    const other = GROUND_BODY.replace(SUBMISSION, '0b6f2c1e-0000-4000-8000-00000000000f');
    fetchMock.mockImplementation(() => Promise.resolve({ ok: true, text: () => Promise.resolve(other) }));
    focus('CH-4115');
    select('CH-4115', 'observation');
    await settle();
    expect(sheet.getAttribute('data-detent')).toBe('half');
    // Scrolled to the section, not to a row.
    await vi.waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith(
      expect.objectContaining({ block: 'start' }),
    ));
  });

  it('marks the row at once when the region\'s body is already up', async () => {
    select('CH-4115', 'tap');
    await settle();
    focus('CH-4115');
    expect(row().hasAttribute('data-focused')).toBe(true);
  });
});


// Last in the file: a report change stamps every later body URL with `r=`.
describe('a report filed or removed (SNOW-1295)', () => {
  afterEach(() => {
    localStorage.removeItem('snowdesk.reports.epoch');
  });

  it('drops kept bodies and asks again past the HTTP cache', async () => {
    select('CH-4116', 'tap');
    await settle();
    select('CH-4115', 'tap');
    await settle();
    fetchMock.mockClear();
    document.dispatchEvent(new CustomEvent('snowdesk:reports-changed'));
    await settle();
    // The open sheet reads its region again, with a URL the cache has not seen.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toMatch(/^\/partials\/region-sheet\/ch-4115\/\?r=\d+$/);
    expect(localStorage.getItem('snowdesk.reports.epoch')).toMatch(/^\d+$/);
    // And a region visited before the change is not served from memory.
    fetchMock.mockClear();
    select('CH-4116', 'tap');
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
