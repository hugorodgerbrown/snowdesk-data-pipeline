/*
 * tests/js/test_rest_zones.js — the wildlife rest zones layer
 * (static/js/rest_zones.js, SNOW-1384).
 *
 * rest_zones.js is booted alone against stubs: the map, MapSheet's attach
 * and a fetch. What it must do: load nothing while switched off or zoomed
 * out; load the view's box for the map's day once switched on, and again
 * only when the view leaves the box or the day changes; keep the last
 * zones drawn when a load fails; persist the switch and announce it; and
 * on a tap inside a zone, fill the sheet and open it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const GEOJSON_URL = '/api/rest-zones.geojson';
const KEY = 'snowdesk.map.overlay.rest_zones';
// As MapLibre hands a hit back: no top-level id, the uuid in the properties.
const ZONE = {
  properties: {
    uuid: 'uuid-1',
    name: 'Silvretta',
    binding: true,
    restriction: 'Betretungsverbot',
    period: '21.12. - 30.04.',
    in_force: true,
    canton: 'GR',
    url: 'https://www.gr.ch/wildruhe',
  },
};
const FC = { type: 'FeatureCollection', features: [ZONE] };

/** A MapLibre stub that keeps its zoom and centre. */
function stubMap() {
  const handlers = {};
  const layers = new Map();
  const sources = new Map();
  const view = { zoom: 12, lng: 10.1, lat: 46.85 };
  const map = {
    view,
    layers,
    sources,
    hits: [],
    on: (ev, cb) => {
      (handlers[ev] ||= []).push(cb);
    },
    fire: (ev) => (handlers[ev] || []).forEach((cb) => cb()),
    isStyleLoaded: () => true,
    getSource: (id) => sources.get(id) || null,
    addSource: (id, def) => sources.set(id, { def, setData: vi.fn() }),
    getLayer: (id) => layers.get(id) || null,
    addLayer: (def) => layers.set(def.id, { ...def, layout: { ...def.layout } }),
    setLayoutProperty: (id, prop, value) => {
      layers.get(id).layout[prop] = value;
    },
    getZoom: () => view.zoom,
    getBounds: () => ({
      getWest: () => view.lng - 0.05,
      getSouth: () => view.lat - 0.05,
      getEast: () => view.lng + 0.05,
      getNorth: () => view.lat + 0.05,
    }),
    queryRenderedFeatures: vi.fn(() => map.hits),
  };
  return map;
}

let fetches;
let answer;
let sheet;
let announced = 0;
/** Every document listener a boot adds, removed after the test. */
let listeners = [];

// Counted once for the file: a listener per boot would pile up.
document.addEventListener('snowdesk:overlay-visibility-changed', () => {
  announced += 1;
});

const lands = () => Promise.resolve({ ok: true, json: () => Promise.resolve(FC) });
const notOk = () => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) });

/**
 * Boot rest_zones.js.
 *
 * @param {{on?: boolean, search?: string, zoom?: number}} [options]
 */
async function boot(options = {}) {
  localStorage.clear();
  if (options.on) localStorage.setItem(KEY, 'true');
  history.replaceState(null, '', '/map/' + (options.search || ''));
  document.body.innerHTML = `
    <div id="season-scrubber" data-today="2026-11-02"></div>
    <div id="rest-zones-config" data-geojson-url="${GEOJSON_URL}"></div>
    <div id="rest-zone-sheet" hidden></div>
    <template id="rest-zone-body-template"><div>
      <h2 data-rest-zone-name></h2>
      <p><span data-rest-zone-kind></span><span data-rest-zone-row>· <span data-rest-zone-canton></span></span></p>
      <p data-rest-zone-status></p>
      <dd data-rest-zone-period></dd>
      <div data-rest-zone-row><dd data-rest-zone-restriction></dd></div>
      <a data-rest-zone-rules href="#">Rules</a>
    </div></template>`;
  const sheetEl = document.getElementById('rest-zone-sheet');
  let teardown = () => {};
  sheet = {
    open: vi.fn(() => sheetEl.removeAttribute('hidden')),
    close: vi.fn(() => {
      sheetEl.setAttribute('hidden', '');
      teardown();
    }),
    isOpen: () => !sheetEl.hasAttribute('hidden'),
  };
  window.MapSheet = {
    attach: vi.fn((_el, opts) => {
      teardown = (opts && opts.onTeardown) || teardown;
      return sheet;
    }),
  };
  const map = stubMap();
  if (options.zoom !== undefined) map.view.zoom = options.zoom;
  window.snowdeskMapState = { map, overlayStorageKey: { rest_zones: KEY } };
  announced = 0;
  fetches = [];
  answer = lands;
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      fetches.push(String(url));
      return answer();
    }),
  );
  vi.useFakeTimers();
  vi.resetModules();
  const add = document.addEventListener.bind(document);
  const spy = vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    listeners.push([type, fn, opts]);
    add(type, fn, opts);
  });
  await import('../../static/js/rest_zones_core.js');
  await import('../../static/js/rest_zones.js');
  spy.mockRestore();
  return map;
}

/** Let the debounce fire and the fetch settle. */
async function settle() {
  await vi.advanceTimersByTimeAsync(300);
  await vi.runAllTimersAsync();
}

afterEach(() => {
  // Each boot is a fresh module on the same document: its listeners go
  // with it, or the next test's events reach every earlier boot.
  for (const [type, fn, opts] of listeners) document.removeEventListener(type, fn, opts);
  listeners = [];
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete window.MapSheet;
  delete window.snowdeskMapState;
  delete window.pwaRestZones;
  delete window.pwaRestZonesCore;
  localStorage.clear();
});

describe('switched off', () => {
  it('installs hidden layers and loads nothing', async () => {
    const map = await boot();
    await settle();
    expect(map.layers.get('rest-zones-fill').layout.visibility).toBe('none');
    expect(map.layers.get('rest-zones-line').layout.visibility).toBe('none');
    expect(map.layers.get('rest-zones-fill').minzoom).toBe(10);
    // MapLibre drops a string Feature id: the uuid property stands in.
    expect(map.sources.get('rest-zones').def.promoteId).toBe('uuid');
    expect(fetches).toEqual([]);
    expect(window.pwaRestZones.isEnabled()).toBe(false);
  });

  it('takes no tap', async () => {
    const map = await boot();
    map.hits = [ZONE];
    expect(window.pwaRestZones.tap({ x: 1, y: 2 })).toBe(false);
    expect(sheet.open).not.toHaveBeenCalled();
  });
});

describe('switched on', () => {
  it('loads the view for the map day from ?d=', async () => {
    const map = await boot({ on: true, search: '?d=2027-01-10' });
    await settle();
    expect(fetches).toHaveLength(1);
    expect(fetches[0]).toMatch(/^\/api\/rest-zones\.geojson\?bbox=[\d.,]+&d=2027-01-10$/);
    expect(map.sources.get('rest-zones').setData).toHaveBeenCalledWith(FC);
    expect(map.layers.get('rest-zones-fill').layout.visibility).toBe('visible');
  });

  it('falls back to the page today without ?d=', async () => {
    await boot({ on: true });
    await settle();
    expect(fetches[0]).toMatch(/&d=2026-11-02$/);
  });

  it('loads nothing zoomed out past z10', async () => {
    const map = await boot({ on: true, zoom: 9.5 });
    map.fire('moveend');
    await settle();
    expect(fetches).toEqual([]);
    map.hits = [ZONE];
    expect(window.pwaRestZones.tap({ x: 0, y: 0 })).toBe(false);
  });

  it('reloads only when the view leaves the box or the day changes', async () => {
    const map = await boot({ on: true });
    await settle();
    expect(fetches).toHaveLength(1);

    map.view.lng += 0.01;
    map.fire('moveend');
    await settle();
    expect(fetches).toHaveLength(1);

    map.view.lng += 0.5;
    map.fire('moveend');
    await settle();
    expect(fetches).toHaveLength(2);

    document.dispatchEvent(
      new CustomEvent('snowdesk:date-changed', { detail: { date: '2027-02-01' } }),
    );
    await settle();
    expect(fetches).toHaveLength(3);
    expect(fetches[2]).toMatch(/&d=2027-02-01$/);
  });

  it('keeps the last zones drawn when a load fails', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const map = await boot({ on: true });
    await settle();
    const source = map.sources.get('rest-zones');
    answer = notOk;
    map.view.lng += 0.5;
    map.fire('moveend');
    await settle();
    expect(fetches).toHaveLength(2);
    expect(source.setData).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('opens the sheet for a tap inside a zone', async () => {
    const map = await boot({ on: true });
    await settle();
    map.hits = [ZONE];
    expect(window.pwaRestZones.tap({ x: 5, y: 6 })).toBe(true);
    expect(map.queryRenderedFeatures).toHaveBeenCalledWith(
      [5, 6], { layers: ['rest-zones-fill', 'rest-zones-line'] },
    );
    expect(sheet.open).toHaveBeenCalled();
    const el = document.getElementById('rest-zone-sheet');
    expect(el.querySelector('[data-rest-zone-name]').textContent).toBe('Silvretta');
    expect(el.querySelector('[data-rest-zone-kind]').textContent).toBe('Legally binding');
    expect(el.querySelector('[data-rest-zone-canton]').textContent).toBe('GR');
    expect(el.querySelector('[data-rest-zone-status]').textContent).toBe('Protected on this day');
    expect(el.querySelector('[data-rest-zone-period]').textContent).toBe('21.12. - 30.04.');
    expect(el.querySelector('[data-rest-zone-restriction]').textContent).toBe('Betretungsverbot');
    expect(el.querySelector('[data-rest-zone-rules]').getAttribute('href'))
      .toBe('https://www.gr.ch/wildruhe');
  });

  it('says what a recommended zone out of season with nothing stated is', async () => {
    const map = await boot({ on: true });
    map.hits = [{
      properties: { uuid: 'uuid-2', name: 'Z', binding: false, in_force: false, period: '', restriction: '', canton: '', url: '' },
    }];
    expect(window.pwaRestZones.tap({ x: 0, y: 0 })).toBe(true);
    const el = document.getElementById('rest-zone-sheet');
    expect(el.querySelector('[data-rest-zone-kind]').textContent).toBe('Recommended');
    expect(el.querySelector('[data-rest-zone-status]').textContent).toBe('Not protected on this day');
    expect(el.querySelector('[data-rest-zone-period]').textContent).toBe('Period not stated');
    expect(el.querySelector('[data-rest-zone-canton]')).toBeNull();
    expect(el.querySelector('[data-rest-zone-restriction]')).toBeNull();
    expect(el.querySelector('[data-rest-zone-rules]')).toBeNull();
  });

  it.each(['javascript:alert(1)', 'data:text/html,x', 'www.gr.ch'])(
    'drops a rules link that is not http(s): %s',
    async (url) => {
      const map = await boot({ on: true });
      map.hits = [{ properties: { ...ZONE.properties, uuid: 'uuid-3', url } }];
      expect(window.pwaRestZones.tap({ x: 0, y: 0 })).toBe(true);
      const el = document.getElementById('rest-zone-sheet');
      expect(el.querySelector('[data-rest-zone-rules]')).toBeNull();
    },
  );

  it('takes no tap where there is no zone', async () => {
    const map = await boot({ on: true });
    map.hits = [];
    expect(window.pwaRestZones.tap({ x: 0, y: 0 })).toBe(false);
    expect(sheet.open).not.toHaveBeenCalled();
  });
});

describe('a change of day', () => {
  /** A fetch answer the test settles by hand. */
  function deferred() {
    let resolve;
    const promise = new Promise((r) => {
      resolve = r;
    });
    return { promise, resolve };
  }
  const answering = (body) => ({ ok: true, json: () => Promise.resolve(body) });
  const withForce = (inForce) => ({
    type: 'FeatureCollection',
    features: [{ ...ZONE, properties: { ...ZONE.properties, in_force: inForce } }],
  });
  const dayChange = (date) => document.dispatchEvent(
    new CustomEvent('snowdesk:date-changed', { detail: { date } }),
  );

  it('drops a superseded day once the map is back on the drawn one', async () => {
    const map = await boot({ on: true, search: '?d=2027-01-10' });
    await settle();
    const source = map.sources.get('rest-zones');
    expect(source.setData).toHaveBeenCalledTimes(1);

    const july = deferred();
    answer = () => july.promise;
    dayChange('2027-07-10');
    await vi.advanceTimersByTimeAsync(300);
    expect(fetches).toHaveLength(2);

    // Back to January before July answers: January is what is drawn.
    dayChange('2027-01-10');
    await vi.advanceTimersByTimeAsync(300);
    expect(fetches).toHaveLength(2);

    july.resolve(answering(withForce(false)));
    await vi.runAllTimersAsync();
    expect(source.setData).toHaveBeenCalledTimes(1);
  });

  it('drops an answer for a day the map has left', async () => {
    const map = await boot({ on: true, search: '?d=2027-01-10' });
    await settle();
    const source = map.sources.get('rest-zones');
    const july = deferred();
    answer = () => july.promise;
    dayChange('2027-07-10');
    await vi.advanceTimersByTimeAsync(300);
    // The day moves on before the debounce asks again.
    dayChange('2027-08-10');
    july.resolve(answering(withForce(false)));
    await Promise.resolve();
    await Promise.resolve();
    expect(source.setData).toHaveBeenCalledTimes(1);
  });

  it('refreshes the open sheet for the new day', async () => {
    const map = await boot({ on: true, search: '?d=2027-01-10' });
    await settle();
    map.hits = [ZONE];
    window.pwaRestZones.tap({ x: 0, y: 0 });
    const el = document.getElementById('rest-zone-sheet');
    expect(el.querySelector('[data-rest-zone-status]').textContent).toBe('Protected on this day');

    answer = () => Promise.resolve(answering(withForce(false)));
    dayChange('2027-07-10');
    await settle();
    expect(sheet.isOpen()).toBe(true);
    expect(el.querySelector('[data-rest-zone-status]').textContent)
      .toBe('Not protected on this day');
  });

  it('closes the open sheet when its zone is no longer loaded', async () => {
    const map = await boot({ on: true, search: '?d=2027-01-10' });
    await settle();
    map.hits = [ZONE];
    window.pwaRestZones.tap({ x: 0, y: 0 });
    answer = () => Promise.resolve(answering({ type: 'FeatureCollection', features: [] }));
    dayChange('2027-07-10');
    await settle();
    expect(sheet.isOpen()).toBe(false);
  });

  it('leaves a closed sheet closed', async () => {
    const map = await boot({ on: true, search: '?d=2027-01-10' });
    await settle();
    map.hits = [ZONE];
    window.pwaRestZones.tap({ x: 0, y: 0 });
    sheet.close();
    answer = () => Promise.resolve(answering(withForce(false)));
    dayChange('2027-07-10');
    await settle();
    expect(sheet.isOpen()).toBe(false);
    expect(sheet.open).toHaveBeenCalledTimes(1);
  });
});

describe('the bridge', () => {
  it('persists the switch, shows the layers, loads and announces', async () => {
    const map = await boot();
    await settle();
    window.pwaRestZones.show();
    expect(localStorage.getItem(KEY)).toBe('true');
    expect(window.pwaRestZones.isEnabled()).toBe(true);
    expect(map.layers.get('rest-zones-fill').layout.visibility).toBe('visible');
    expect(announced).toBe(1);
    await settle();
    expect(fetches).toHaveLength(1);

    window.pwaRestZones.hide();
    expect(localStorage.getItem(KEY)).toBe('false');
    expect(map.layers.get('rest-zones-line').layout.visibility).toBe('none');
    expect(sheet.close).toHaveBeenCalled();
    expect(announced).toBe(2);
  });

  it('re-adds the layers after a style swap', async () => {
    const map = await boot({ on: true });
    await settle();
    map.sources.clear();
    map.layers.clear();
    map.fire('styledata');
    expect(map.getLayer('rest-zones-fill')).not.toBeNull();
    expect(map.sources.get('rest-zones').def.data).toEqual(FC);
  });
});

describe('placement modes', () => {
  // Hugo, 3 Nov 2026: the zones are legally binding and still there while
  // a pin is placed or a route drawn, so no placement hides them.
  afterEach(() => {
    delete window.PlacementFocus;
  });

  it('stays drawn and loads while a placement has the map', async () => {
    window.PlacementFocus = { isActive: () => true };
    const map = await boot({ on: true });
    await settle();
    expect(fetches).toHaveLength(1);
    expect(map.layers.get('rest-zones-fill').layout.visibility).toBe('visible');
    expect(map.layers.get('rest-zones-line').layout.visibility).toBe('visible');
  });

  it('takes no tap while a placement has the map', async () => {
    window.PlacementFocus = { isActive: () => true };
    const map = await boot({ on: true });
    await settle();
    map.hits = [ZONE];
    expect(window.pwaRestZones.tap({ x: 1, y: 2 })).toBe(false);
    expect(sheet.open).not.toHaveBeenCalled();
  });

  it('shows while a route is drawn with the switch off, then hides again', async () => {
    const map = await boot();
    await settle();
    expect(fetches).toEqual([]);

    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: true } }));
    await settle();
    expect(fetches).toHaveLength(1);
    expect(map.layers.get('rest-zones-fill').layout.visibility).toBe('visible');
    // The switch itself is untouched.
    expect(window.pwaRestZones.isEnabled()).toBe(false);

    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: false } }));
    expect(map.layers.get('rest-zones-fill').layout.visibility).toBe('none');
    expect(map.layers.get('rest-zones-line').layout.visibility).toBe('none');
  });
});
