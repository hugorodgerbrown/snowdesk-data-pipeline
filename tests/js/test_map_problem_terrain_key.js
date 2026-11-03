/*
 * tests/js/test_map_problem_terrain_key.js — the problem-terrain layer's
 * key follows the layer (SNOW-1196, SNOW-1206).
 *
 * static/js/map_problem_terrain_key.js against a reduced copy of the
 * legend section, with ``window.pwaBulletinTerrain``
 * (map.js) stood in for by a stub.
 *
 * What is pinned: the legend's problem-terrain key shows exactly while the
 * layer is on, and follows a day change. The menu row's second line it once
 * wrote is gone (SNOW-1410).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MARKUP = `
  <section data-problem-terrain-legend hidden>Problem terrain</section>
`;

let enabled;
let added = [];

const legend = () => document.querySelector('[data-problem-terrain-legend]');

/** Load a fresh copy of the wiring. */
async function load() {
  vi.resetModules();
  await import('../../static/js/map_problem_terrain_key.js');
}

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  enabled = true;
  window.pwaBulletinTerrain = {
    isEnabled: () => enabled,
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

  it('follows a day change', async () => {
    await load();
    enabled = false;
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-02-14' } }));
    expect(legend().hidden).toBe(true);
  });
});
