/*
 * static/js/map_weather_core.js — pure helpers for the map Weather overlay
 * (SNOW-573).
 *
 * `installWeatherLayer` in map.js needs a real MapLibre `map`, so it isn't
 * unit-tested directly — but the projection logic it drives on every
 * scrubber date change has nothing to do with MapLibre at all: it is a pure
 * transform from the `days`-keyed GeoJSON payload
 * (`/api/forecast-weather.geojson`, `favourites.geojson`) onto the flat
 * `icon`/`temp_label` feature properties a MapLibre symbol layer's
 * data-driven expressions (`['get', 'icon']`, `['get', 'temp_label']`) can
 * read. Extracted here so it is covered by
 * `tests/js/test_map_weather_core.js`, following the same split as
 * `choropleth_core.js` / `scrubber_core.js` / `search_core.js`.
 *
 * Because the payload carries the *whole* forecast window (not one day),
 * `map.js` never re-fetches on `snowdesk:date-changed` — it re-projects the
 * already-fetched FeatureCollection in memory and calls `source.setData()`.
 * That re-projection, the forecast-window membership check that drives the
 * layers-menu row's disabled state, and the "which icons does this payload
 * actually need decoded" set are all pure functions of the payload, so they
 * live here rather than inline in map.js.
 *
 * Deliberately dependency-free: every export is a pure function of its
 * arguments, no module-scope state, no MapLibre/DOM reference.
 *
 * Attached to `window` (not `self`) — like `scrubber_core.js`, every
 * consumer is a page script; there is no service-worker context to share
 * with.
 *
 * Public API — attached to `window.pwaWeatherCore`:
 *
 *   formatTempLabel(tmax)
 *     The day's max temperature (°C, possibly null/undefined) → the short
 *     label a symbol layer's text-field renders, e.g. "4°". Returns ''
 *     (MapLibre renders an empty label, not a stray character) when tmax is
 *     not a finite number.
 *   projectFeatureForDate(feature, dateKey)
 *     One weather GeoJSON Feature (carrying a `properties.days` dict) →
 *     a new Feature whose properties also carry flat `icon`/`temp_label`
 *     fields for `dateKey`. `icon`/`temp_label` are '' when the feature has
 *     no entry for `dateKey` — a MapLibre `match` on '' safely resolves to
 *     "draw nothing distinguishable" rather than throwing.
 *   projectFeatureCollectionForDate(featureCollection, dateKey)
 *     Maps `projectFeatureForDate` over every feature in a
 *     FeatureCollection, returning a new FeatureCollection — the shape
 *     `source.setData()` expects.
 *   forecastWindowDates(featureCollection)
 *     The sorted, deduplicated union of every ISO date key present across
 *     every feature's `days` dict — the layer's forecast window.
 *   isDateInForecastWindow(dateKey, featureCollection)
 *     True when `dateKey` is a member of `forecastWindowDates`. Point
 *     weather is forecast-only and often runs short (ICON-CH2 commonly
 *     returns 5 of the requested 7 days) — the layers-menu row disables
 *     itself, with a reason, for any scrubber date outside this set rather
 *     than silently drawing nothing.
 *   iconFilenamesForPayload(featureCollection)
 *     The sorted, deduplicated set of every icon filename referenced across
 *     every feature's `days` dict — the exact set `installWeatherLayer`
 *     needs to rasterise via `map.addImage`, so a re-register after a
 *     basemap `setStyle` never decodes an icon the payload doesn't use.
 *   extractWeatherFeatures(featureCollection)
 *     The subset of a FeatureCollection's features that carry a `days`
 *     property — pulls the weather-bearing subset out of
 *     `favourites.geojson` (only present when the `weather_layer` flag is
 *     active) for the client-side merge into the map's weather source. A
 *     favourite's synthetic offline `pending` feature (SNOW-479, no `days`
 *     yet) is naturally excluded.
 *   mergeFeatureCollections(a, b)
 *     Concatenates two FeatureCollections' feature arrays into one new
 *     FeatureCollection — the public resort-anchored payload plus the
 *     signed-in visitor's own favourite-anchored features.
 */

(function () {
  'use strict';

  /**
   * The day's max temperature → the short symbol-layer text-field label.
   *
   * @param {number|null|undefined} tmax
   * @returns {string} e.g. `'4°'`, or `''` when `tmax` is not finite.
   */
  function formatTempLabel(tmax) {
    if (typeof tmax !== 'number' || !Number.isFinite(tmax)) return '';
    return Math.round(tmax) + '°';
  }

  /**
   * Project one Feature's `days[dateKey]` entry onto flat properties.
   *
   * @param {{type: string, geometry: Object, properties: Object}} feature
   * @param {string} dateKey ISO date, e.g. `'2026-08-07'`.
   * @returns {{type: string, geometry: Object, properties: Object}} A new
   *   Feature — the input is never mutated.
   */
  function projectFeatureForDate(feature, dateKey) {
    var properties = (feature && feature.properties) || {};
    var days = properties.days || {};
    var day = days[dateKey] || null;
    var newProperties = Object.assign({}, properties, {
      icon: day ? day.icon : '',
      temp_label: day ? formatTempLabel(day.tmax) : '',
      condition_label: day ? day.label : '',
    });
    return Object.assign({}, feature, { properties: newProperties });
  }

  /**
   * Project every Feature in a FeatureCollection for one date.
   *
   * @param {{type: string, features: Array<Object>}|null|undefined} featureCollection
   * @param {string} dateKey ISO date, e.g. `'2026-08-07'`.
   * @returns {{type: string, features: Array<Object>}} A new
   *   FeatureCollection — the input is never mutated. `{type:
   *   'FeatureCollection', features: []}` when `featureCollection` is
   *   absent.
   */
  function projectFeatureCollectionForDate(featureCollection, dateKey) {
    var features =
      (featureCollection && featureCollection.features) || [];
    return {
      type: 'FeatureCollection',
      features: features.map(function (feature) {
        return projectFeatureForDate(feature, dateKey);
      }),
    };
  }

  /**
   * The sorted, deduplicated union of every date key across every feature.
   *
   * @param {{type: string, features: Array<Object>}|null|undefined} featureCollection
   * @returns {string[]} Ascending ISO date strings.
   */
  function forecastWindowDates(featureCollection) {
    var features = (featureCollection && featureCollection.features) || [];
    var seen = Object.create(null);
    for (var i = 0; i < features.length; i++) {
      var days = (features[i].properties && features[i].properties.days) || {};
      var keys = Object.keys(days);
      for (var j = 0; j < keys.length; j++) {
        seen[keys[j]] = true;
      }
    }
    return Object.keys(seen).sort();
  }

  /**
   * True when `dateKey` is inside the payload's forecast window.
   *
   * @param {string} dateKey ISO date, e.g. `'2026-08-07'`.
   * @param {{type: string, features: Array<Object>}|null|undefined} featureCollection
   * @returns {boolean}
   */
  function isDateInForecastWindow(dateKey, featureCollection) {
    return forecastWindowDates(featureCollection).indexOf(dateKey) !== -1;
  }

  /**
   * The sorted, deduplicated set of icon filenames the payload references.
   *
   * @param {{type: string, features: Array<Object>}|null|undefined} featureCollection
   * @returns {string[]} Sorted icon filenames, e.g. `['clear-day.svg', ...]`.
   */
  function iconFilenamesForPayload(featureCollection) {
    var features = (featureCollection && featureCollection.features) || [];
    var seen = Object.create(null);
    for (var i = 0; i < features.length; i++) {
      var days = (features[i].properties && features[i].properties.days) || {};
      var keys = Object.keys(days);
      for (var j = 0; j < keys.length; j++) {
        var icon = days[keys[j]] && days[keys[j]].icon;
        if (icon) seen[icon] = true;
      }
    }
    return Object.keys(seen).sort();
  }

  /**
   * The subset of features that carry a `days` property.
   *
   * @param {{type: string, features: Array<Object>}|null|undefined} featureCollection
   * @returns {{type: string, features: Array<Object>}} A new
   *   FeatureCollection — the input is never mutated.
   */
  function extractWeatherFeatures(featureCollection) {
    var features = (featureCollection && featureCollection.features) || [];
    return {
      type: 'FeatureCollection',
      features: features.filter(function (feature) {
        return !!(feature.properties && feature.properties.days);
      }),
    };
  }

  /**
   * Concatenate two FeatureCollections' feature arrays into one.
   *
   * @param {{type: string, features: Array<Object>}|null|undefined} a
   * @param {{type: string, features: Array<Object>}|null|undefined} b
   * @returns {{type: string, features: Array<Object>}} A new
   *   FeatureCollection — neither input is mutated.
   */
  function mergeFeatureCollections(a, b) {
    var featuresA = (a && a.features) || [];
    var featuresB = (b && b.features) || [];
    return { type: 'FeatureCollection', features: featuresA.concat(featuresB) };
  }

  window.pwaWeatherCore = Object.freeze({
    formatTempLabel: formatTempLabel,
    projectFeatureForDate: projectFeatureForDate,
    projectFeatureCollectionForDate: projectFeatureCollectionForDate,
    forecastWindowDates: forecastWindowDates,
    isDateInForecastWindow: isDateInForecastWindow,
    iconFilenamesForPayload: iconFilenamesForPayload,
    extractWeatherFeatures: extractWeatherFeatures,
    mergeFeatureCollections: mergeFeatureCollections,
  });
})();
