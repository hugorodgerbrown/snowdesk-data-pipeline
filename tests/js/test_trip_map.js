/*
 * tests/js/test_trip_map.js — the trip page's map payload arithmetic
 * (SNOW-820).
 *
 * `trip_map.js` builds one camera and two sources from an inline payload.
 * Everything worth asserting about that is a pure function of the payload
 * — the bbox reshape, what counts as a drawable line, what counts as a
 * placeable marker, and where the profile's point lands on the line
 * (SNOW-1216). None of it needs a browser, so none of it belongs in
 * `tests/e2e/`.
 *
 * The two `_core` modules are imported first because `trip_map.js` reads
 * them off `self` at its own top level, exactly as the page loads them.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_markers_core.js';
import '../../static/js/slope_classes_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/route_legs_core.js';
import '../../static/js/route_drawing_core.js';
import '../../static/js/route_cursor_map_core.js';
import '../../static/js/trip_map.js';

const core = self.pwaTripMapCore;

/** A payload matching what apps.trips.views._trip_map_payload emits. */
function payload(overrides) {
  return Object.assign(
    {
      route: {
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: [
            [7.4, 46.1, 1500],
            [7.41, 46.11, 1550],
            [7.42, 46.12, 1600],
          ],
        },
        properties: { distance_m: 2500, ascent_m: 100, descent_m: 0 },
      },
      meeting: {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [7.4, 46.1] },
        properties: {},
      },
      bounds: [7.4, 46.1, 7.42, 46.12],
    },
    overrides || {}
  );
}

describe('fitBoundsFor', () => {
  it('reshapes a flat GeoJSON bbox into the nested pair fitBounds takes', () => {
    // The one transform between how a snapshot is stored and how MapLibre
    // is fitted. Getting it wrong frames the wrong half of the world.
    expect(core.fitBoundsFor([7.4, 46.1, 7.42, 46.12])).toEqual([
      [7.4, 46.1],
      [7.42, 46.12],
    ]);
  });

  it('returns null for a malformed bbox rather than throwing', () => {
    // A malformed snapshot must leave the map at its default frame, not
    // take the whole module down inside the constructor.
    expect(core.fitBoundsFor(null)).toBeNull();
    expect(core.fitBoundsFor([7.4, 46.1])).toBeNull();
    expect(core.fitBoundsFor([7.4, 46.1, 'e', 46.12])).toBeNull();
    expect(core.fitBoundsFor([7.4, 46.1, NaN, 46.12])).toBeNull();
  });
});

describe('routeSourceData', () => {
  it('wraps the route feature in a FeatureCollection', () => {
    const data = core.routeSourceData(payload());

    expect(data.type).toBe('FeatureCollection');
    expect(data.features).toHaveLength(1);
    expect(data.features[0].geometry.coordinates).toHaveLength(3);
  });

  it('is empty for a track with fewer than two points', () => {
    // A one-point "line" is not a line. An empty collection draws nothing;
    // a malformed one is a MapLibre error.
    const data = core.routeSourceData(
      payload({
        route: {
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: [[7.4, 46.1, 1500]] },
          properties: {},
        },
      })
    );

    expect(data.features).toHaveLength(0);
  });

  it('is empty for a payload with no route at all', () => {
    expect(core.routeSourceData(null).features).toHaveLength(0);
    expect(core.routeSourceData({}).features).toHaveLength(0);
  });
});

describe('meetingSourceData', () => {
  it('places the marker at the payload coordinate, longitude first', () => {
    // The axis order the server writes and MapLibre reads. A swap here
    // would put a Swiss meeting point in the Indian Ocean.
    const data = core.meetingSourceData(payload());

    expect(data.features[0].geometry.coordinates).toEqual([7.4, 46.1]);
  });

  it('is empty when the coordinate is missing or not numeric', () => {
    expect(
      core.meetingSourceData(
        payload({
          meeting: {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [] },
            properties: {},
          },
        })
      ).features
    ).toHaveLength(0);

    expect(
      core.meetingSourceData(
        payload({
          meeting: {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: ['7.4', '46.1'] },
            properties: {},
          },
        })
      ).features
    ).toHaveLength(0);
  });
});

describe('readPayload', () => {
  it('parses the inline json_script element', () => {
    const el = document.createElement('script');
    el.type = 'application/json';
    el.id = 'trip-map-payload';
    el.textContent = JSON.stringify({ bounds: [1, 2, 3, 4] });
    document.body.appendChild(el);

    expect(core.readPayload(document).bounds).toEqual([1, 2, 3, 4]);

    document.body.removeChild(el);
  });

  it('returns null when the element is absent or unparseable', () => {
    expect(core.readPayload(document)).toBeNull();

    const el = document.createElement('script');
    // `type` matters: jsdom EXECUTES a bare <script>, so a typeless one
    // holding malformed JSON throws a SyntaxError out of the DOM rather
    // than reaching the parser this test is about.
    el.type = 'application/json';
    el.id = 'trip-map-payload';
    el.textContent = '{not json';
    document.body.appendChild(el);

    expect(core.readPayload(document)).toBeNull();

    document.body.removeChild(el);
  });
});

describe('ensureMeetingIcon — the image handed to MapLibre', () => {
  /**
   * A fake MapLibre map that records what addImage was given.
   *
   * @param {boolean} has Whether the style already holds the icon.
   * @returns {{hasImage: Function, addImage: Function, calls: Array}}
   */
  function fakeMap(has) {
    const calls = [];
    return {
      calls: calls,
      hasImage: () => has,
      addImage: (id, image, options) => calls.push({ id, image, options }),
    };
  }

  it('passes the core its own image object, not a re-wrapped one', () => {
    // The regression this exists for: the icon was handed over as
    // `{width, height, data: core.startDotPixels(...)}`, but the core
    // ALREADY returns `{width, height, data}`. That put a whole image
    // object where MapLibre expects the raw pixel buffer, so it threw
    // "mismatched image size" inside installLayers and killed the meeting
    // marker — and everything after it — on every trip page. The payload
    // arithmetic was fine, which is exactly why nothing else caught it.
    const map = fakeMap(false);
    core.ensureMeetingIcon(map);

    expect(map.calls).toHaveLength(1);
    const image = map.calls[0].image;
    // MapLibre's own contract: the buffer must be exactly width × height
    // RGBA bytes. This is the assertion the bug failed.
    expect(image.data.length).toBe(image.width * image.height * 4);
    expect(ArrayBuffer.isView(image.data)).toBe(true);
  });

  it('registers at the core pixel ratio, so the dot is not drawn double size', () => {
    const map = fakeMap(false);
    core.ensureMeetingIcon(map);

    expect(map.calls[0].options.pixelRatio).toBe(self.pwaRouteMarkersCore.PIXEL_RATIO);
  });

  it('is free to call again once the style already holds the icon', () => {
    // installLayers runs on every style load, so a repeat call must not
    // throw — addImage rejects a duplicate id.
    const map = fakeMap(true);
    core.ensureMeetingIcon(map);

    expect(map.calls).toHaveLength(0);
  });
});

/*
 * The trip's line, drawn as the map draws it (SNOW-1212).
 *
 * The layers themselves are route_drawing_core.js's and are tested there.
 * What is worth holding here is that the trip page hands its route to that
 * drawing at all — the page used to paint the SNOW-962 slope classes at
 * every zoom — and that its blank-basemap check knows every source the
 * drawing adds.
 */

/** A payload whose route carries a slope record with the given angles. */
function sampled(angles) {
  const points = [];
  for (let i = 0; i <= angles.length; i += 1) points.push([7.4 + i / 1000, 46.1]);
  const p = payload();
  p.route.geometry.coordinates = points.map(([x, y], i) => [x, y, 1500 + 100 * i]);
  p.route.properties.slope = { points, angles };
  p.route.properties.legs = [
    { i: 1, from: 0, to: angles.length - 1, climbing: true, point_from: 0, point_to: angles.length },
  ];
  return p;
}

describe('installLayers — the marks a trip map draws', () => {
  /**
   * A MapLibre stand-in that records the sources and layers it is given.
   *
   * @returns {{sources: Array<string>, layers: Array<object>}}
   */
  function recordingMap() {
    const sources = [];
    const layers = [];
    return {
      sources,
      layers,
      getSource: (id) => (sources.includes(id) ? {} : null),
      addSource: (id) => sources.push(id),
      addLayer: (def) => layers.push(def),
      hasImage: () => false,
      addImage: () => {},
      getStyle: () => ({ layers: [] }),
    };
  }

  it('draws the route as its legs below z14 and its slope classes from z14', () => {
    const map = recordingMap();

    core.installLayers(map, sampled([12, 34]));

    const ids = map.layers.map((layer) => layer.id);
    expect(ids).toEqual(expect.arrayContaining([
      'trip-route-leg-climb',
      'trip-route-leg-descent',
      'trip-route-slope-line',
      'trip-route-transitions',
      'trip-route-endpoints',
    ]));
    const slope = map.layers.find((layer) => layer.id === 'trip-route-slope-line');
    expect(slope.minzoom).toBe(self.pwaRouteDrawingCore.ROUTE_SLOPE_MINZOOM);
  });

  it("draws the profile's point under the meeting marker", () => {
    // SNOW-1216. The meeting point is the plan; a reader scrubbing past
    // it must never hide it.
    const map = recordingMap();

    core.installLayers(map, sampled([12, 34]));

    const ids = map.layers.map((layer) => layer.id);
    expect(map.sources).toContain('trip-route-cursor');
    expect(ids.indexOf('trip-route-cursor')).toBeLessThan(ids.indexOf('trip-meeting-point'));
  });

  it('keeps the meeting marker on top of the route', () => {
    const map = recordingMap();

    core.installLayers(map, sampled([12, 34]));

    expect(map.layers[map.layers.length - 1].id).toBe('trip-meeting-point');
  });

  it('is free to call again on a style reload', () => {
    const map = recordingMap();

    core.installLayers(map, sampled([12, 34]));
    const count = map.layers.length;
    core.installLayers(map, sampled([12, 34]));

    expect(map.layers).toHaveLength(count);
  });

  it('draws no fall-line arrow or passage split, though the snapshot carries both', () => {
    // Taken off both maps by SNOW-1019. The records still travel, which
    // is what makes this worth holding — the marks could come back
    // unnoticed.
    const p = sampled([40, 52]);
    p.route.properties.slope.fall_lines = [{ i: 1, deg: 205 }];
    p.route.properties.slope.passages = [{ from: 1, to: 1, m: 25, fall_line: 'climbing' }];
    const map = recordingMap();

    core.installLayers(map, p);

    const ids = [...map.sources, ...map.layers.map((layer) => layer.id)];
    expect(ids.filter((id) => /fall-line|passage/.test(id))).toEqual([]);
    expect(core).not.toHaveProperty('routeFallLineSourceData');
  });
});

/*
 * The profile's point, on the map (SNOW-1216).
 *
 * The profile announces a sample index; the map draws it where the map
 * page would — the middle of the index's segment on a sampled route, the
 * middle of its equal share on one never sampled.
 */
describe("cursorFeature — where the profile's point lands", () => {
  it('is the middle of the index segment on a sampled route', () => {
    const point = core.cursorFeature(sampled([12, 34]), 1);

    expect(point.geometry.type).toBe('Point');
    expect(point.geometry.coordinates[0]).toBeCloseTo(7.4015, 6);
    expect(point.geometry.coordinates[1]).toBeCloseTo(46.1, 6);
  });

  it("carries the segment's slope class colour", () => {
    const point = core.cursorFeature(sampled([12, 34]), 1);
    const slope = self.pwaRouteSlopeCore;

    expect(point.properties.colour).toBe(slope.CLASSES[slope.classify(34)].hex);
  });

  it('falls back to equal shares of the line on a route never sampled', () => {
    const p = payload();
    p.route.properties.legs = [
      { i: 1, from: 0, to: 1, climbing: true, point_from: 0, point_to: 2 },
    ];

    const first = core.cursorFeature(p, 0);
    const second = core.cursorFeature(p, 1);

    expect(first.geometry.coordinates[0]).toBeLessThan(second.geometry.coordinates[0]);
    expect(first.properties.colour).toBeUndefined();
  });

  it('answers null with no point placed, or nothing to place it on', () => {
    expect(core.cursorFeature(sampled([12, 34]), null)).toBeNull();
    expect(core.cursorFeature(payload(), 0)).toBeNull();
    expect(core.cursorFeature(null, 0)).toBeNull();
  });
});
