/*
 * tests/js/test_map_problem_terrain_key.js — the problem-terrain layer's
 * key and layers-menu row follow the layer (SNOW-1196, SNOW-1206).
 *
 * static/js/map_problem_terrain_key.js against a reduced copy of the
 * legend section and the menu row, with ``window.pwaBulletinTerrain``
 * (map.js) stood in for by a stub.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the legend's problem-terrain key shows exactly while the layer is on;
 *   - the layers-menu row's second line says when the day has no bulletin
 *     to paint (SNOW-1195), and follows a day change;
 *   - with no strings template its English fallback is SNOW-1377's
 *     "From zoom 12, Switzerland".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const MARKUP = `
  <section data-problem-terrain-legend hidden>Problem terrain</section>
  <span data-problem-terrain-detail>From zoom 12, Switzerland</span>
  <template id="map-problem-terrain-strings-template">
    <span data-string="detail">Ab Zoom 12, Schweiz</span>
    <span data-string="no-bulletin">Kein Bulletin für diesen Tag</span>
  </template>`;

let enabled;
let dayEmpty;
let added = [];

const legend = () => document.querySelector('[data-problem-terrain-legend]');
const detail = () => document.querySelector('[data-problem-terrain-detail]').textContent;

/** Load a fresh copy of the wiring. */
async function load() {
  vi.resetModules();
  await import('../../static/js/map_problem_terrain_key.js');
}

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  enabled = true;
  dayEmpty = false;
  window.pwaBulletinTerrain = {
    isEnabled: () => enabled,
    dayEmpty: () => dayEmpty,
    setEnabled: vi.fn((on) => {
      enabled = on;
      document.dispatchEvent(new CustomEvent('snowdesk:problem-terrain-changed'));
    }),
  };
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

describe('the problem-terrain key', () => {
  it('shows exactly while the layer is on', async () => {
    await load();
    expect(legend().hidden).toBe(false);
    window.pwaBulletinTerrain.setEnabled(false);
    expect(legend().hidden).toBe(true);
    window.pwaBulletinTerrain.setEnabled(true);
    expect(legend().hidden).toBe(false);
  });

  it('stays hidden when the layer starts off', async () => {
    enabled = false;
    await load();
    expect(legend().hidden).toBe(true);
  });
});

describe("the layers-menu row's second line", () => {
  it('says when the day has no bulletin to paint', async () => {
    await load();
    expect(detail()).toBe('Ab Zoom 12, Schweiz');
    dayEmpty = true;
    document.dispatchEvent(new CustomEvent('snowdesk:problem-terrain-changed'));
    expect(detail()).toBe('Kein Bulletin für diesen Tag');
  });

  it('follows a day change', async () => {
    dayEmpty = true;
    await load();
    dayEmpty = false;
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-02-14' } }));
    expect(detail()).toBe('Ab Zoom 12, Schweiz');
  });
});

describe('the English fallback (SNOW-1377)', () => {
  it('reads "From zoom 12, Switzerland" with no strings template', async () => {
    document.getElementById('map-problem-terrain-strings-template').remove();
    document.querySelector('[data-problem-terrain-detail]').textContent = '';
    await load();
    expect(detail()).toBe('From zoom 12, Switzerland');
  });
});
