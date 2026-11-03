/*
 * tests/js/test_rest_zones_core.js — the pure half of the wildlife rest
 * zones layer (static/js/rest_zones_core.js, SNOW-1384).
 *
 * The rectangle a view loads, whether a loaded one still holds the view,
 * the endpoint URL, the paint over `binding` and `in_force`, and which
 * zone a tap opens.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/rest_zones_core.js';

const core = self.pwaRestZonesCore;

describe('the limits', () => {
  it('draws from z10 and loads at most two degrees each way', () => {
    expect(core.MINZOOM).toBe(10);
    expect(core.MAX_SPAN_DEG).toBe(2.0);
  });
});

describe('loadBox', () => {
  it('pads a view and rounds it outward to the pad', () => {
    expect(core.loadBox([10.02, 46.81, 10.13, 46.88])).toEqual([9.95, 46.75, 10.2, 46.95]);
  });

  it('drops the padding before refusing a view near the limit', () => {
    const box = core.loadBox([10.0, 46.0, 11.98, 46.5]);
    expect(box).not.toBeNull();
    expect(box[2] - box[0]).toBeLessThanOrEqual(core.MAX_SPAN_DEG);
    expect(box[0]).toBeLessThanOrEqual(10.0);
    expect(box[2]).toBeGreaterThanOrEqual(11.98);
  });

  it('loads nothing for a view wider than the limit', () => {
    expect(core.loadBox([8.0, 46.0, 10.5, 46.5])).toBeNull();
    expect(core.loadBox([9.0, 45.5, 9.5, 47.6])).toBeNull();
  });
});

describe('covers', () => {
  it('holds a view inside it, edges included, and nothing else', () => {
    const box = [10.0, 46.0, 10.5, 46.5];
    expect(core.covers(box, [10.0, 46.0, 10.5, 46.5])).toBe(true);
    expect(core.covers(box, [10.1, 46.1, 10.2, 46.2])).toBe(true);
    expect(core.covers(box, [9.99, 46.1, 10.2, 46.2])).toBe(false);
    expect(core.covers(box, [10.1, 46.1, 10.2, 46.51])).toBe(false);
    expect(core.covers(null, [10.1, 46.1, 10.2, 46.2])).toBe(false);
  });
});

describe('dataUrl', () => {
  it('asks for the box and the day', () => {
    expect(core.dataUrl('/api/rest-zones.geojson', [10, 46, 10.5, 46.5], '2027-01-10'))
      .toBe('/api/rest-zones.geojson?bbox=10,46,10.5,46.5&d=2027-01-10');
  });

  it('leaves the day to the server when the map has none', () => {
    expect(core.dataUrl('/api/rest-zones.geojson', [10, 46, 10.5, 46.5], null))
      .toBe('/api/rest-zones.geojson?bbox=10,46,10.5,46.5');
  });

  it('appends to a base that already has a query', () => {
    expect(core.dataUrl('/x?y=1', [1, 2, 3, 4], null)).toBe('/x?y=1&bbox=1,2,3,4');
  });
});

/**
 * Evaluate the small subset of MapLibre expressions the paint uses, for
 * one feature's properties: enough to check what each case paints.
 */
function evaluate(expr, props) {
  if (!Array.isArray(expr)) return expr;
  const [op, ...args] = expr;
  switch (op) {
    case 'get':
      return props[args[0]];
    case '==':
      return evaluate(args[0], props) === evaluate(args[1], props);
    case '!=':
      return evaluate(args[0], props) !== evaluate(args[1], props);
    case 'case': {
      for (let i = 0; i + 1 < args.length; i += 2) {
        if (evaluate(args[i], props)) return evaluate(args[i + 1], props);
      }
      return evaluate(args[args.length - 1], props);
    }
    default:
      throw new Error('unexpected operator ' + op);
  }
}

describe('the paint', () => {
  const binding = { binding: true, in_force: true };
  const recommended = { binding: false, in_force: true };
  const outOfSeason = { binding: true, in_force: false };

  it('colours a binding zone red and a recommended one amber', () => {
    const fill = core.fillPaint()['fill-color'];
    const line = core.linePaint()['line-color'];
    expect(evaluate(fill, binding)).toBe(core.BINDING_COLOUR);
    expect(evaluate(fill, recommended)).toBe(core.RECOMMENDED_COLOUR);
    expect(evaluate(line, binding)).toBe(core.BINDING_COLOUR);
    expect(evaluate(line, recommended)).toBe(core.RECOMMENDED_COLOUR);
  });

  it('fills a binding zone strongest, a recommended one fainter, one out of season faintest', () => {
    const opacity = core.fillPaint()['fill-opacity'];
    const a = evaluate(opacity, binding);
    const b = evaluate(opacity, recommended);
    const c = evaluate(opacity, outOfSeason);
    expect(a).toBeGreaterThan(b);
    expect(b).toBeGreaterThan(c);
    // Never zero: a tap inside a zone is answered only from what is painted.
    expect(c).toBeGreaterThan(0);
  });

  it('fades the outline of a zone out of its period', () => {
    const opacity = core.linePaint()['line-opacity'];
    expect(evaluate(opacity, binding)).toBeGreaterThan(evaluate(opacity, outOfSeason));
  });
});

describe('pickZone', () => {
  // As MapLibre answers: no top-level id (a string id is dropped), the uuid
  // in the properties the source promotes.
  it('takes the topmost zone', () => {
    const top = { properties: { uuid: 'a', name: 'Top' } };
    const under = { properties: { uuid: 'b', name: 'Under' } };
    expect(core.pickZone([top, under])).toBe(top);
  });

  it('skips a hit with no properties or no uuid', () => {
    const zone = { properties: { uuid: 'b', name: 'Zone' } };
    expect(core.pickZone([{ properties: {} }, { id: 'x' }, zone])).toBe(zone);
  });

  it('answers null for nothing', () => {
    expect(core.pickZone([])).toBeNull();
    expect(core.pickZone(null)).toBeNull();
  });
});
