/*
 * tests/js/test_trip_deeplink_core.js — the map's trip-arrival arithmetic
 * (SNOW-828).
 *
 * `map.js` turns a `?trip=` / `?trip_share=` arrival into a fetch, a line
 * and a camera. Everything worth asserting about the URL half of that is a
 * pure function of the query string and the page's two URL templates —
 * which parameter was read, which endpoint answers it, and what is left in
 * the address bar afterwards. None of it needs a browser or a map, so per
 * docs/client-side-tests.md none of it belongs in `tests/e2e/`.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/trip_deeplink_core.js';

const core = self.pwaTripDeepLinkCore;

/** The templates as apps.public.views._trips_context emits them. */
const TEMPLATES = {
  uuid: '/trips/__UUID__/route.geojson',
  token: '/trips/s/__TOKEN__/route.geojson',
};

const UUID = '3f7a1c2e-9d64-4b1a-8f0e-5c2b7a9d1e34';
const TOKEN = 'Qx7mB2rTa1Z';

describe('read', () => {
  it('reads a participant arrival as a uuid', () => {
    expect(core.read(`?trip=${UUID}`)).toEqual({
      param: 'trip',
      kind: 'uuid',
      value: UUID,
    });
  });

  it('reads a link-holder arrival as a token', () => {
    expect(core.read(`?trip_share=${TOKEN}`)).toEqual({
      param: 'trip_share',
      kind: 'token',
      value: TOKEN,
    });
  });

  it('prefers the participant parameter when a URL carries both', () => {
    // Not a shape anything emits — a tiebreak, not a contract. It resolves
    // to the endpoint that still answers for a trip whose share has since
    // been revoked.
    const link = core.read(`?trip_share=${TOKEN}&trip=${UUID}`);
    expect(link.kind).toBe('uuid');
  });

  it('returns null for a query string with no trip in it', () => {
    expect(core.read('?d=2026-02-16&panel=routes')).toBeNull();
    expect(core.read('')).toBeNull();
    expect(core.read(null)).toBeNull();
  });

  it('treats an empty value as no value', () => {
    // `?trip=` is a malformed link; fetching /trips//route.geojson for it
    // would 404 noisily rather than doing nothing quietly.
    expect(core.read('?trip=')).toBeNull();
  });

  it('reads the parameter from anywhere in the query string', () => {
    expect(core.read(`?d=2026-02-16&trip=${UUID}&panel=routes`).value).toBe(UUID);
  });
});

describe('endpointFor', () => {
  it('builds the participant endpoint from the uuid template', () => {
    const link = core.read(`?trip=${UUID}`);
    expect(core.endpointFor(link, TEMPLATES)).toBe(
      `/trips/${UUID}/route.geojson`,
    );
  });

  it('builds the tokenised endpoint from the token template', () => {
    const link = core.read(`?trip_share=${TOKEN}`);
    expect(core.endpointFor(link, TEMPLATES)).toBe(
      `/trips/s/${TOKEN}/route.geojson`,
    );
  });

  it('percent-encodes the value into the path', () => {
    // Neither identifier space contains a character needing it, but the
    // value arrives from the address bar and a path built from it must not
    // be assemblable into something else.
    const link = core.read('?trip_share=a%2Fb');
    expect(core.endpointFor(link, TEMPLATES)).toBe(
      '/trips/s/a%2Fb/route.geojson',
    );
  });

  it('returns null when the page carries no template', () => {
    const link = core.read(`?trip=${UUID}`);
    expect(core.endpointFor(link, { uuid: null, token: null })).toBeNull();
    expect(core.endpointFor(link, null)).toBeNull();
  });

  it('returns null when the template has no placeholder to fill', () => {
    const link = core.read(`?trip=${UUID}`);
    expect(core.endpointFor(link, { uuid: '/trips/route.geojson' })).toBeNull();
  });

  it('returns null for no link', () => {
    expect(core.endpointFor(null, TEMPLATES)).toBeNull();
  });
});

describe('strip', () => {
  it('removes the parameter that was read', () => {
    expect(core.strip(`?trip=${UUID}`)).toBe('');
  });

  it('removes BOTH parameters, not only the one read', () => {
    // The unread one would otherwise stay as a standing instruction.
    expect(core.strip(`?trip=${UUID}&trip_share=${TOKEN}`)).toBe('');
  });

  it('leaves every other parameter alone, and keeps the ?', () => {
    expect(core.strip(`?d=2026-02-16&trip=${UUID}&panel=routes`)).toBe(
      '?d=2026-02-16&panel=routes',
    );
  });

  it('returns an empty string for an empty query', () => {
    expect(core.strip('')).toBe('');
    expect(core.strip(null)).toBe('');
  });
});
