/*
 * static/js/map_weather_core.js — pure helpers for the map Weather overlay
 * (SNOW-761).
 *
 * `installWeatherLayer` in map.js needs a real MapLibre `map`, so it isn't
 * unit-tested directly — but nothing it does on a scrubber date change has
 * anything to do with MapLibre. It is a pure transform from the days-keyed
 * GeoJSON payload (`/api/weather.geojson`) onto the flat
 * `icon`/`label` feature properties a symbol layer's data-driven
 * expressions (`['get', 'icon']`, `['get', 'label']`) can read. That lives
 * here so it is covered by `tests/js/test_map_weather_core.js`, following
 * the same split as `choropleth_core.js` / `scrubber_core.js`.
 *
 * Because the payload carries the WHOLE forecast window, map.js never
 * re-fetches on `snowdesk:date-changed` — it re-projects the already-fetched
 * FeatureCollection in memory and calls `source.setData()`.
 *
 * Three things this module owns that the old one did not:
 *
 *   1. THE ICON IS DERIVED HERE, NOT SERVED. The feed carries the WMO
 *      weather code and the day's max temperature and nothing else. The
 *      code → bucket table below mirrors `_WMO_CODE_TO_ICON_BUCKET` in
 *      `apps/weather/services/weather_display.py`, and the two are held
 *      together by `tests/weather/services/test_icon_table_parity.py`,
 *      which parses this file. A mirror with no guard is drift waiting to
 *      happen.
 *
 *      The map always draws the DAY variant. A map symbol summarises a
 *      whole calendar day — a daily maximum temperature and a daily
 *      condition — so switching every station on the map to night icons
 *      because the reader happens to be up late says something about the
 *      reader, not about the day. The server-rendered panels, which show
 *      one place at a time, still make the day/night call (`is_day`).
 *
 *   2. THE LABEL CARRIES THE ALTITUDE. A weather symbol on a mountain map
 *      is meaningless without the height it was read at: 2° at 1500 m and
 *      2° at 3000 m are different weeks. `formatLabel` puts the elevation
 *      under the temperature. A location with no resolved elevation falls
 *      back to the temperature alone rather than printing a gap.
 *
 *   3. CLUSTERING COLLAPSES TO THE LOWEST STATION, and it is a pure
 *      transform here rather than MapLibre `clusterProperties`. MapLibre's
 *      cluster accumulators do `min`/`max` on numbers but have no argmin —
 *      "the icon belonging to the lowest member" is not expressible as a
 *      cluster property at all. Lowest, not nearest-to-centre, because the
 *      valley station is the one a reader recognises and the one whose
 *      freezing level actually decides whether it is raining where they
 *      parked.
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
 *   iconForCode(code)
 *     WMO weather interpretation code → Meteocons SVG basename, e.g.
 *     `'light_snow-day.svg'`. Unknown codes fall back to `'cloudy.svg'`,
 *     matching the server's own neutral default.
 *   formatLabel(tmax, elevationM)
 *     The day's max temperature and the station's altitude → the symbol
 *     layer's text-field, e.g. `'4°\n3328 m'`. Returns `''` when neither
 *     is a finite number, so MapLibre draws no label rather than a stray
 *     character.
 *   projectFeatureForDate(feature, dateKey)
 *   projectFeatureCollectionForDate(featureCollection, dateKey)
 *     Project the `days` dict onto flat `icon`/`label` properties for one
 *     date. A feature with no entry for `dateKey` projects to `icon: ''`,
 *     which the layer's own filter drops entirely.
 *   forecastWindowDates(featureCollection)
 *   isDateInForecastWindow(dateKey, featureCollection)
 *     The payload's date coverage, and membership in it.
 *   iconFilenamesForPayload(featureCollection)
 *     The exact set of icon filenames the payload needs rasterised, so a
 *     re-register after a basemap `setStyle` never decodes an icon nothing
 *     draws.
 *   collapseToLowest(featureCollection, radiusDegrees)
 *     Cluster overlapping stations and keep the LOWEST of each — see (3).
 */

(function () {
  'use strict';

  // Mirrors _WMO_CODE_TO_ICON_BUCKET in
  // apps/weather/services/weather_display.py. Parsed by
  // tests/weather/services/test_icon_table_parity.py — keep the literal
  // shape (one `code: 'bucket',` pair per line) or that guard stops
  // reading it.
  var WMO_ICON_BUCKET = {
    0: 'clear',
    1: 'partly_cloudy',
    2: 'partly_cloudy',
    3: 'cloudy',
    45: 'fog',
    48: 'fog',
    51: 'drizzle',
    53: 'drizzle',
    55: 'drizzle',
    56: 'drizzle',
    57: 'drizzle',
    61: 'light_rain',
    63: 'moderate_rain',
    65: 'heavy_rain',
    66: 'light_rain',
    67: 'heavy_rain',
    71: 'light_snow',
    73: 'moderate_snow',
    75: 'heavy_snow',
    77: 'light_snow',
    80: 'light_rain',
    81: 'moderate_rain',
    82: 'heavy_rain',
    85: 'light_snow',
    86: 'heavy_snow',
    95: 'thunder',
    96: 'thunder',
    99: 'thunder',
  };

  // The one bucket shipping a single file rather than a day/night pair.
  var NO_DAY_NIGHT_BUCKET = 'cloudy';
  var DEFAULT_BUCKET = 'cloudy';

  /**
   * WMO weather interpretation code → Meteocons SVG basename.
   *
   * @param {number|null|undefined} code
   * @returns {string} e.g. `'light_snow-day.svg'`; `'cloudy.svg'` for an
   *   unrecognised or missing code.
   */
  function iconForCode(code) {
    var bucket = WMO_ICON_BUCKET[code];
    if (!bucket) bucket = DEFAULT_BUCKET;
    if (bucket === NO_DAY_NIGHT_BUCKET) return bucket + '.svg';
    return bucket + '-day.svg';
  }

  /**
   * The day's max temperature and the station's altitude → symbol label.
   *
   * Two lines: the temperature, then the elevation. Either half is omitted
   * when it is not a finite number, and the result is `''` when both are —
   * MapLibre renders an empty label rather than a stray character.
   *
   * @param {number|null|undefined} tmax Daily max temperature, °C.
   * @param {number|null|undefined} elevationM Station elevation, metres.
   * @returns {string}
   */
  function formatLabel(tmax, elevationM) {
    var lines = [];
    if (typeof tmax === 'number' && Number.isFinite(tmax)) {
      lines.push(Math.round(tmax) + '°');
    }
    if (typeof elevationM === 'number' && Number.isFinite(elevationM)) {
      lines.push(Math.round(elevationM) + ' m');
    }
    return lines.join('\n');
  }

  /**
   * Project one Feature's `days[dateKey]` entry onto flat properties.
   *
   * @param {{type: string, geometry: Object, properties: Object}} feature
   * @param {string} dateKey ISO date, e.g. `'2026-08-30'`.
   * @returns {{type: string, geometry: Object, properties: Object}} A new
   *   Feature — the input is never mutated.
   */
  function projectFeatureForDate(feature, dateKey) {
    var properties = (feature && feature.properties) || {};
    var days = properties.days || {};
    var day = days[dateKey] || null;
    var newProperties = Object.assign({}, properties, {
      icon: day ? iconForCode(day.code) : '',
      label: day ? formatLabel(day.tmax, properties.elevation_m) : '',
    });
    return Object.assign({}, feature, { properties: newProperties });
  }

  /**
   * Project every Feature in a FeatureCollection for one date.
   *
   * @param {{type: string, features: Array<Object>}|null|undefined} featureCollection
   * @param {string} dateKey ISO date, e.g. `'2026-08-30'`.
   * @returns {{type: string, features: Array<Object>}} A new
   *   FeatureCollection — the input is never mutated.
   */
  function projectFeatureCollectionForDate(featureCollection, dateKey) {
    var features = (featureCollection && featureCollection.features) || [];
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
   * @param {string} dateKey ISO date, e.g. `'2026-08-30'`.
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
        var day = days[keys[j]];
        if (day) seen[iconForCode(day.code)] = true;
      }
    }
    return Object.keys(seen).sort();
  }

  /**
   * Read a feature's elevation for the "which is lowest" comparison.
   *
   * A station with no resolved elevation sorts as infinitely high, so it
   * only ever wins a cluster it is alone in. That is deliberate: an
   * unresolved elevation is missing data, and letting it beat a station
   * whose height we actually know would collapse the cluster onto the one
   * pin that cannot label itself.
   *
   * @param {Object} feature
   * @returns {number}
   */
  function elevationOf(feature) {
    var value = feature && feature.properties && feature.properties.elevation_m;
    return typeof value === 'number' && Number.isFinite(value)
      ? value
      : Infinity;
  }

  /**
   * Collapse overlapping stations to the lowest one in each cluster.
   *
   * Single-linkage clustering on a square grid of side `radiusDegrees`,
   * walked in input order: each feature joins the first existing cluster
   * whose representative is within the radius on both axes, or starts one.
   * That is O(n·clusters) rather than O(n²) and, more importantly, it is
   * DETERMINISTIC — the same payload always collapses the same way, so the
   * map does not reshuffle its pins between two identical renders.
   *
   * Degrees, not metres: the caller derives the radius from the current
   * zoom, where a degree of longitude is the unit the viewport is already
   * expressed in. At Alpine latitudes a degree of longitude is about 0.7 of
   * a degree of latitude, so a square in degrees is a slightly wide
   * rectangle on the ground — which does not matter for a legibility
   * heuristic and costs a trigonometric call per comparison to fix.
   *
   * A non-positive radius disables clustering entirely and returns every
   * feature, which is what the caller passes above the zoom where symbols
   * stop colliding.
   *
   * @param {{type: string, features: Array<Object>}|null|undefined} featureCollection
   * @param {number} radiusDegrees Cluster half-width, in degrees.
   * @returns {{type: string, features: Array<Object>}} A new
   *   FeatureCollection carrying one feature per cluster — the lowest
   *   member. Inputs are never mutated.
   */
  function collapseToLowest(featureCollection, radiusDegrees) {
    var features = (featureCollection && featureCollection.features) || [];
    if (!(radiusDegrees > 0)) {
      return { type: 'FeatureCollection', features: features.slice() };
    }
    var winners = [];
    for (var i = 0; i < features.length; i++) {
      var feature = features[i];
      var coordinates =
        (feature && feature.geometry && feature.geometry.coordinates) || null;
      if (!coordinates) continue;
      var lon = coordinates[0];
      var lat = coordinates[1];
      var joined = false;
      for (var j = 0; j < winners.length; j++) {
        var winner = winners[j];
        if (
          Math.abs(lon - winner.lon) <= radiusDegrees &&
          Math.abs(lat - winner.lat) <= radiusDegrees
        ) {
          if (elevationOf(feature) < elevationOf(winner.feature)) {
            // The anchor stays where the cluster was first seeded; only the
            // feature it stands for changes. Moving the anchor to each new
            // winner would let a cluster drift across the map as it grows.
            winner.feature = feature;
          }
          joined = true;
          break;
        }
      }
      if (!joined) {
        winners.push({ lon: lon, lat: lat, feature: feature });
      }
    }
    return {
      type: 'FeatureCollection',
      features: winners.map(function (winner) {
        return winner.feature;
      }),
    };
  }

  window.pwaWeatherCore = Object.freeze({
    iconForCode: iconForCode,
    formatLabel: formatLabel,
    projectFeatureForDate: projectFeatureForDate,
    projectFeatureCollectionForDate: projectFeatureCollectionForDate,
    forecastWindowDates: forecastWindowDates,
    isDateInForecastWindow: isDateInForecastWindow,
    iconFilenamesForPayload: iconFilenamesForPayload,
    collapseToLowest: collapseToLowest,
  });
})();
