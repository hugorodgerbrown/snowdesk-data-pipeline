/*
 * tests/js/test_community_reports_core.js — the "On the ground" layer's
 * states, window and pin age (static/js/community_reports_core.js,
 * SNOW-1290).
 *
 * The states are the feature: a report is LIVE on the day it was made,
 * RECENT for the three days after, and on a scrubbed map only that day's
 * reports are drawn, as DAY pins. Every edge of that is a string compare
 * on YYYY-MM-DD days, which is exactly what gets an off-by-one, so each
 * edge is written down here.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/community_reports_core.js';

const core = self.pwaCommunityReportsCore;

const TODAY = '2026-10-29';

/** A one-report collection for the given day and time. */
function collection(observedOn, observedAt = `${observedOn}T09:00:00+00:00`, family = 'SURFACE') {
  return {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.5, 46.1] },
      properties: { observed_on: observedOn, observed_at: observedAt, family },
    }],
  };
}

describe('addDays', () => {
  it('crosses a month and a year', () => {
    expect(core.addDays('2026-11-01', -1)).toBe('2026-10-31');
    expect(core.addDays('2026-12-31', 1)).toBe('2027-01-01');
  });

  it('is not moved by a daylight-saving change', () => {
    // Europe moves its clocks on 25 October 2026.
    expect(core.addDays('2026-10-26', -1)).toBe('2026-10-25');
  });
});

describe('reportState — the map shows today', () => {
  it('is live for a report made today', () => {
    expect(core.reportState(TODAY, TODAY, TODAY)).toBe('live');
  });

  it('treats no display date as today', () => {
    expect(core.reportState(TODAY, null, TODAY)).toBe('live');
  });

  it('is recent for each of the three days before', () => {
    expect(core.reportState('2026-10-28', TODAY, TODAY)).toBe('recent');
    expect(core.reportState('2026-10-26', TODAY, TODAY)).toBe('recent');
  });

  it('drops a report from the fourth day back', () => {
    expect(core.reportState('2026-10-25', TODAY, TODAY)).toBeNull();
  });

  it('drops a report dated after today', () => {
    expect(core.reportState('2026-10-30', TODAY, TODAY)).toBeNull();
  });
});

describe('reportState — the map shows another day', () => {
  it('is a day pin for a report made on that day', () => {
    expect(core.reportState('2026-10-20', '2026-10-20', TODAY)).toBe('day');
  });

  it('draws nothing from the days either side', () => {
    expect(core.reportState('2026-10-19', '2026-10-20', TODAY)).toBeNull();
    expect(core.reportState('2026-10-21', '2026-10-20', TODAY)).toBeNull();
  });

  it('is never live on yesterday, even for yesterday\'s reports', () => {
    expect(core.reportState('2026-10-28', '2026-10-28', TODAY)).toBe('day');
  });
});

describe('reportState — nothing to go on', () => {
  it('draws nothing with no day known', () => {
    expect(core.reportState(TODAY, TODAY, null)).toBeNull();
    expect(core.reportState(null, TODAY, TODAY)).toBeNull();
  });
});

describe('ageLabel', () => {
  const now = Date.parse('2026-10-29T12:00:00Z');

  it('reads minutes under an hour, never 0m', () => {
    expect(core.ageLabel('2026-10-29T11:59:40Z', now)).toBe('1m');
    expect(core.ageLabel('2026-10-29T11:48:00Z', now)).toBe('12m');
    expect(core.ageLabel('2026-10-29T11:00:01Z', now)).toBe('59m');
  });

  it('reads whole hours from an hour', () => {
    expect(core.ageLabel('2026-10-29T11:00:00Z', now)).toBe('1h');
    expect(core.ageLabel('2026-10-29T08:30:00Z', now)).toBe('3h');
  });

  it('reads a future or unreadable time as just filed', () => {
    expect(core.ageLabel('2026-10-29T12:05:00Z', now)).toBe('1m');
    expect(core.ageLabel('not a time', now)).toBe('1m');
    expect(core.ageLabel(null, now)).toBe('1m');
  });
});

describe('withStates', () => {
  const now = Date.parse('2026-10-29T12:00:00Z');

  it('stamps a live report with its state and its age', () => {
    const out = core.withStates(collection(TODAY, '2026-10-29T09:00:00Z'), TODAY, TODAY, now);
    expect(out.features[0].properties._state).toBe('live');
    expect(out.features[0].properties._age).toBe('3h');
  });

  it('gives a recent report no age: the ring says it', () => {
    const out = core.withStates(collection('2026-10-27'), TODAY, TODAY, now);
    expect(out.features[0].properties._state).toBe('recent');
    expect(out.features[0].properties).not.toHaveProperty('_age');
  });

  it('drops what the window no longer holds', () => {
    // An offline read-back of a payload cached days ago.
    expect(core.withStates(collection('2026-10-20'), TODAY, TODAY, now).features).toEqual([]);
  });

  it('never mutates the payload the offline cache keeps', () => {
    const payload = collection(TODAY);
    core.withStates(payload, TODAY, TODAY, now);
    expect(payload.features[0].properties).not.toHaveProperty('_state');
  });

  it('reads an unknown family as a warning sign, the safe default', () => {
    const out = core.withStates(collection(TODAY, undefined, 'MYSTERY'), TODAY, TODAY, now);
    expect(out.features[0].properties.family).toBe('WARNING');
  });

  it('hands back anything that is not a collection unchanged', () => {
    expect(core.withStates(null, TODAY, TODAY, now)).toBeNull();
    const odd = { type: 'FeatureCollection' };
    expect(core.withStates(odd, TODAY, TODAY, now)).toBe(odd);
  });
});

describe('fetchUrl', () => {
  const base = '/api/community-reports.geojson';

  it('asks today with the bare URL the offline cache has always used', () => {
    expect(core.fetchUrl(base, TODAY, TODAY)).toBe(base);
    expect(core.fetchUrl(base, null, TODAY)).toBe(base);
  });

  it('asks another day with ?d=', () => {
    expect(core.fetchUrl(base, '2026-10-20', TODAY)).toBe(`${base}?d=2026-10-20`);
    expect(core.fetchUrl(`${base}?x=1`, '2026-10-20', TODAY)).toBe(`${base}?x=1&d=2026-10-20`);
  });
});

describe('asList', () => {
  it('passes an array through and parses JSON text', () => {
    expect(core.asList(['POWDER', 'BUSY'])).toEqual(['POWDER', 'BUSY']);
    expect(core.asList('["POWDER","BUSY"]')).toEqual(['POWDER', 'BUSY']);
  });

  it('reads a bare string as one item, and nothing as none', () => {
    expect(core.asList('POWDER')).toEqual(['POWDER']);
    expect(core.asList('')).toEqual([]);
    expect(core.asList(undefined)).toEqual([]);
    expect(core.asList('{"a":1}')).toEqual(['{"a":1}']);
  });
});

describe('the family table', () => {
  it('has a colour and a drawing for every family, most serious first', () => {
    expect(core.FAMILIES).toEqual(['WARNING', 'SURFACE', 'TRAIL']);
    for (const family of core.FAMILIES) {
      expect(core.FAMILY_COLOURS[family]).toMatch(/^#[0-9a-f]{6}$/);
      expect(core.FAMILY_PATHS[family].length).toBeGreaterThan(0);
    }
  });
});
