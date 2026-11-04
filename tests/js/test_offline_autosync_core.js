/*
 * tests/js/test_offline_autosync_core.js — Vitest tests for
 * static/js/offline_autosync_core.js (SNOW-1416): what a sync fetches,
 * and which areas a background sync refreshes.
 */

import { beforeAll, describe, expect, it } from 'vitest';

let core;

beforeAll(async () => {
  await import('../../static/js/offline_autosync_core.js');
  core = self.pwaOfflineAutosyncCore;
});

const NOW = new Date('2026-11-04T12:00:00.000Z');
const MINUTES = 60 * 1000;

function ago(minutes) {
  return new Date(NOW.getTime() - minutes * MINUTES).toISOString();
}

const BULLETIN = '/bulletin/CH-1/?date=2026-11-04';
const TOMORROW = '/bulletin/CH-1/?date=2026-11-05';
const TRIP = '/trips/abc/';
const ROUTE = '/trips/abc/route.geojson';
const PLAN = [BULLETIN, TOMORROW, TRIP, ROUTE];
const PERMANENT = new Set([TRIP, ROUTE]);

describe('contentDelta', () => {
  it('fetches only what a fresh area lacks', () => {
    const held = new Set([BULLETIN, TRIP, ROUTE]);

    expect(core.contentDelta(PLAN, held, PERMANENT, { contentAt: ago(10) }, NOW)).toEqual([
      TOMORROW,
    ]);
  });

  it('fetches nothing for a fresh area that holds everything', () => {
    const held = new Set(PLAN);

    expect(core.contentDelta(PLAN, held, PERMANENT, { contentAt: ago(59) }, NOW)).toEqual([]);
  });

  it('refetches the held bulletins of a stale area, and never its held trip', () => {
    const held = new Set(PLAN);

    expect(core.contentDelta(PLAN, held, PERMANENT, { contentAt: ago(61) }, NOW)).toEqual([
      BULLETIN,
      TOMORROW,
    ]);
  });

  it('treats a never-synced or incomplete area as stale', () => {
    const held = new Set(PLAN);

    expect(core.contentDelta(PLAN, held, PERMANENT, {}, NOW)).toEqual([BULLETIN, TOMORROW]);
    expect(
      core.contentDelta(PLAN, held, PERMANENT, { contentAt: ago(5), contentIncomplete: true }, NOW),
    ).toEqual([BULLETIN, TOMORROW]);
  });

  it('fetches a missing trip whatever the age', () => {
    expect(core.contentDelta(PLAN, new Set([BULLETIN, TOMORROW]), PERMANENT, { contentAt: ago(1) }, NOW)).toEqual([
      TRIP,
      ROUTE,
    ]);
  });
});

describe('dueAreas', () => {
  it('is due when content is old or missing', () => {
    const areas = [
      { id: 'old', record: { contentAt: ago(90) } },
      { id: 'never', record: {} },
      { id: 'fresh', record: { contentAt: ago(30) } },
    ];

    expect(core.dueAreas(areas, {}, NOW)).toEqual(['old', 'never']);
  });

  it('does not retry an area attempted within the hour, even when its content is old', () => {
    const areas = [{ id: 'failing', record: { contentAt: ago(600) } }];

    expect(core.dueAreas(areas, { failing: ago(20) }, NOW)).toEqual([]);
    expect(core.dueAreas(areas, { failing: ago(61) }, NOW)).toEqual(['failing']);
  });

  it('answers nothing for no areas', () => {
    expect(core.dueAreas([], {}, NOW)).toEqual([]);
    expect(core.dueAreas(undefined, undefined, NOW)).toEqual([]);
  });

  it('reads an unreadable stamp as stale and an unreadable attempt as long ago', () => {
    const areas = [{ id: 'a', record: { contentAt: 'not a date' } }];

    expect(core.dueAreas(areas, { a: 'nope' }, NOW)).toEqual(['a']);
  });
});
