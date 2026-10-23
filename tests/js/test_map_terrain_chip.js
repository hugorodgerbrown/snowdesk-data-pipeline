/*
 * tests/js/test_map_terrain_chip.js — the problem-terrain layer names
 * itself on the map while it paints (SNOW-1196).
 *
 * static/js/map_terrain_chip.js against a reduced copy of the chip and the
 * legend toggle, with ``window.pwaBulletinTerrain`` (map.js) stood in for
 * by a stub.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the chip shows exactly while the layer paints, and names the day the
 *     map is on — including a day only announced, as a timelapse does;
 *   - Key opens the legend card, and does not close it when already open;
 *   - × switches the layer off through the bridge, not by hiding itself.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const MARKUP = `
  <div id="season-scrubber" data-today="2026-10-23"></div>
  <div id="map-legend" data-state="collapsed">
    <button id="map-legend-toggle" type="button">i</button>
  </div>
  <div id="map-terrain-chip" hidden>
    <span data-terrain-chip-label></span>
    <button type="button" data-terrain-chip-key>Key</button>
    <button type="button" data-terrain-chip-off>×</button>
  </div>`;

let enabled;
let added = [];

const $ = (id) => document.getElementById(id);
const label = () => document.querySelector('[data-terrain-chip-label]').textContent;

/** Load a fresh copy of the wiring. */
async function load() {
  vi.resetModules();
  await import('../../static/js/map_terrain_chip.js');
}

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  enabled = true;
  window.pwaBulletinTerrain = {
    isEnabled: () => enabled,
    setEnabled: vi.fn((on) => {
      enabled = on;
      document.dispatchEvent(new CustomEvent('snowdesk:terrain-filter-changed'));
    }),
  };
  globalThis.readUrlDateParam = () => '2026-02-12';
  globalThis.formatDatePopup = (key) => `day ${key}`;
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    if (type.startsWith('snowdesk:')) added.push([type, fn]);
    return original(type, fn, opts);
  });
});

afterEach(() => {
  for (const [type, fn] of added) document.removeEventListener(type, fn);
  added = [];
  delete window.pwaBulletinTerrain;
  vi.restoreAllMocks();
});

describe('the problem-terrain chip', () => {
  it('shows while the layer paints, naming the day', async () => {
    await load();
    expect($('map-terrain-chip').hidden).toBe(false);
    expect(label()).toBe('Problem terrain · day 2026-02-12');
  });

  it('follows an announced day', async () => {
    await load();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-02-14' } }));
    expect(label()).toBe('Problem terrain · day 2026-02-14');
  });

  it('stays hidden with the layer off, and appears when it comes on', async () => {
    enabled = false;
    await load();
    expect($('map-terrain-chip').hidden).toBe(true);
    enabled = true;
    document.dispatchEvent(new CustomEvent('snowdesk:terrain-filter-changed'));
    expect($('map-terrain-chip').hidden).toBe(false);
  });

  it('switches the layer off on ×', async () => {
    await load();
    document.querySelector('[data-terrain-chip-off]').click();
    expect(window.pwaBulletinTerrain.setEnabled).toHaveBeenCalledWith(false);
    expect($('map-terrain-chip').hidden).toBe(true);
  });

  it('opens the legend on Key, and leaves an open one open', async () => {
    const toggle = $('map-legend-toggle');
    toggle.addEventListener('click', () => {
      const root = $('map-legend');
      root.dataset.state = root.dataset.state === 'expanded' ? 'collapsed' : 'expanded';
    });
    await load();
    const key = document.querySelector('[data-terrain-chip-key]');
    key.click();
    expect($('map-legend').dataset.state).toBe('expanded');
    key.click();
    expect($('map-legend').dataset.state).toBe('expanded');
  });
});
