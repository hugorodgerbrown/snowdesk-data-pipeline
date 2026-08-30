/*
 * tests/js/test_map_weather_core.js — Vitest unit tests for
 * static/js/map_weather_core.js (SNOW-761).
 *
 * `installWeatherLayer` in map.js needs a real MapLibre `map`, so it is not
 * unit-tested here — these cover the three pure transforms the overlay
 * rests on:
 *
 *   - `iconForCode`, now that the feed serves the WMO code rather than a
 *     filename (the Python half of that table is guarded from pytest, in
 *     tests/weather/services/test_icon_table_parity.py);
 *   - `formatLabel`, which puts the station's altitude under the day's max
 *     temperature — the whole reason a map symbol is readable on a mountain;
 *   - `collapseToLowest`, the cluster-to-the-valley-station transform that
 *     MapLibre's own `clusterProperties` cannot express, having min/max but
 *     no argmin.
 *
 * `map_weather_core.js` is a plain browser IIFE that assigns a frozen
 * `window.pwaWeatherCore` — importing it for side effects is enough.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_weather_core.js';

const core = window.pwaWeatherCore;

function makeFeature(days, { lon = 7.4, lat = 46.1, elevation = 1500, id = 1 } = {}) {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [lon, lat] },
    properties: { location_id: id, name: 'Station', elevation_m: elevation, days },
  };
}

describe('iconForCode', () => {
  it('maps a known code to its day-variant Meteocons file', () => {
    expect(core.iconForCode(0)).toBe('clear-day.svg');
    expect(core.iconForCode(71)).toBe('light_snow-day.svg');
    expect(core.iconForCode(95)).toBe('thunder-day.svg');
  });

  it('never returns a night variant', () => {
    // A map symbol summarises a whole day, so the day icon is correct at
    // any hour — see the module docstring.
    const everyIcon = Array.from({ length: 100 }, (_, code) => core.iconForCode(code));
    expect(everyIcon.some((name) => name.includes('-night'))).toBe(false);
  });

  it('ships cloudy without a day/night suffix', () => {
    expect(core.iconForCode(3)).toBe('cloudy.svg');
  });

  it('falls back to cloudy for an unknown, null or missing code', () => {
    expect(core.iconForCode(4)).toBe('cloudy.svg');
    expect(core.iconForCode(null)).toBe('cloudy.svg');
    expect(core.iconForCode(undefined)).toBe('cloudy.svg');
  });
});

describe('formatLabel', () => {
  it('puts the rounded temperature above the rounded elevation', () => {
    expect(core.formatLabel(4.4, 3328)).toBe('4°\n3328 m');
    expect(core.formatLabel(-3.2, 1500.6)).toBe('-3°\n1501 m');
  });

  it('renders zero degrees as 0°, not as nothing', () => {
    expect(core.formatLabel(0, 1500)).toBe('0°\n1500 m');
  });

  it('drops the elevation line when the station has no resolved height', () => {
    // Elevation is resolved out of band, so a freshly imported location
    // has none — the label falls back rather than printing a gap.
    expect(core.formatLabel(4, null)).toBe('4°');
    expect(core.formatLabel(4, undefined)).toBe('4°');
  });

  it('drops the temperature line when tmax is missing', () => {
    expect(core.formatLabel(null, 2000)).toBe('2000 m');
  });

  it('returns an empty label when it has neither', () => {
    expect(core.formatLabel(null, null)).toBe('');
    expect(core.formatLabel(NaN, NaN)).toBe('');
  });
});

describe('projectFeatureForDate', () => {
  it('projects the day entry onto flat icon and label properties', () => {
    const feature = makeFeature({ '2026-08-30': { code: 71, tmax: 4 } }, { elevation: 3328 });

    const projected = core.projectFeatureForDate(feature, '2026-08-30');

    expect(projected.properties.icon).toBe('light_snow-day.svg');
    expect(projected.properties.label).toBe('4°\n3328 m');
  });

  it('projects an empty icon when the feature has no entry for the date', () => {
    // The layer filters on icon !== '', so this feature draws nothing at
    // all rather than an empty symbol reserving a collision box.
    const feature = makeFeature({ '2026-08-30': { code: 71, tmax: 4 } });

    const projected = core.projectFeatureForDate(feature, '2026-09-30');

    expect(projected.properties.icon).toBe('');
    expect(projected.properties.label).toBe('');
  });

  it('does not mutate the input feature', () => {
    const feature = makeFeature({ '2026-08-30': { code: 71, tmax: 4 } });

    core.projectFeatureForDate(feature, '2026-08-30');

    expect(feature.properties.icon).toBeUndefined();
  });

  it('keeps the other properties and the geometry', () => {
    const feature = makeFeature({ '2026-08-30': { code: 0, tmax: 1 } }, { id: 42 });

    const projected = core.projectFeatureForDate(feature, '2026-08-30');

    expect(projected.properties.location_id).toBe(42);
    expect(projected.geometry).toEqual(feature.geometry);
  });
});

describe('projectFeatureCollectionForDate', () => {
  it('projects every feature and returns a FeatureCollection', () => {
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({ '2026-08-30': { code: 0, tmax: 6 } }, { id: 1 }),
        makeFeature({ '2026-08-30': { code: 73, tmax: -2 } }, { id: 2 }),
      ],
    };

    const projected = core.projectFeatureCollectionForDate(fc, '2026-08-30');

    expect(projected.type).toBe('FeatureCollection');
    expect(projected.features.map((f) => f.properties.icon)).toEqual([
      'clear-day.svg',
      'moderate_snow-day.svg',
    ]);
  });

  it('returns an empty collection for a null payload', () => {
    expect(core.projectFeatureCollectionForDate(null, '2026-08-30')).toEqual({
      type: 'FeatureCollection',
      features: [],
    });
  });
});

describe('forecastWindowDates / isDateInForecastWindow', () => {
  const fc = {
    type: 'FeatureCollection',
    features: [
      makeFeature({ '2026-08-31': { code: 0, tmax: 1 }, '2026-08-30': { code: 0, tmax: 1 } }),
      makeFeature({ '2026-09-01': { code: 0, tmax: 1 }, '2026-08-30': { code: 0, tmax: 1 } }),
    ],
  };

  it('returns the sorted, deduplicated union across every feature', () => {
    expect(core.forecastWindowDates(fc)).toEqual([
      '2026-08-30',
      '2026-08-31',
      '2026-09-01',
    ]);
  });

  it('returns an empty window for a null payload', () => {
    expect(core.forecastWindowDates(null)).toEqual([]);
  });

  it('answers membership against that window', () => {
    expect(core.isDateInForecastWindow('2026-08-31', fc)).toBe(true);
    expect(core.isDateInForecastWindow('2026-02-14', fc)).toBe(false);
  });
});

describe('iconFilenamesForPayload', () => {
  it('returns the sorted set of icons the payload actually needs', () => {
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({ '2026-08-30': { code: 71, tmax: 1 }, '2026-08-31': { code: 0, tmax: 2 } }),
        makeFeature({ '2026-08-30': { code: 71, tmax: 1 } }),
      ],
    };

    expect(core.iconFilenamesForPayload(fc)).toEqual([
      'clear-day.svg',
      'light_snow-day.svg',
    ]);
  });

  it('returns an empty set for a null payload', () => {
    expect(core.iconFilenamesForPayload(null)).toEqual([]);
  });
});

describe('collapseToLowest', () => {
  it('keeps the lowest station of an overlapping pair', () => {
    // The valley station is the one a reader recognises, and its freezing
    // level is the one that decides whether it is raining where they parked.
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({}, { lon: 7.4, lat: 46.1, elevation: 3328, id: 'peak' }),
        makeFeature({}, { lon: 7.41, lat: 46.1, elevation: 1500, id: 'village' }),
      ],
    };

    const collapsed = core.collapseToLowest(fc, 0.1);

    expect(collapsed.features).toHaveLength(1);
    expect(collapsed.features[0].properties.location_id).toBe('village');
  });

  it('keeps the lowest whichever order the members arrive in', () => {
    const low = makeFeature({}, { lon: 7.4, lat: 46.1, elevation: 800, id: 'low' });
    const high = makeFeature({}, { lon: 7.41, lat: 46.1, elevation: 2600, id: 'high' });

    for (const features of [[low, high], [high, low]]) {
      const collapsed = core.collapseToLowest(
        { type: 'FeatureCollection', features },
        0.1,
      );
      expect(collapsed.features[0].properties.location_id).toBe('low');
    }
  });

  it('keeps stations further apart than the radius separate', () => {
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({}, { lon: 7.4, lat: 46.1, elevation: 3328, id: 'a' }),
        makeFeature({}, { lon: 9.9, lat: 46.1, elevation: 1500, id: 'b' }),
      ],
    };

    const collapsed = core.collapseToLowest(fc, 0.1);

    expect(collapsed.features.map((f) => f.properties.location_id)).toEqual(['a', 'b']);
  });

  it('sorts a station with no resolved elevation as infinitely high', () => {
    // Missing data must not beat a height we actually know — otherwise the
    // cluster collapses onto the one pin that cannot label itself.
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({}, { lon: 7.4, lat: 46.1, elevation: null, id: 'unknown' }),
        makeFeature({}, { lon: 7.41, lat: 46.1, elevation: 3328, id: 'known' }),
      ],
    };

    const collapsed = core.collapseToLowest(fc, 0.1);

    expect(collapsed.features[0].properties.location_id).toBe('known');
  });

  it('returns every feature when the radius is zero or negative', () => {
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({}, { lon: 7.4, lat: 46.1, id: 'a' }),
        makeFeature({}, { lon: 7.4001, lat: 46.1, id: 'b' }),
      ],
    };

    expect(core.collapseToLowest(fc, 0).features).toHaveLength(2);
    expect(core.collapseToLowest(fc, -1).features).toHaveLength(2);
  });

  it('does not mutate the input collection', () => {
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({}, { lon: 7.4, lat: 46.1, elevation: 3328, id: 'a' }),
        makeFeature({}, { lon: 7.41, lat: 46.1, elevation: 1500, id: 'b' }),
      ],
    };

    core.collapseToLowest(fc, 0.1);

    expect(fc.features).toHaveLength(2);
  });

  it('returns an empty collection for a null payload', () => {
    expect(core.collapseToLowest(null, 0.1)).toEqual({
      type: 'FeatureCollection',
      features: [],
    });
  });
});
