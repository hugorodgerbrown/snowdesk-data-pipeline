/*
 * static/js/route_drawing_core.js — how a saved route is drawn on a map,
 * shared by every map that draws one (SNOW-1212).
 *
 * The map page drew its routes from layer definitions written inline in
 * `map.js`. The trip page (`trip_map.js`) and the plan page's meeting
 * picker (`trip_meeting_picker.js`) could not reach those, so each drew a
 * route of its own: the trip page in the slope classes SNOW-1017 retired
 * below z14, the picker as one flat line. One track read three ways on
 * three maps.
 *
 * This module is the one drawing. It holds the layer definitions and the
 * zoom the drawing switches at, and the three maps build their layers from
 * it:
 *
 *   - below ROUTE_SLOPE_MINZOOM a route is drawn as its LEGS — climbs
 *     slate and dashed, descents fuchsia and solid, over a casing — with a
 *     numbered marker at each transition, a start dot and a finish flag;
 *   - from ROUTE_SLOPE_MINZOOM a sampled route's core is drawn in the
 *     slope classes instead, and its legs' cores go transparent;
 *   - a route with no drawable legs is drawn flat, in the route colour.
 *
 * The reasoning for each of those is
 * docs/decisions/legs-not-slope-classes-on-the-map.md. The geometry is
 * `route_legs_core.js`'s and the classes are `route_slope_core.js`'s; this
 * module adds only the layers that paint them.
 *
 * ## What stays with the caller
 *
 * `map.js` interleaves layers of its own between these — the route
 * cursor sits over the lines and under the marks. So the definitions
 * come back in two
 * ordered groups, `lineLayers` and `markLayers`, and the caller adds them.
 * The two trip pages have nothing to interleave and call `install`, which
 * adds the sources and both groups in one go.
 *
 * ## Colours
 *
 * Hex literals, because MapLibre paint properties cannot reference a CSS
 * custom property. Each names the token it mirrors in `src/css/main.css`.
 * The leg colours and the slope ramp are read off their own cores at call
 * time, with the same literal fallbacks `map.js` used, so a core that
 * failed to load costs its own layers and nothing else.
 *
 * ## Exports (frozen `self.pwaRouteDrawingCore`)
 *
 *   ROUTE_SLOPE_MINZOOM        — the zoom the core switches to classes
 *   TRANSITION_MINZOOM         — the numbered transitions' minzoom
 *   ENDPOINT_MINZOOM           — the start dot and finish flag's minzoom
 *   ROUTE_LINE_COLOUR          — the flat line (--color-route-line)
 *   ROUTE_CASING_COLOUR        — the casing (--color-route-line-casing)
 *   START_ICON / END_ICON      — the two end markers' image ids
 *   legCoreOpacity(base)       — a leg core's opacity expression
 *   flatFilter()               — the flat line's filter, or null
 *   sourceIds(prefix)          — the five source ids `install` adds
 *   sourceData(collection)     — the five sources' data
 *   lineLayers(options)        — the line layers, bottom to top
 *   markLayers(options)        — the marker layers, bottom to top
 *   ensureMarkerImages(map)    — registers the start dot and finish flag
 *   styleTextFont(style)       — a font stack the style declares
 *   install(map, collection, options) — sources and every layer
 */

// @ts-check

(function () {
  'use strict';

  /**
   * The zoom from which a route's core is painted in the slope classes
   * rather than its leg colours (legs-not-slope-classes-on-the-map.md,
   * 2026-09-30). The leg lines stop here and the class lines start.
   */
  const ROUTE_SLOPE_MINZOOM = 14;

  /**
   * The numbered transitions' minzoom. At a country scale they would pile
   * onto one another and onto the start dot.
   */
  const TRANSITION_MINZOOM = 11;

  /**
   * The start dot and finish flag's minzoom: the zoom at which a route's
   * two ends are far enough apart to read as two markers.
   */
  const ENDPOINT_MINZOOM = 10;

  /** Mirrors `--color-route-line`. */
  const ROUTE_LINE_COLOUR = '#c026d3';

  /** Mirrors `--color-route-line-casing`. */
  const ROUTE_CASING_COLOUR = '#1a1916';

  /** The open SAC tour's halo (SNOW-1354), mirroring `--color-card`. */
  const HALO_COLOUR = '#ffffff';

  /** A transition marker's ring, so it reads over either leg colour. */
  const TRANSITION_RING_COLOUR = '#ffffff';

  /** The map image ids the two end markers are registered under. */
  const START_ICON = 'route-start-dot';
  const END_ICON = 'route-finish-flag';

  /** The label font when a style declares none. */
  const FALLBACK_TEXT_FONT = ['Noto Sans Regular'];

  /**
   * @typedef {{type: string, features: Array<any>}} FeatureCollection
   *
   * @typedef {object} SourceIds
   * @property {string} route The route lines themselves (the flat line).
   * @property {string} legs One line per leg.
   * @property {string} slopes One line per slope-class segment.
   * @property {string} transitions One point per transition.
   * @property {string} endpoints The start and finish points.
   *
   * @typedef {object} LayerOptions
   * @property {string} prefix Every layer id starts with it.
   * @property {SourceIds} sources The sources the layers read.
   * @property {boolean} [visible] False adds the layers hidden.
   * @property {?Array<any>} [casingFilter] The flat casing's filter;
   *   defaults to `flatFilter()`.
   * @property {?Array<any>} [lineFilter] The flat line's filter; defaults
   *   to `flatFilter()`.
   * @property {Array<string>} [textFont] The transition labels' font.
   * @property {{climb: string, descent: string, line?: string}} [colours]
   *   The leg colours, and the flat line's; a route's own when omitted.
   *   An SAC tour not yet saved passes SAC's (SNOW-1310).
   * @property {boolean} [solidClimb] True draws a climb solid, without
   *   its dash: an SAC tour, because a dash on SAC's own maps means a leg
   *   needing extra care (SNOW-1307).
   * @property {boolean} [halo] True adds a slim white halo under the
   *   casings below ROUTE_SLOPE_MINZOOM: the SAC tour a reader has open,
   *   so it stands off the tours network beside it, drawn in the same
   *   blues. The line and casing stay a saved route's (SNOW-1354).
   */

  /**
   * An empty FeatureCollection — `setData` throws on a null.
   *
   * @returns {FeatureCollection}
   */
  function empty() {
    return { type: 'FeatureCollection', features: [] };
  }

  /**
   * The two leg colours, off the legs core with a literal fallback.
   *
   * The literals mirror `--color-route-rail-climb` and
   * `--color-route-rail-descent`; the fallback is only for a page where
   * the core has not loaded, where no leg is drawn and nothing reads them.
   *
   * @param {{climb: string, descent: string, line?: string}} [override] Colours that
   *   win over the route's own (LayerOptions.colours).
   * @returns {{climb: string, descent: string}}
   */
  function legColours(override) {
    if (override) return override;
    const core = self.pwaRouteLegsCore || {};
    return {
      climb: core.LEG_CLIMB_COLOUR || '#64748b',
      descent: core.LEG_DESCENT_COLOUR || '#c026d3',
    };
  }

  /**
   * The opacity of a leg's core line: `base` below ROUTE_SLOPE_MINZOOM,
   * and from there 0 for a leg of a sampled route, whose slope segments
   * take its place, and still `base` for one whose route has none.
   *
   * An opacity rather than a `maxzoom` because the switch is per route: a
   * layer's zoom range cannot vary by feature, and a `maxzoom` hid the
   * core of every route the sampler had not reached yet, leaving only its
   * casing.
   *
   * @param {number} base The opacity.
   * @returns {Array<any>} A zoom `step` expression.
   */
  function legCoreOpacity(base) {
    return [
      'step', ['zoom'], base,
      ROUTE_SLOPE_MINZOOM, ['case', ['==', ['get', 'sampled'], true], 0, base],
    ];
  }

  /**
   * The filter that keeps a legged route off the flat line and casing.
   *
   * A route the leg layers draw must leave both, or the flat line shows
   * through every seam. Null — no filter, every route flat — when the
   * legs core is not loaded: the leg layers then draw nothing, and
   * excluding the legged routes here would leave them drawn by nobody.
   *
   * A function rather than a constant because the legs core may arrive
   * after this module is parsed; the test has to run at install time.
   *
   * @returns {?Array<any>}
   */
  function flatFilter() {
    return self.pwaRouteLegsCore ? ['!', ['has', 'legs']] : null;
  }

  /**
   * The five source ids `install` adds under one prefix.
   *
   * @param {string} prefix
   * @returns {SourceIds}
   */
  function sourceIds(prefix) {
    return {
      route: prefix,
      legs: prefix + '-legs',
      slopes: prefix + '-slopes',
      transitions: prefix + '-transitions',
      endpoints: prefix + '-endpoints',
    };
  }

  /**
   * The data for each of the five sources, from one routes collection.
   *
   * The `route` entry is `withDrawableLegs`'s copy, so `flatFilter`'s
   * `has legs` test is true only of a route the leg layers actually draw.
   * Every other entry is an empty collection when its core is missing.
   *
   * @param {?FeatureCollection} collection The routes.
   * @returns {{route: FeatureCollection, legs: FeatureCollection,
   *   slopes: FeatureCollection, transitions: FeatureCollection,
   *   endpoints: FeatureCollection}}
   */
  function sourceData(collection) {
    const fc = collection || empty();
    const legs = self.pwaRouteLegsCore;
    const markers = self.pwaRouteMarkersCore;
    return {
      route: legs && legs.withDrawableLegs
        ? /** @type {FeatureCollection} */ (legs.withDrawableLegs(fc))
        : fc,
      legs: legs ? legs.legCollection(fc) : empty(),
      slopes: legs && legs.slopeSegmentCollection ? legs.slopeSegmentCollection(fc) : empty(),
      transitions: legs ? legs.transitionCollection(fc) : empty(),
      endpoints: markers ? markers.endpointsGeojson(fc) : empty(),
    };
  }

  /**
   * Merge a layout with the visibility the caller asked for.
   *
   * @param {LayerOptions} options
   * @param {Object<string, any>} layout
   * @returns {Object<string, any>}
   */
  function layoutFor(options, layout) {
    return Object.assign({ visibility: options.visible === false ? 'none' : 'visible' }, layout);
  }

  /**
   * Add a filter to a layer definition, unless it is null.
   *
   * @param {Object<string, any>} layer
   * @param {?Array<any>|undefined} filter
   * @returns {Object<string, any>}
   */
  function filtered(layer, filter) {
    if (filter) layer.filter = filter;
    return layer;
  }

  /**
   * The route's line layers, in paint order, bottom first.
   *
   * The flat casing and line (a route with no drawable legs), the legs'
   * own casing, the climb and descent cores, then the slope-class lines
   * from ROUTE_SLOPE_MINZOOM. The legs have a casing of their own,
   * narrower than the flat one's, because a leg is one of several on a
   * route and the dash on a climb needs gaps a reader can see at z12.
   *
   * TWO leg layers because a climb is DASHED and a descent solid, and
   * `line-dasharray` is not a data-driven property in MapLibre. The dash
   * carries the direction for a reader who cannot tell the two colours
   * apart. Butt caps on the dash, because round caps close the gaps.
   *
   * @param {LayerOptions} options
   * @returns {Array<Object<string, any>>}
   */
  function lineLayers(options) {
    const p = options.prefix;
    const s = options.sources;
    const flat = flatFilter();
    const colours = legColours(options.colours);
    const slopeCore = self.pwaRouteSlopeCore;
    const halo = options.halo === true;
    const legWidth = ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 3.2, 16, 5.5];
    const legCasingWidth = ['interpolate', ['linear'], ['zoom'], 6, 3, 12, 5.5, 16, 9];
    const casingOpacity = 0.55;
    // SNOW-1354: a slim white halo under each casing, 1.25 px clear of it
    // either side, and only below ROUTE_SLOPE_MINZOOM, where the open tour
    // shares the map with the tours network in the same blues. The line
    // and casing are a saved route's own, so the two read alike; from z14
    // the slope classes set the line apart and the halo goes (Hugo,
    // 2026-11-01: the first, bolder halo was too much).
    /**
     * @param {string} id
     * @param {string} source
     * @param {?Array<any>|undefined} filter
     * @param {Array<any>} width
     * @returns {Object<string, any>}
     */
    const haloLayer = (id, source, filter, width) => filtered({
      id: id,
      type: 'line',
      source: source,
      maxzoom: ROUTE_SLOPE_MINZOOM,
      layout: layoutFor(options, { 'line-cap': 'round', 'line-join': 'round' }),
      paint: {
        'line-color': HALO_COLOUR,
        'line-opacity': 0.9,
        'line-width': width,
      },
    }, filter);
    const layers = [
      filtered({
        id: p + '-line-casing',
        type: 'line',
        source: s.route,
        layout: layoutFor(options, { 'line-cap': 'round', 'line-join': 'round' }),
        paint: {
          'line-color': ROUTE_CASING_COLOUR,
          'line-opacity': casingOpacity,
          'line-width': ['interpolate', ['linear'], ['zoom'], 6, 3, 12, 7, 16, 11],
        },
      }, options.casingFilter === undefined ? flat : options.casingFilter),
      filtered({
        id: p + '-line',
        type: 'line',
        source: s.route,
        layout: layoutFor(options, { 'line-cap': 'round', 'line-join': 'round' }),
        paint: {
          'line-color': (options.colours && options.colours.line) || ROUTE_LINE_COLOUR,
          'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 4, 16, 7],
        },
      }, options.lineFilter === undefined ? flat : options.lineFilter),
      {
        id: p + '-leg-casing',
        type: 'line',
        source: s.legs,
        layout: layoutFor(options, { 'line-cap': 'round', 'line-join': 'round' }),
        paint: {
          'line-color': ROUTE_CASING_COLOUR,
          'line-opacity': casingOpacity,
          'line-width': legCasingWidth,
        },
      },
      {
        id: p + '-leg-climb',
        type: 'line',
        source: s.legs,
        filter: ['==', ['get', 'climbing'], true],
        layout: layoutFor(options, {
          'line-cap': options.solidClimb ? 'round' : 'butt',
          'line-join': 'round',
        }),
        paint: Object.assign(
          {
            'line-color': colours.climb,
            'line-opacity': legCoreOpacity(1),
            'line-width': legWidth,
          },
          // In line-widths, so the dash keeps its proportions as the line
          // thickens with zoom.
          options.solidClimb ? {} : { 'line-dasharray': [2, 1.5] },
        ),
      },
      {
        id: p + '-leg-descent',
        type: 'line',
        source: s.legs,
        filter: ['!=', ['get', 'climbing'], true],
        layout: layoutFor(options, { 'line-cap': 'round', 'line-join': 'round' }),
        paint: {
          'line-color': colours.descent,
          'line-opacity': legCoreOpacity(1),
          'line-width': legWidth,
        },
      },
    ];
    if (halo) {
      // Under the flat casing (index 0) and the leg casing (index 2).
      const flatCasingFilter = options.casingFilter === undefined ? flat : options.casingFilter;
      layers.splice(2, 0, haloLayer(
        p + '-leg-halo', s.legs, null,
        ['interpolate', ['linear'], ['zoom'], 6, 5.5, 12, 8, 16, 11.5],
      ));
      layers.unshift(haloLayer(
        p + '-line-halo', s.route, flatCasingFilter,
        ['interpolate', ['linear'], ['zoom'], 6, 5.5, 12, 9.5, 16, 13.5],
      ));
    }
    // A segment the terrain had no answer for is its own layer in the
    // unknown grey: `step` needs a number, and falling on the ramp's first
    // stop would paint unsurveyed ground as gentle.
    for (const unknown of [false, true]) {
      layers.push({
        id: p + (unknown ? '-slope-unknown' : '-slope-line'),
        type: 'line',
        source: s.slopes,
        minzoom: ROUTE_SLOPE_MINZOOM,
        filter: unknown ? ['==', ['get', 'unknown'], true] : ['!=', ['get', 'unknown'], true],
        layout: layoutFor(options, { 'line-cap': 'butt', 'line-join': 'round' }),
        paint: {
          'line-color': unknown
            ? ((slopeCore && slopeCore.UNKNOWN_COLOUR) || ROUTE_LINE_COLOUR)
            : (slopeCore ? slopeCore.colourExpression() : ROUTE_LINE_COLOUR),
          'line-opacity': 1,
          'line-width': ['interpolate', ['linear'], ['zoom'], 12, 3.2, 16, 5.5],
        },
      });
    }
    return layers;
  }

  /**
   * The route's marker layers, in paint order, bottom first.
   *
   * A circle at each transition in the colour of the leg it OPENS, its
   * number as a symbol over it, then the start dot and finish flag. The
   * circle carries the position on its own; the number needs glyphs, and
   * a style that fails to load them leaves it out without taking the
   * marker with it. Every marker allows overlap: a dropped transition
   * would renumber the route in the reader's head, and a dropped end
   * would leave a short track with one end.
   *
   * @param {LayerOptions} options
   * @returns {Array<Object<string, any>>}
   */
  function markLayers(options) {
    const p = options.prefix;
    const s = options.sources;
    const colours = legColours(options.colours);
    return [
      {
        id: p + '-transitions',
        type: 'circle',
        source: s.transitions,
        minzoom: TRANSITION_MINZOOM,
        layout: layoutFor(options, {}),
        paint: {
          'circle-color': ['case', ['get', 'climbing'], colours.climb, colours.descent],
          'circle-radius': 7,
          'circle-stroke-color': TRANSITION_RING_COLOUR,
          'circle-stroke-width': 1.5,
        },
      },
      {
        id: p + '-transition-labels',
        type: 'symbol',
        source: s.transitions,
        minzoom: TRANSITION_MINZOOM,
        layout: layoutFor(options, {
          'text-field': ['to-string', ['get', 'n']],
          'text-font': options.textFont || FALLBACK_TEXT_FONT,
          'text-size': 10,
          'text-allow-overlap': true,
          'text-ignore-placement': true,
        }),
        paint: {
          'text-color': TRANSITION_RING_COLOUR,
        },
      },
      {
        id: p + '-endpoints',
        type: 'symbol',
        source: s.endpoints,
        minzoom: ENDPOINT_MINZOOM,
        layout: layoutFor(options, {
          'icon-image': ['case', ['==', ['get', 'role'], 'start'], START_ICON, END_ICON],
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          // The dot is centred on its point; the flag hangs off a pole
          // whose foot is the point.
          'icon-anchor': ['case', ['==', ['get', 'role'], 'start'], 'center', 'bottom-left'],
        }),
      },
    ];
  }

  /**
   * A `#rrggbb` colour as its three channels.
   *
   * @param {string} hex
   * @returns {[number, number, number]}
   */
  function channels(hex) {
    const value = parseInt(hex.slice(1), 16);
    return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
  }

  /**
   * Register the start dot and finish flag, unless the style holds them.
   *
   * `setStyle` drops every registered image with every layer, so this is
   * called on each install, and `hasImage` makes the repeat call free. The
   * core's own PIXEL_RATIO goes with each image: the icon is sized in
   * device pixels.
   *
   * @param {any} map A MapLibre map.
   * @returns {void}
   */
  function ensureMarkerImages(map) {
    const core = self.pwaRouteMarkersCore;
    if (!core) return;
    const ratio = { pixelRatio: core.PIXEL_RATIO };
    if (!map.hasImage(START_ICON)) {
      map.addImage(START_ICON, core.startDotPixels(...channels(ROUTE_LINE_COLOUR)), ratio);
    }
    if (!map.hasImage(END_ICON)) {
      map.addImage(END_ICON, core.finishFlagPixels(...channels(ROUTE_CASING_COLOUR)), ratio);
    }
  }

  /**
   * Whether a `text-font` value is a literal stack rather than an
   * expression.
   *
   * Both are arrays of strings — `['get', 'font']` is one too — so an
   * array headed by an operator name (lowercase, no spaces, as every
   * MapLibre operator is and no font name is) is refused.
   *
   * @param {*} font
   * @returns {boolean}
   */
  function isFontStack(font) {
    return Array.isArray(font)
      && font.length > 0
      && font.every((v) => typeof v === 'string')
      && !/^[a-z-]+$/.test(font[0]);
  }

  /**
   * A literal font stack the style's own symbol layers use.
   *
   * The transition numbers are a symbol, and a symbol's font has to be
   * one the basemap's glyph server serves. The first literal stack a
   * symbol layer names is enough for a page with one route on it; `map.js`
   * picks the most-used one, for the reasons it gives.
   *
   * @param {?{layers?: Array<any>}} style A MapLibre style.
   * @returns {Array<string>}
   */
  function styleTextFont(style) {
    const layers = (style && style.layers) || [];
    for (const layer of layers) {
      const font = layer && layer.type === 'symbol' && layer.layout && layer.layout['text-font'];
      if (isFontStack(font)) return font;
    }
    return FALLBACK_TEXT_FONT;
  }

  /**
   * Add a route's five sources and every layer, for a page that has
   * nothing to interleave with them.
   *
   * Idempotent, guarded on the route source, so a style reload cannot
   * duplicate layers.
   *
   * @param {any} map A MapLibre map.
   * @param {?FeatureCollection} collection The routes to draw.
   * @param {{prefix: string, textFont?: Array<string>, colours?: {climb: string, descent: string, line?: string}, solidClimb?: boolean, halo?: boolean}} options
   *   `textFont` is the transition labels' font; the style's own when
   *   omitted. `colours` and `solidClimb` as LayerOptions.
   * @returns {SourceIds}
   */
  function install(map, collection, options) {
    const sources = sourceIds(options.prefix);
    if (map.getSource(sources.route)) return sources;
    ensureMarkerImages(map);
    const data = sourceData(collection);
    for (const key of /** @type {Array<keyof SourceIds>} */ (Object.keys(sources))) {
      map.addSource(sources[key], { type: 'geojson', data: data[key] });
    }
    const layerOptions = {
      prefix: options.prefix,
      sources: sources,
      textFont: options.textFont || styleTextFont(map.getStyle ? map.getStyle() : null),
      colours: options.colours,
      solidClimb: options.solidClimb,
      halo: options.halo,
    };
    for (const layer of lineLayers(layerOptions).concat(markLayers(layerOptions))) {
      map.addLayer(layer);
    }
    return sources;
  }

  self.pwaRouteDrawingCore = Object.freeze({
    ROUTE_SLOPE_MINZOOM: ROUTE_SLOPE_MINZOOM,
    TRANSITION_MINZOOM: TRANSITION_MINZOOM,
    ENDPOINT_MINZOOM: ENDPOINT_MINZOOM,
    ROUTE_LINE_COLOUR: ROUTE_LINE_COLOUR,
    ROUTE_CASING_COLOUR: ROUTE_CASING_COLOUR,
    START_ICON: START_ICON,
    END_ICON: END_ICON,
    legCoreOpacity: legCoreOpacity,
    flatFilter: flatFilter,
    sourceIds: sourceIds,
    sourceData: sourceData,
    lineLayers: lineLayers,
    markLayers: markLayers,
    ensureMarkerImages: ensureMarkerImages,
    styleTextFont: styleTextFont,
    install: install,
  });
})();
