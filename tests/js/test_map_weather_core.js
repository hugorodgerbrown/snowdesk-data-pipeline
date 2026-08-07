/*
 * tests/js/test_map_weather_core.js — Vitest unit tests for
 * static/js/map_weather_core.js (SNOW-573).
 *
 * `installWeatherLayer` in map.js needs a real MapLibre `map`, so it isn't
 * unit-tested here — these tests cover the pure projection/window logic it
 * relies on: bucket → icon filename passthrough, the days[date] → flat
 * feature properties projection, and the forecast-window guard.
 *
 * `map_weather_core.js` is a plain browser IIFE that assigns a frozen
 * `window.pwaWeatherCore` — importing it for side effects is enough.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_weather_core.js';

const core = window.pwaWeatherCore;

function makeFeature(days) {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [7.4, 46.1] },
    properties: { resort_id: 1, name: 'Verbier', region_id: 'CH-4115', days: days },
  };
}

describe('formatTempLabel', () => {
  it('rounds a positive temperature to the nearest degree with a ° suffix', () => {
    expect(core.formatTempLabel(4.4)).toBe('4°');
    expect(core.formatTempLabel(4.6)).toBe('5°');
  });

  it('rounds a negative temperature correctly', () => {
    expect(core.formatTempLabel(-3.2)).toBe('-3°');
  });

  it('renders zero as 0°, not an empty string', () => {
    expect(core.formatTempLabel(0)).toBe('0°');
  });

  it('returns empty string for null, undefined, or non-finite input', () => {
    expect(core.formatTempLabel(null)).toBe('');
    expect(core.formatTempLabel(undefined)).toBe('');
    expect(core.formatTempLabel(NaN)).toBe('');
    expect(core.formatTempLabel(Infinity)).toBe('');
  });
});

describe('projectFeatureForDate', () => {
  it('projects the matching date entry onto flat icon/temp_label/condition_label', () => {
    const feature = makeFeature({
      '2026-08-07': { icon: 'light_snow-day.svg', label: 'Light snow', tmax: 4.0, tmin: -3.0, snow: 2.0 },
    });

    const projected = core.projectFeatureForDate(feature, '2026-08-07');

    expect(projected.properties.icon).toBe('light_snow-day.svg');
    expect(projected.properties.temp_label).toBe('4°');
    expect(projected.properties.condition_label).toBe('Light snow');
    // resort_id/name/region_id pass through unchanged.
    expect(projected.properties.resort_id).toBe(1);
    expect(projected.properties.name).toBe('Verbier');
  });

  it('yields empty-string properties for a date absent from days', () => {
    const feature = makeFeature({
      '2026-08-07': { icon: 'clear-day.svg', label: 'Clear', tmax: 4.0, tmin: -3.0, snow: 0.0 },
    });

    const projected = core.projectFeatureForDate(feature, '2026-08-20');

    expect(projected.properties.icon).toBe('');
    expect(projected.properties.temp_label).toBe('');
    expect(projected.properties.condition_label).toBe('');
  });

  it('does not mutate the input feature', () => {
    const feature = makeFeature({
      '2026-08-07': { icon: 'clear-day.svg', label: 'Clear', tmax: 4.0, tmin: -3.0, snow: 0.0 },
    });
    const originalProperties = feature.properties;

    core.projectFeatureForDate(feature, '2026-08-07');

    expect(feature.properties).toBe(originalProperties);
    expect(feature.properties.icon).toBeUndefined();
  });

  it('handles a feature with no days property at all', () => {
    const feature = {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.4, 46.1] },
      properties: { resort_id: 1 },
    };

    const projected = core.projectFeatureForDate(feature, '2026-08-07');

    expect(projected.properties.icon).toBe('');
    expect(projected.properties.temp_label).toBe('');
  });
});

describe('projectFeatureCollectionForDate', () => {
  it('projects every feature in the collection', () => {
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({ '2026-08-07': { icon: 'clear-day.svg', label: 'Clear', tmax: 4.0, tmin: -3.0, snow: 0.0 } }),
        makeFeature({ '2026-08-07': { icon: 'heavy_snow-day.svg', label: 'Heavy snow', tmax: -1.0, tmin: -8.0, snow: 12.0 } }),
      ],
    };

    const projected = core.projectFeatureCollectionForDate(fc, '2026-08-07');

    expect(projected.type).toBe('FeatureCollection');
    expect(projected.features).toHaveLength(2);
    expect(projected.features[0].properties.icon).toBe('clear-day.svg');
    expect(projected.features[1].properties.icon).toBe('heavy_snow-day.svg');
  });

  it('returns an empty FeatureCollection for null/undefined input', () => {
    expect(core.projectFeatureCollectionForDate(null, '2026-08-07')).toEqual({
      type: 'FeatureCollection',
      features: [],
    });
    expect(core.projectFeatureCollectionForDate(undefined, '2026-08-07')).toEqual({
      type: 'FeatureCollection',
      features: [],
    });
  });
});

describe('forecastWindowDates', () => {
  it('returns the sorted union of dates across every feature', () => {
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({
          '2026-08-08': { icon: 'clear-day.svg', label: 'Clear', tmax: 4, tmin: -3, snow: 0 },
          '2026-08-07': { icon: 'clear-day.svg', label: 'Clear', tmax: 4, tmin: -3, snow: 0 },
        }),
        // A short-window point (SNOW-573: ICON-CH2 often returns fewer than
        // POINT_FORECAST_DAYS) contributes only one extra date.
        makeFeature({
          '2026-08-09': { icon: 'clear-day.svg', label: 'Clear', tmax: 4, tmin: -3, snow: 0 },
        }),
      ],
    };

    expect(core.forecastWindowDates(fc)).toEqual(['2026-08-07', '2026-08-08', '2026-08-09']);
  });

  it('returns an empty array for an empty or absent FeatureCollection', () => {
    expect(core.forecastWindowDates({ type: 'FeatureCollection', features: [] })).toEqual([]);
    expect(core.forecastWindowDates(null)).toEqual([]);
  });
});

describe('isDateInForecastWindow', () => {
  const fc = {
    type: 'FeatureCollection',
    features: [
      makeFeature({
        '2026-08-07': { icon: 'clear-day.svg', label: 'Clear', tmax: 4, tmin: -3, snow: 0 },
      }),
    ],
  };

  it('is true for a date present in the payload', () => {
    expect(core.isDateInForecastWindow('2026-08-07', fc)).toBe(true);
  });

  it('is false for a date outside the stored window (e.g. a historical scrub)', () => {
    expect(core.isDateInForecastWindow('2025-01-01', fc)).toBe(false);
  });

  it('is false for every date when the payload is empty', () => {
    expect(core.isDateInForecastWindow('2026-08-07', { type: 'FeatureCollection', features: [] })).toBe(false);
  });
});

describe('iconFilenamesForPayload', () => {
  it('returns the sorted, deduplicated set of icon filenames used by the payload', () => {
    const fc = {
      type: 'FeatureCollection',
      features: [
        makeFeature({
          '2026-08-07': { icon: 'clear-day.svg', label: 'Clear', tmax: 4, tmin: -3, snow: 0 },
          '2026-08-08': { icon: 'light_snow-day.svg', label: 'Light snow', tmax: 1, tmin: -4, snow: 2 },
        }),
        makeFeature({
          // Same icon as feature 0's first day — must be deduplicated.
          '2026-08-07': { icon: 'clear-day.svg', label: 'Clear', tmax: 5, tmin: -2, snow: 0 },
        }),
      ],
    };

    expect(core.iconFilenamesForPayload(fc)).toEqual(['clear-day.svg', 'light_snow-day.svg']);
  });

  it('returns an empty array when the payload has no weather yet', () => {
    const fc = { type: 'FeatureCollection', features: [makeFeature({})] };
    expect(core.iconFilenamesForPayload(fc)).toEqual([]);
  });
});
