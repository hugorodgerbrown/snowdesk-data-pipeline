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
 *      weather code and the day's max temperature and nothing else. TWO
 *      tables below mirror the server: the code → bucket map mirrors
 *      `_WMO_CODE_TO_ICON_BUCKET` and `DAY_NIGHT_BUCKETS` mirrors
 *      `WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT`, both in
 *      `apps/weather/services/weather_display.py`. Both are held to their
 *      Python counterpart by
 *      `tests/weather/services/test_icon_table_parity.py`, which parses
 *      this file. A mirror with no guard is drift waiting to happen.
 *
 *      Where the set draws a day/night pair the map always takes the DAY
 *      variant. A map symbol summarises a whole calendar day — a daily
 *      maximum temperature and a daily condition — so switching every
 *      station on the map to night icons because the reader happens to be
 *      up late says something about the reader, not about the day. The
 *      server-rendered panels, which show one place at a time, still make
 *      the day/night call (`is_day`).
 *
 *   2. THE LABEL IS THE SYMBOL, CAPTIONED. The WMO condition icon leads and
 *      the temperature sits under it; the station's ground elevation is
 *      deliberately absent. It was there — a symbol on a mountain map does
 *      mean less without the height it was read at — but every marker
 *      carrying one made the map unreadable at any density, and elevation
 *      earns its place on a resort page showing three points at three
 *      heights rather than here. (It is `Location.elevation_m`, not the
 *      freezing level, which has never been on the map.) Elevation still
 *      drives the cluster collapse below.
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
 *     WMO weather interpretation code → weather SVG basename, e.g.
 *     `'light_snow.svg'`. Unknown codes fall back to `'cloudy.svg'`,
 *     matching the server's own neutral default.
 *   formatElevation(elevationM)
 *     The station's ground elevation as the label's second line.
 *
 *   formatElevationBreak(elevationM) / elevationMarkFor(elevationM)
 *     The line break before it, and the id of the mark that says the
 *     metre value is a height — both empty when there is no elevation.
 *     Separate properties because a `format` section cannot be inserted
 *     into the middle of another one; see formatElevationBreak.
 *
 *   formatTemp(tmax)
 *     The day's max temperature → the label's caption, e.g. `'4°'`.
 *     Returns `''` when there is no finite reading, so MapLibre draws the
 *     symbol alone rather than a stray character.
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

  // The buckets shipping a day/night PAIR rather than a single file — the
  // two the icon set draws a sun or a moon in. Mirrors
  // WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT in
  // apps/weather/services/weather_display.py; parsed by
  // tests/weather/services/test_icon_table_parity.py, so keep the literal
  // shape (one quoted bucket per line) or that guard stops reading it.
  var DAY_NIGHT_BUCKETS = [
    'clear',
    'partly_cloudy',
  ];
  var DEFAULT_BUCKET = 'cloudy';

  // The registered MapLibre image id of the elevation mark. Declared here
  // rather than in map.js because the projection below writes it into every
  // feature and the layer reads it back — the two have to agree on the
  // string, and this module is the one both of them already import.
  var ELEVATION_MARK_ID = 'weather-elevation-mark';

  /**
   * WMO weather interpretation code → weather SVG basename.
   *
   * Only `DAY_NIGHT_BUCKETS` take the `-day` suffix; every other bucket
   * ships one file whatever the light, so its filename is the bare bucket.
   *
   * @param {number|null|undefined} code
   * @returns {string} e.g. `'light_snow.svg'`, `'clear-day.svg'`;
   *   `'cloudy.svg'` for an unrecognised or missing code.
   */
  function iconForCode(code) {
    var bucket = WMO_ICON_BUCKET[code];
    if (!bucket) bucket = DEFAULT_BUCKET;
    if (DAY_NIGHT_BUCKETS.indexOf(bucket) !== -1) return bucket + '-day.svg';
    return bucket + '.svg';
  }

  /**
   * The temperature alone, as its own label line.
   *
   * Its own field rather than part of a combined string, so the layer can
   * hold it down with `font-scale` while the inline symbol grows with
   * `text-size`. That is how the icon leads without a bold face — one is
   * not guaranteed by whatever glyph endpoint the active basemap uses.
   *
   * @param {number|null|undefined} tmax Day's max temperature, °C.
   * @returns {string} e.g. `'21°'`, or `''` when there is no reading.
   */
  function formatTemp(tmax) {
    if (typeof tmax !== 'number' || !Number.isFinite(tmax)) return '';
    return Math.round(tmax) + '°';
  }

  /**
   * The station's ground elevation, as the label's second line.
   *
   * NO LEADING NEWLINE — see `formatElevationBreak`, which owns it. The
   * break used to live in this string, until the elevation gained a mark
   * that has to sit BETWEEN the break and the value; a section cannot be
   * inserted into the middle of another section's text.
   *
   * @param {number|null|undefined} elevationM Station elevation, metres.
   * @returns {string} e.g. `'3328 m'`, or `''` when unresolved.
   */
  function formatElevation(elevationM) {
    if (typeof elevationM !== 'number' || !Number.isFinite(elevationM)) return '';
    return Math.round(elevationM) + ' m';
  }

  /**
   * The line break before the elevation — its own label property.
   *
   * A `format` expression is fixed at style time: there is no way to add or
   * drop a section per feature, so every section renders for every symbol
   * and the only lever is what each one CONTAINS. A station with no
   * resolved elevation therefore has to contribute an empty break, an
   * empty mark and an empty value — three empty strings, which render as
   * nothing — rather than three sections the layer could omit. Put the
   * break in the value instead and the empty case leaves a blank second
   * line, which shifts the whole label off the point it is labelling.
   *
   * @param {number|null|undefined} elevationM Station elevation, metres.
   * @returns {string} `'\n'`, or `''` when there is no elevation to break for.
   */
  function formatElevationBreak(elevationM) {
    return formatElevation(elevationM) === '' ? '' : '\n';
  }

  /**
   * The mark that says the metre value is a HEIGHT, not a distance.
   *
   * Returns an image id for the layer's inline `image` section, or `''`
   * when there is no elevation — an empty id draws nothing, the same
   * mechanism `icon` uses for a day with no reading.
   *
   * @param {number|null|undefined} elevationM Station elevation, metres.
   * @returns {string} The registered image id, or `''`.
   */
  function elevationMarkFor(elevationM) {
    return formatElevation(elevationM) === '' ? '' : ELEVATION_MARK_ID;
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
      label_temp: day ? formatTemp(day.tmax) : '',
      label_break: day ? formatElevationBreak(properties.elevation_m) : '',
      elev_mark: day ? elevationMarkFor(properties.elevation_m) : '',
      label_elev: day ? formatElevation(properties.elevation_m) : '',
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
    formatTemp: formatTemp,
    formatElevation: formatElevation,
    formatElevationBreak: formatElevationBreak,
    elevationMarkFor: elevationMarkFor,
    ELEVATION_MARK_ID: ELEVATION_MARK_ID,
    projectFeatureForDate: projectFeatureForDate,
    projectFeatureCollectionForDate: projectFeatureCollectionForDate,
    forecastWindowDates: forecastWindowDates,
    isDateInForecastWindow: isDateInForecastWindow,
    iconFilenamesForPayload: iconFilenamesForPayload,
    collapseToLowest: collapseToLowest,
  });
})();
