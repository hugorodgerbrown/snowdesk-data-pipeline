/*
 * static/js/country_load_core.js — the two decisions inside a country load
 * (SNOW-898).
 *
 * `ensureCountryLoaded` in map.js is 147 lines of code wrapped around two
 * questions that are pure, subtle, and were reachable only by booting the
 * whole map bundle with a fake MapLibre attached:
 *
 *   1. Which of this country's four feeds must actually be fetched?
 *   2. Given which of them landed, what should the layers-menu sync dot do?
 *
 * Both encode rules learned from incidents, and both were expressed as a
 * conditional expression under a paragraph of comment — the shape where a
 * later edit changes the behaviour and nothing notices.
 *
 * WHY QUESTION 1 IS NOT OBVIOUS
 * -----------------------------
 * The boot country is special: the map's own boot has already fetched its L4
 * geometry and its season ratings, so re-fetching them would be waste. But
 * SNOW-524 is the other half, and it is the half that bit. Switzerland used
 * to be marked loaded off those two boot fetches alone, which meant its L1
 * and L2 were never fetched at all — so Switzerland, and only Switzerland,
 * could never reach the cached state every country the user toggles on
 * reaches. "Skip what boot did" and "skip this country entirely" are one
 * character apart in the code and a permanent bug apart in behaviour.
 *
 * WHY QUESTION 2 IS NOT OBVIOUS EITHER
 * ------------------------------------
 * Two rules meet here.
 *
 * SNOW-524: the L1 and L2 fetches swallow their own failures (`.catch(() =>
 * null)`) so a partial load does not reject the whole thing. That means the
 * country's dot must not be greened off a successful *call* — only off
 * complete data — or a country with missing boundaries reads as fully
 * cached. And a feed that was skipped BECAUSE BOOT ALREADY FETCHED IT counts
 * as landed, which is why this is not simply "all four are truthy".
 *
 * SNOW-658: `markCached` is optimistic, and for a grouped provider row that
 * is a lie. AT and IT share the ALBINA row, whose dot may only green once
 * both have landed — so a grouped row hands off to a real probe instead.
 *
 * Get either wrong and the failure is a dot that says a country is available
 * offline when it is not, which is the one thing that dashboard exists to
 * answer.
 *
 * SNOW-1186 added a third piece that belongs to the same load: WHEN a
 * country's merged geometry reaches its MapLibre source. See
 * `createSourceBatch` below.
 *
 * Exports (frozen `self.pwaCountryLoadCore`):
 *
 *   planCountryFeeds(options)
 *   syncDotAction(options)
 *   createSourceBatch(apply)
 */

(function () {
  'use strict';

  /**
   * Decide which of a country's four feeds this load must fetch.
   *
   * A feed is fetched when its URL is configured AND boot has not already
   * fetched it for this country. Only the boot country has anything already
   * fetched, and only two of its four feeds — the L4 geometry and the season
   * ratings. Its L1 and L2 are fetched here like anyone else's; see the
   * module header for the bug that came from assuming otherwise.
   *
   * @param {Object} options
   * @param {boolean} [options.isBootCountry] True for the country the map's
   *   own boot already fetched L4 geometry and season ratings for.
   * @param {boolean} [options.hasRegionsUrl] `REGIONS_URL` is configured.
   * @param {boolean} [options.hasMajorUrl] `MAJOR_REGIONS_URL` is configured.
   * @param {boolean} [options.hasSubUrl] `SUB_REGIONS_URL` is configured.
   * @param {boolean} [options.hasRatingsUrl] `RATINGS_URL` is configured.
   * @returns {{regions: boolean, major: boolean, sub: boolean,
   *   ratings: string}} `ratings` is `'fetch'` (request the season),
   *   `'ensure-cached'` (boot already has it in memory — only top up the SW
   *   cache) or `'skip'` (no URL configured).
   */
  function planCountryFeeds(options) {
    var opts = options || {};
    var isBoot = !!opts.isBootCountry;
    var ratings = 'skip';
    if (opts.hasRatingsUrl) {
      ratings = isBoot ? 'ensure-cached' : 'fetch';
    }
    return {
      // Skipped for the boot country: its L4 geometry is already in
      // `geojsonCache`, installed and painted.
      regions: !!opts.hasRegionsUrl && !isBoot,
      // NOT skipped for the boot country — SNOW-524.
      major: !!opts.hasMajorUrl,
      sub: !!opts.hasSubUrl,
      ratings: ratings,
    };
  }

  /**
   * Decide what the layers-menu sync dot should do after a country load.
   *
   * @param {Object} options
   * @param {boolean} [options.isBootCountry] As above. A feed skipped
   *   because boot already fetched it counts as landed.
   * @param {boolean} [options.regionsLoaded] The L4 feed returned features.
   * @param {boolean} [options.majorLoaded] The L1 feed returned features.
   * @param {boolean} [options.subLoaded] The L2 feed returned features.
   * @param {boolean} [options.ratingsOk] The ratings feed landed.
   * @param {number} [options.rowCountryCount] How many countries share this
   *   country's layers-menu row. ALBINA's row carries AT and IT, so 2.
   * @returns {string} `'mark-cached'` to green the row optimistically, or
   *   `'refresh'` to hand off to a real probe.
   */
  function syncDotAction(options) {
    var opts = options || {};
    // SNOW-524: a skipped feed was already fetched by boot, so it counts —
    // but ONLY the L4 geometry is ever skipped, and only for the boot
    // country. L1 and L2 are always fetched, so a missing one is always a
    // real gap however this country was loaded.
    var regionsOk = !!opts.isBootCountry || !!opts.regionsLoaded;
    var complete = regionsOk
      && !!opts.majorLoaded
      && !!opts.subLoaded
      && !!opts.ratingsOk;

    if (!complete) return 'refresh';

    // SNOW-658: `markCached` is optimistic, which is correct for a row that
    // stands for one country and a lie for one that stands for two. A
    // grouped row may only green once every country in it has landed, and
    // this load knows about one of them — so let the probe answer.
    return (opts.rowCountryCount || 1) === 1 ? 'mark-cached' : 'refresh';
  }

  /**
   * Hold source updates while several country loads land, then apply each
   * source once (SNOW-1186).
   *
   * Boot loads every boot country at once, and each one merged its geometry
   * into the shared cache and called `setData` straight away. Every
   * `setData` makes MapLibre's worker re-tile the WHOLE merged collection
   * and the map repaint, so three countries landing meant three full
   * re-tiles of an ever-larger set and a repaint after each. Under
   * Lighthouse's software WebGL a repaint is 0.4-0.6 s of main thread.
   *
   * `push` takes a READER, not the data: while held only the latest
   * reader per source is kept, and it is called at `release`, so the flush
   * applies the cache as it stands then, not as it stood at the first push.
   * Not held, `push` applies at once, which keeps a user's country toggle
   * exactly as immediate as it was. `release` applies every held source
   * even when one throws, then rethrows the first error.
   *
   * @param {function(string, *): void} apply Writes data into one source,
   *   by id. map.js passes `getSource(id)?.setData(data)`.
   * @returns {{hold: function(): void, push: function(string, function(): *): void,
   *   release: function(): void, isHeld: function(): boolean}}
   */
  function createSourceBatch(apply) {
    var held = false;
    var dirty = new Map();
    return {
      hold: function () { held = true; },
      push: function (id, read) {
        if (held) {
          dirty.set(id, read);
          return;
        }
        apply(id, read());
      },
      // Idempotent, so the timeout cap and the all-landed signal can both
      // call it without coordinating which went first.
      release: function () {
        if (!held) return;
        held = false;
        var pending = Array.from(dirty);
        dirty.clear();
        // One source failing must not leave the others stale: the queue is
        // already cleared, so an entry skipped here is never retried. Apply
        // every entry, then rethrow the first failure for the caller to log.
        var failure = null;
        pending.forEach(function (entry) {
          try {
            apply(entry[0], entry[1]());
          } catch (err) {
            if (!failure) failure = err;
          }
        });
        if (failure) throw failure;
      },
      isHeld: function () { return held; },
    };
  }

  self.pwaCountryLoadCore = Object.freeze({
    planCountryFeeds: planCountryFeeds,
    syncDotAction: syncDotAction,
    createSourceBatch: createSourceBatch,
  });
})();
