/*
 * tests/js/test_country_load_core.js — the two decisions inside a country
 * load (SNOW-898).
 *
 * Both were conditional expressions buried in `ensureCountryLoaded`, and
 * both encode rules learned from incidents. Neither could be exercised
 * without booting the whole map bundle with a fake MapLibre attached, so in
 * practice neither was exercised at all.
 *
 * The cases below are those incidents, named. Each one has a comment saying
 * what shipped when the rule was got wrong, because the point of this file
 * is that the next person to edit those expressions finds out.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/country_load_core.js';

const core = window.pwaCountryLoadCore;

/** Every URL configured, which is the production shape. */
const ALL_URLS = {
  hasRegionsUrl: true,
  hasMajorUrl: true,
  hasSubUrl: true,
  hasRatingsUrl: true,
};

describe('planCountryFeeds', () => {
  it('fetches all four feeds for a country the user toggles on', () => {
    expect(core.planCountryFeeds({ isBootCountry: false, ...ALL_URLS })).toEqual({
      regions: true,
      major: true,
      sub: true,
      ratings: 'fetch',
    });
  });

  it('skips only what boot already fetched for the boot country', () => {
    // THE SNOW-524 RULE. Switzerland used to be marked loaded off the boot
    // fetches alone, so its L1 and L2 were never fetched at all — Switzerland
    // alone could never reach the cached state every other country reached.
    // `major` and `sub` being TRUE here is the whole assertion; `regions`
    // being false is the easy half.
    expect(core.planCountryFeeds({ isBootCountry: true, ...ALL_URLS })).toEqual({
      regions: false,
      major: true,
      sub: true,
      ratings: 'ensure-cached',
    });
  });

  it('distinguishes topping up the ratings cache from fetching ratings', () => {
    // The boot country's season ratings are already in memory, but may be
    // absent from Cache Storage: on a first-ever visit that fetch runs before
    // the service worker controls the page, so it is never intercepted. The
    // two branches are different work, not the same work twice.
    expect(core.planCountryFeeds({ isBootCountry: true, ...ALL_URLS }).ratings)
      .toBe('ensure-cached');
    expect(core.planCountryFeeds({ isBootCountry: false, ...ALL_URLS }).ratings)
      .toBe('fetch');
  });

  it('asks for nothing that has no URL configured', () => {
    expect(core.planCountryFeeds({ isBootCountry: false })).toEqual({
      regions: false,
      major: false,
      sub: false,
      ratings: 'skip',
    });
  });

  it('survives being called with no options at all', () => {
    // Defensive rather than decorative: this runs inside a map event
    // handler, where a throw is swallowed and the country silently never
    // loads.
    expect(() => core.planCountryFeeds()).not.toThrow();
  });
});

/** A complete load of a country the user toggled on. */
const COMPLETE = {
  isBootCountry: false,
  regionsLoaded: true,
  majorLoaded: true,
  subLoaded: true,
  ratingsOk: true,
  rowCountryCount: 1,
};

describe('syncDotAction', () => {
  it('greens a single-country row once every feed has landed', () => {
    expect(core.syncDotAction(COMPLETE)).toBe('mark-cached');
  });

  it('counts a feed boot already fetched as landed', () => {
    // The boot country never fetches its own L4 here, so `regionsLoaded` is
    // false on a complete load. Reading that as a gap would leave
    // Switzerland's dot permanently grey however many times it loaded.
    expect(core.syncDotAction({
      ...COMPLETE, isBootCountry: true, regionsLoaded: false,
    })).toBe('mark-cached');
  });

  it('refuses to green a row when a boundary feed is missing', () => {
    // THE OTHER HALF OF SNOW-524. The L1/L2 legs swallow their own failures
    // so a partial load does not reject — which means a successful CALL is
    // not evidence of complete data. Greening off the call is how a country
    // with missing boundaries came to read as fully available offline.
    for (const missing of ['majorLoaded', 'subLoaded', 'ratingsOk']) {
      expect(
        core.syncDotAction({ ...COMPLETE, [missing]: false }),
        `${missing} missing must not green the row`,
      ).toBe('refresh');
    }
  });

  it('refuses to green a non-boot country whose own L4 never landed', () => {
    expect(core.syncDotAction({ ...COMPLETE, regionsLoaded: false }))
      .toBe('refresh');
  });

  it('never greens a grouped provider row, even on a complete load', () => {
    // THE SNOW-658 RULE. AT and IT share the ALBINA row, whose dot may only
    // green once BOTH have landed. This load knows about one of them, so an
    // optimistic mark would be a lie — hand off to a real probe.
    expect(core.syncDotAction({ ...COMPLETE, rowCountryCount: 2 }))
      .toBe('refresh');
  });

  it('treats an unstated row size as a single-country row', () => {
    const { rowCountryCount, ...withoutCount } = COMPLETE;
    expect(core.syncDotAction(withoutCount)).toBe('mark-cached');
  });

  it('survives being called with no options at all', () => {
    expect(() => core.syncDotAction()).not.toThrow();
    // Nothing landed, so nothing may be claimed.
    expect(core.syncDotAction()).toBe('refresh');
  });
});
