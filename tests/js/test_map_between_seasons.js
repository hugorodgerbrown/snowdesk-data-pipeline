/*
 * tests/js/test_map_between_seasons.js — Vitest unit tests for
 * static/js/map_between_seasons.js (SNOW-1244).
 *
 * The note explains today's empty off-season map. These cases pin when it
 * shows (after the map settles and the welcome card has gone, on today,
 * over no walkthrough or deep link), what its button does (the sample day
 * and the region on it), and that the "×" keeps it away for the season it
 * was dismissed in only.
 *
 * The module reads #map-between-seasons at parse time, so the fixture
 * exists before each import; `vi.resetModules()` gives every case a fresh
 * IIFE run. Fake timers drive the settle wait.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'snowdesk.map.between-seasons';
const SETTLE_MS = 2000;
const TODAY = '2026-10-27';

function buildFixture() {
  document.body.innerHTML = `
    <div id="season-scrubber" data-today="${TODAY}"></div>
    <div id="map">
      <div id="map-utility-cluster"></div>
      <div id="map-tour-offer" hidden></div>
      <div id="map-resort-guide-bar" hidden></div>
      <div
        id="map-between-seasons"
        data-season="2025/26"
        data-sample-date="2026-04-10"
        data-region-id="CH-4115"
        hidden
      >
        <button type="button" data-between-seasons-close>×</button>
        <button type="button" data-between-seasons-explore>Explore</button>
      </div>
    </div>
  `;
}

async function load() {
  vi.resetModules();
  await import('../../static/js/map_between_seasons.js');
}

const note = () => document.getElementById('map-between-seasons');
const settle = () => {
  document.dispatchEvent(new CustomEvent('snowdesk:country-ratings-loaded'));
  vi.advanceTimersByTime(SETTLE_MS);
};
const flush = () => new Promise((resolve) => queueMicrotask(resolve));

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  history.replaceState(null, '', '/map/');
  document.documentElement.removeAttribute('data-map-sheet');
  window.readUrlDateParam = () => '';
  window.pwaTelemetry = { emit: vi.fn() };
  buildFixture();
});

afterEach(() => {
  vi.useRealTimers();
  history.replaceState(null, '', '/');
  delete window.readUrlDateParam;
  delete window.pwaTelemetry;
});

describe('when it shows', () => {
  it('waits for the map to settle', async () => {
    await load();
    expect(note().hidden).toBe(true);

    settle();

    expect(note().hidden).toBe(false);
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.between_seasons.shown', {});
  });

  it('waits for the welcome card to go', async () => {
    document.getElementById('map-tour-offer').hidden = false;
    await load();
    settle();
    expect(note().hidden).toBe(true);

    document.getElementById('map-tour-offer').hidden = true;
    await flush();

    expect(note().hidden).toBe(false);
  });

  it('never shows over a walkthrough', async () => {
    document.getElementById('map-resort-guide-bar').hidden = false;
    await load();
    settle();

    expect(note().hidden).toBe(true);
  });

  it('hides on another day and comes back on today', async () => {
    await load();
    settle();

    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-04-10' } }));
    expect(note().hidden).toBe(true);

    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: TODAY } }));
    expect(note().hidden).toBe(false);
  });

  it('stays away on a deep link', async () => {
    history.replaceState(null, '', '/map/?resort=verbier');
    await load();
    settle();

    expect(note().hidden).toBe(true);
  });
});

describe('dismissing it', () => {
  it('keeps it away for the season it was dismissed in', async () => {
    await load();
    settle();
    note().querySelector('[data-between-seasons-close]').click();

    expect(note().hidden).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe('2025/26');

    buildFixture();
    await load();
    settle();
    expect(note().hidden).toBe(true);
  });

  it('comes back in the next off-season', async () => {
    localStorage.setItem(STORAGE_KEY, '2024/25');
    await load();
    settle();

    expect(note().hidden).toBe(false);
  });
});

describe('exploring the sample', () => {
  it('moves to the sample day and opens the region', async () => {
    const events = [];
    const record = (e) => events.push([e.type, e.detail]);
    document.addEventListener('snowdesk:scrub-to', record);
    document.addEventListener('snowdesk:region-open', record);
    await load();
    settle();

    note().querySelector('[data-between-seasons-explore]').click();

    expect(note().hidden).toBe(true);
    expect(events).toEqual([
      ['snowdesk:scrub-to', { date: '2026-04-10', source: 'between-seasons' }],
      ['snowdesk:region-open', { region_id: 'CH-4115' }],
    ]);
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.between_seasons.explored', {});
    document.removeEventListener('snowdesk:scrub-to', record);
    document.removeEventListener('snowdesk:region-open', record);
  });

  it('does not come back on today after exploring', async () => {
    await load();
    settle();
    note().querySelector('[data-between-seasons-explore]').click();

    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: TODAY } }));

    expect(note().hidden).toBe(true);
  });
});
