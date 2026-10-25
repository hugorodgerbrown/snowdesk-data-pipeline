/*
 * tests/js/test_map_routes_overlay_403.js — a 403 from routes.geojson
 * empties the routes layer rather than drawing the error (SNOW-1230).
 *
 * Scenario: none — a fetch status mapped to an empty FeatureCollection,
 * asserted on the map stub's source. No browser is needed to prove it.
 *
 * A signed-out visitor sees routes only through pending shares. When the
 * example walkthrough ends it drops the example from the session and
 * announces 'snowdesk:routes-changed'; the re-read then answers 403 (no
 * account, nothing pending). Before SNOW-1230 that 403's JSON error body
 * went into the layer as data. Now the answer is "no routes", and any
 * other failure still leaves the map as it was.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import {
  announce,
  boot,
  harness,
  resetHarnessState,
  teardown,
} from './_route_slope_harness.js';

let mapStub;

beforeAll(async () => {
  mapStub = await boot({ showRoutes: true });
});

afterAll(teardown);

beforeEach(() => {
  resetHarnessState();
});

describe('a routes re-read that answers 403', () => {
  it('draws no routes', async () => {
    const source = mapStub.sources.get('routes');
    source.setData.mockClear();
    harness.status = 403;
    harness.payload = { error: 'authentication_required' };

    announce({});
    await vi.waitFor(() => expect(source.setData).toHaveBeenCalled());

    const drawn = source.setData.mock.calls.at(-1)[0];
    expect(drawn.features).toEqual([]);
  });

  it('leaves the map as it was on any other failure', async () => {
    const source = mapStub.sources.get('routes');
    source.setData.mockClear();
    harness.status = 500;

    announce({});
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(source.setData).not.toHaveBeenCalled();
  });
});
