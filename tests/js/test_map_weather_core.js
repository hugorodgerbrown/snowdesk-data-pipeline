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
 *   - `formatTemp`, the day's max temperature as the symbol's caption;
 *   - the elevation trio — `formatElevation`, `formatElevationBreak` and
 *     `elevationMarkFor` — which have to agree on emptiness, because the
 *     layer renders all three as fixed `format` sections;
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

describe('formatTemp', () => {
  it('rounds to a whole degree', () => {
    expect(core.formatTemp(4.4)).toBe('4°');
    expect(core.formatTemp(-3.2)).toBe('-3°');
  });

  it('renders zero degrees as 0°, not as nothing', () => {
    // `0` is falsy, so a truthiness check here would silently drop the
    // reading on exactly the day a skier cares most about.
    expect(core.formatTemp(0)).toBe('0°');
  });

  it('returns an empty caption when there is no finite reading', () => {
    // The symbol still draws; only the caption goes. A stray character
    // under an icon would read as a rendering fault.
    expect(core.formatTemp(null)).toBe('');
    expect(core.formatTemp(undefined)).toBe('');
    expect(core.formatTemp(NaN)).toBe('');
  });

  it('does not carry the station elevation', () => {
    // The label used to be "4°\n3328 m". Every marker carrying its own
    // height made the map unreadable at any density, so elevation went —
    // it earns its place on a resort page showing three points at three
    // heights, not here. It still drives the cluster collapse.
    expect(core.formatTemp(4)).not.toContain('m');
    expect(core.formatLabel).toBeUndefined();
  });
});

describe('the elevation trio', () => {
  it('formats a rounded metre value', () => {
    expect(core.formatElevation(3328)).toBe('3328 m');
    expect(core.formatElevation(1500.6)).toBe('1501 m');
  });

  it('carries the row break and the mark alongside a real elevation', () => {
    expect(core.formatElevationBreak(3328)).toBe('\n');
    expect(core.elevationMarkFor(3328)).toBe(core.ELEVATION_MARK_ID);
  });

  it('goes empty together when the elevation is unresolved', () => {
    // THE POINT OF THE TRIO. A `format` expression is fixed at style time,
    // so all three sections render for every symbol and the only lever is
    // what each one contains. If the break survived an absent elevation
    // the chip would render a blank second row and sit off its own point;
    // if the mark survived it, a stray glyph would land on row one.
    for (const missing of [null, undefined, NaN]) {
      expect(core.formatElevation(missing)).toBe('');
      expect(core.formatElevationBreak(missing)).toBe('');
      expect(core.elevationMarkFor(missing)).toBe('');
    }
  });

  it('treats sea level as a real elevation, not as absent', () => {
    // `0` is falsy. A truthiness check here would drop the row for a
    // coastal station and keep it for every other one.
    expect(core.formatElevation(0)).toBe('0 m');
    expect(core.formatElevationBreak(0)).toBe('\n');
    expect(core.elevationMarkFor(0)).toBe(core.ELEVATION_MARK_ID);
  });
});

describe('projectFeatureForDate', () => {
  it('projects the day entry onto flat icon and label properties', () => {
    const feature = makeFeature({ '2026-08-30': { code: 71, tmax: 4 } }, { elevation: 3328 });

    const projected = core.projectFeatureForDate(feature, '2026-08-30');

    expect(projected.properties.icon).toBe('light_snow-day.svg');
    expect(projected.properties.label_temp).toBe('4°');
    expect(projected.properties.label_break).toBe('\n');
    expect(projected.properties.elev_mark).toBe(core.ELEVATION_MARK_ID);
    expect(projected.properties.label_elev).toBe('3328 m');
  });

  it('projects an empty elevation row when the station has no height', () => {
    const feature = makeFeature(
      { '2026-08-30': { code: 71, tmax: 4 } }, { elevation: null },
    );

    const projected = core.projectFeatureForDate(feature, '2026-08-30');

    expect(projected.properties.label_temp).toBe('4°');
    expect(projected.properties.label_break).toBe('');
    expect(projected.properties.elev_mark).toBe('');
    expect(projected.properties.label_elev).toBe('');
  });

  it('projects an empty icon when the feature has no entry for the date', () => {
    // The layer filters on icon !== '', so this feature draws nothing at
    // all rather than an empty symbol reserving a collision box.
    const feature = makeFeature({ '2026-08-30': { code: 71, tmax: 4 } });

    const projected = core.projectFeatureForDate(feature, '2026-09-30');

    expect(projected.properties.icon).toBe('');
    expect(projected.properties.label_temp).toBe('');
    // The whole label goes with the day, elevation row included — the
    // station's height is not news on a date it has no reading for.
    expect(projected.properties.label_break).toBe('');
    expect(projected.properties.elev_mark).toBe('');
    expect(projected.properties.label_elev).toBe('');
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
