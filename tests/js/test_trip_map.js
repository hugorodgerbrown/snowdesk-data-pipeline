/*
 * tests/js/test_trip_map.js — the trip page's map payload arithmetic
 * (SNOW-820).
 *
 * `trip_map.js` builds one camera and two sources from an inline payload.
 * Everything worth asserting about that is a pure function of the payload
 * — the bbox reshape, what counts as a drawable line, what counts as a
 * placeable marker, and the rule that a track with no elevation gets NO
 * profile rather than a flat one. None of it needs a browser, so none of
 * it belongs in `tests/e2e/`.
 *
 * The two `_core` modules are imported first because `trip_map.js` reads
 * them off `self` at its own top level, exactly as the page loads them.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_markers_core.js';
import '../../static/js/route_slope_core.js';
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

describe('profileFor', () => {
  it('reads the elevation series off the geometry itself', () => {
    // `ele` is the third ordinate of every stored coordinate, so the
    // profile is a derivation of the payload the page already holds.
    const profile = core.profileFor(payload());

    expect(profile.hasElevation).toBe(true);
  });

  it('reports no elevation for a track whose points carry none', () => {
    // The rule Trip.ascent_m's null exists to enforce, at the drawing end:
    // "we don't know" must not be drawn as a flat line at zero.
    const profile = core.profileFor(
      payload({
        route: {
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: [
              [7.4, 46.1, null],
              [7.41, 46.11, null],
            ],
          },
          properties: {},
        },
      })
    );

    expect(profile.hasElevation).toBe(false);
    expect(
      self.pwaElevationProfileCore.createProfileSvg(profile, { doc: document })
    ).toBeNull();
  });

  it('returns null for a payload with nothing to profile', () => {
    expect(core.profileFor(null)).toBeNull();
  });
});

describe('drawProfileRange — the caption that gives the curve a quantity', () => {
  /** A document carrying the card's empty range paragraph. */
  function docWithRange() {
    const doc = document.implementation.createHTMLDocument('trip');
    const p = doc.createElement('p');
    p.setAttribute('data-trip-profile-range', '');
    doc.body.appendChild(p);
    return doc;
  }

  it('states the low and high the y-axis was fitted to', () => {
    // The chart is scaled to THIS track's own range, so the same curve is
    // drawn for a 200 m rolling traverse and a 2000 m climb. Without the
    // pair the picture carries no quantity at all.
    const doc = docWithRange();

    core.drawProfileRange(core.profileFor(payload()), doc);

    expect(doc.querySelector('[data-trip-profile-range]').textContent).toBe(
      '1500–1600 m'
    );
  });

  it('rounds to whole metres, as the map popup caption does', () => {
    const doc = docWithRange();

    core.drawProfileRange({ minEle: 1741.4, maxEle: 3663.8 }, doc);

    expect(doc.querySelector('[data-trip-profile-range]').textContent).toBe(
      '1741–3664 m'
    );
  });

  it('writes nothing for a track that carries no elevation', () => {
    // `hasElevation: false` leaves minEle/maxEle null, and "null–null m"
    // would be a scale for a picture that was never drawn.
    const doc = docWithRange();

    core.drawProfileRange({ minEle: null, maxEle: null }, doc);

    expect(doc.querySelector('[data-trip-profile-range]').textContent).toBe('');
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
 * The trip's line, coloured by the ground under it (SNOW-962).
 *
 * The claim worth testing is the one about the FLAT line. A trip whose
 * snapshot carries a slope record is painted segment by segment, and
 * leaving the flat line in as well paints the track twice — the route
 * colour showing through at every butt-capped join. A trip with no record
 * must keep the flat line, because an unsampled track is not an unknown
 * one and a page that drew nothing would look broken.
 */

/** A payload whose route carries a slope record with the given angles. */
function sampled(angles) {
  const points = [];
  for (let i = 0; i <= angles.length; i += 1) points.push([7.4 + i / 1000, 46.1]);
  const p = payload();
  p.route.properties.slope = { points, angles };
  return p;
}

describe('routeSlopeSourceData', () => {
  it('pairs the record out into one LineString per segment', () => {
    const data = core.routeSlopeSourceData(sampled([12, 34, 47]));

    expect(data.features).toHaveLength(3);
    expect(data.features[0].geometry.coordinates).toHaveLength(2);
    // The middle segment is in the 30–35 band, which is index 1.
    expect(data.features[1].properties.slope_class).toBe(1);
  });

  it('marks an unanswered segment unknown and gives it no class', () => {
    // A class on an unknown is one step expression away from being
    // painted as gentle ground, which is the outcome the whole feature
    // exists to prevent.
    const data = core.routeSlopeSourceData(sampled([12, null]));

    expect(data.features[1].properties.unknown).toBe(true);
    expect(data.features[1].properties.slope_class).toBeUndefined();
  });

  it('is a valid empty collection for a trip nothing has sampled', () => {
    // setData throws on a null, so this must never be one.
    expect(core.routeSlopeSourceData(payload())).toEqual({
      type: 'FeatureCollection',
      features: [],
    });
    expect(core.routeSlopeSourceData(null).features).toEqual([]);
  });
});

describe('the no-fall passage flag on a trip\'s segments (SNOW-964)', () => {
  /** A sampled payload whose record names the given passages. */
  function withPassages(angles, passages) {
    const p = sampled(angles);
    p.route.properties.slope.passages = passages;
    return p;
  }

  it('carries the mark through to the segments the page draws', () => {
    // The flag arrives through `segmentFeatures`; nothing in trip_map.js
    // reads the record for it. Since SNOW-1019 no layer draws it — the
    // test holds the data, which the snapshot still carries.
    const data = core.routeSlopeSourceData(
      withPassages([12, 52, 51, 12], [{ from: 1, to: 2, m: 50, fall_line: 'climbing' }]),
    );

    expect(data.features.map((f) => f.properties.passage))
      .toEqual([undefined, true, true, undefined]);
  });

  it('marks nothing on a trip whose record names no passages', () => {
    const data = core.routeSlopeSourceData(withPassages([12, 34], []));

    for (const feature of data.features) {
      expect(feature.properties).not.toHaveProperty('passage');
    }
  });

  it('marks nothing on a snapshot taken before passages existed', () => {
    // A trip is a verbatim copy of the record as it stood, and an older
    // one simply has no `passages` key — which must draw the track
    // unmarked rather than throw and take the whole map with it.
    const data = core.routeSlopeSourceData(sampled([12, 52]));

    expect(data.features).toHaveLength(2);
    for (const feature of data.features) {
      expect(feature.properties).not.toHaveProperty('passage');
    }
  });
});

describe('isSlopeColoured', () => {
  it('is true when the record produces segments to paint', () => {
    expect(core.isSlopeColoured(sampled([12, 34]))).toBe(true);
  });

  it('is false for a trip that has never been sampled', () => {
    // The flat line stays, and the page draws the track it always did.
    expect(core.isSlopeColoured(payload())).toBe(false);
  });

  it('is false for a record that pairs up wrongly', () => {
    // Three coordinates bound two segments, not three. Rather than paint
    // segments against the wrong ground, nothing is coloured and the flat
    // line carries the track.
    const p = payload();
    p.route.properties.slope = {
      points: [[7.4, 46.1], [7.41, 46.11], [7.42, 46.12]],
      angles: [12, 34, 47],
    };

    expect(core.isSlopeColoured(p)).toBe(false);
  });
});

describe('installLayers — the marks a trip map draws (SNOW-1019)', () => {
  /**
   * A MapLibre stand-in that records the sources and layers it is given.
   *
   * @returns {{sources: Array<string>, layers: Array<string>}}
   */
  function recordingMap() {
    const sources = [];
    const layers = [];
    return {
      sources,
      layers,
      getSource: (id) => (sources.includes(id) ? {} : null),
      addSource: (id) => sources.push(id),
      addLayer: (def) => layers.push(def.id),
      hasImage: () => false,
      addImage: () => {},
    };
  }

  it('draws no crux ring, fall-line arrow or passage split, though the snapshot carries all three', () => {
    // Taken off both maps by SNOW-1019: the crux is deferred, the bank
    // ribbon replaced the arrows, and the passages are bars on the map
    // page's rail two. The records still travel, which is what makes this
    // worth holding — the marks could come back unnoticed.
    const p = sampled([40, 52]);
    p.route.properties.slope.cruxes = [[7.4, 46.1]];
    p.route.properties.slope.fall_lines = [{ i: 1, deg: 205 }];
    p.route.properties.slope.passages = [{ from: 1, to: 1, m: 25, fall_line: 'climbing' }];
    const map = recordingMap();

    core.installLayers(map, p);

    const ids = [...map.sources, ...map.layers];
    expect(ids).toContain('trip-route-slope-line');
    expect(ids.filter((id) => /crux|fall-line|passage/.test(id))).toEqual([]);
    expect(core).not.toHaveProperty('routeCruxSourceData');
    expect(core).not.toHaveProperty('routeFallLineSourceData');
  });
});
