/*
 * tests/js/test_map_state_channel.js — Vitest unit tests for
 * `window.snowdeskMapState`, the one named channel onto the map bundle's
 * shared module state (SNOW-610, covered under SNOW-649).
 *
 * SNOW-610 split map.js into thirteen classic scripts that share one
 * lexical scope. `MAP`, `COUNTRY_STATE` and the feature lookups stay bare
 * module-scope bindings, which is exactly right for the bundle and useless
 * to anything outside it — so this channel exists as the single sanctioned
 * door in.
 *
 * The property that earns a test is that the accessors are LIVE. A channel
 * that snapshotted its values at construction would look identical in
 * every smoke test — `map` would be null and `featureByRegionId` empty on
 * a page that had not finished booting, which is also what a correct
 * channel returns at that moment. The failure only shows up later, as a
 * surface silently reading a stale map forever. That is the shape of the
 * `window.MAP` bug js-globals-lint was written for: a read that is always
 * undefined, and every guard around it quietly taking the "no map yet"
 * branch.
 *
 * Only map_state.js is booted — the file is pure declarations plus the
 * channel, with no boot IIFE to satisfy.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

let channel;

beforeEach(() => {
  loadMapBundle(['map_state.js']);
  channel = window.snowdeskMapState;
});

describe('the channel surface', () => {
  it('is frozen, so no surface can bolt its own state onto it', () => {
    expect(Object.isFrozen(channel)).toBe(true);
  });

  it('throws on an attempt to add a property, rather than dropping it quietly', () => {
    // ES modules are strict mode, so a write to a frozen object is a
    // TypeError rather than a silent no-op — the loud direction, and the
    // one that surfaces a surface trying to stash state on the channel.
    expect(() => {
      channel.somethingNew = 1;
    }).toThrow(TypeError);
    expect(channel.somethingNew).toBeUndefined();
  });

  it('exposes the documented members', () => {
    for (const key of [
      'map',
      'ready',
      'featureById',
      'featureByRegionId',
      'countryState',
      // SNOW-660 removed 'bootDateKey': nothing computes a cold-open date
      // any more, so there is no shared binding for one to travel on.
      'autozoom',
      'isPlaying',
    ]) {
      expect(key in channel).toBe(true);
    }
  });
});

describe('reads are live, not snapshots', () => {
  it('starts with no map, matching a page that has not booted', () => {
    expect(channel.map).toBeNull();
  });

  it('reflects a map assigned after the channel was built', () => {
    const fakeMap = { id: 'maplibre' };
    channel.map = fakeMap;
    expect(channel.map).toBe(fakeMap);
  });

  it('reflects region lookups populated after boot', () => {
    // The bundle populates FEATURE_BY_REGION_ID inside map.on('load'),
    // long after this channel object exists.
    expect(channel.featureByRegionId).toEqual({});
    channel.featureByRegionId['CH-4115'] = { id: 1 };
    expect(channel.featureByRegionId['CH-4115']).toEqual({ id: 1 });
  });

  it('hands back the same object identity on every read, not a copy', () => {
    // A defensive copy would make `channel.countryState.fr = true` a no-op
    // from the bundle's point of view — a write that appears to work and
    // changes nothing.
    expect(channel.countryState).toBe(channel.countryState);
  });

  it('exposes the boot country state', () => {
    expect(channel.countryState).toEqual({ ch: true, fr: false, at: false, it: false });
  });

  it('exposes a ready promise rather than a boolean', () => {
    expect(typeof channel.ready.then).toBe('function');
  });
});

describe('writes reach the bundle’s own bindings', () => {
  it('writes autozoom through', () => {
    expect(channel.autozoom).toBe(false);
    channel.autozoom = true;
    expect(channel.autozoom).toBe(true);
  });

  it('writes isPlaying through', () => {
    channel.isPlaying = true;
    expect(channel.isPlaying).toBe(true);
  });

  it('keeps a country toggle visible to later readers', () => {
    channel.countryState.fr = true;
    expect(window.snowdeskMapState.countryState.fr).toBe(true);
  });
});

describe('read-only members reject writes rather than silently diverging', () => {
  // These are getter-only by design: the bundle owns the container, and a
  // surface that replaced it wholesale would leave every other reader
  // holding the old object. Under strict mode that write throws, which is
  // the outcome worth having — a silent no-op would look like it worked.

  it('refuses to have featureByRegionId replaced', () => {
    const before = channel.featureByRegionId;
    expect(() => {
      channel.featureByRegionId = { replaced: true };
    }).toThrow(TypeError);
    expect(channel.featureByRegionId).toBe(before);
  });

  it('refuses to have countryState replaced', () => {
    const before = channel.countryState;
    expect(() => {
      channel.countryState = { ch: false };
    }).toThrow(TypeError);
    expect(channel.countryState).toBe(before);
  });
});
