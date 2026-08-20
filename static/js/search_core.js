/*
 * static/js/search_core.js — Pure region/resort search helpers (SNOW-618).
 *
 * The map's search box is in-memory autocomplete over region names, EAWS
 * ids, parent sub-region names and resort names — every one of which is
 * already resident after the initial load, so there is no round trip and
 * no index worth worrying about (a few hundred entries).
 *
 * Extracted from ``static/js/map.js``'s main IIFE following the same
 * ``*_core.js`` pattern ``scrubber_core.js`` and ``basemap_download_core.js``
 * already use, and for the same reason: ``map.js`` is one file of IIFEs
 * with no module boundary and cannot be imported under jsdom, so logic
 * that matters has to live somewhere a test can reach it. What stayed
 * behind in ``map.js`` is the part that isn't logic — the index itself,
 * the DOM, the keyboard handling and the map-preview highlighting.
 *
 * Every export is a pure function of its arguments. Nothing here touches
 * the DOM, the map, or module-level state.
 *
 * ## Resorts are rows again (SNOW-697)
 *
 * SNOW-188 collapsed the index to one row per region and folded resort
 * names into that row's searchable string: a query for "Verbier" selected
 * the parent region but never showed the word it matched on. The stated
 * reason was the resort data — lists that were "either empty (AT/IT/FR) or
 * long enough that truncating them adds noise". That was true of the data
 * at the time. It isn't now: ``kind`` separates lift-served resorts from
 * touring terrain, ``/api/resorts-by-region/`` filters to the former, and
 * the curated sheet has been through several passes since.
 *
 * So a resort is its own row again, as it was before SNOW-188 — its own
 * name on the primary line, its parent region on the secondary line — and
 * region rows no longer fold resort names in, because the resort row is
 * the better answer to a resort query. Everything else SNOW-188 added is
 * kept: the region-id badge, the L2 name on region rows, and matching on
 * both.
 *
 * Exports (frozen ``self.pwaSearchCore``):
 *
 *   MAX_RESULTS
 *     How many rows the dropdown shows. Capped so it stays usable on a
 *     narrow viewport rather than running off the bottom of the map.
 *   normalise(text)
 *     Lowercase, NFD-decomposed, combining marks stripped — so "Évolène"
 *     matches "evolene" and "Graubünden" matches "graubunden".
 *   buildRegionEntry(props)
 *     The region row, or ``null`` for props with no ``regionID``.
 *   buildResortEntries(props, resorts)
 *     A row per resort in the region, possibly empty.
 *   buildEntries(props, resorts)
 *     Both of the above in one call — what ``map.js`` indexes with.
 *   runSearch(index, query, maxResults)
 *     The matching rows, ranked and capped.
 *
 * Every entry, whichever builder made it, has the same shape::
 *
 *   { type, primary, secondary, primaryKey, regionID, searchable }
 *
 * ``regionID`` is always the PARENT region — it is what the row selects on
 * the map and what the badge shows, for a resort row as much as a region
 * one. Picking "Verbier" selects CH-4115, exactly as picking the region
 * would; the row is a better label for that destination, not a different
 * destination.
 */

(function () {
  'use strict';

  // Capped for the dropdown's sake, not the search's — matching is over a
  // few hundred entries and the cost of a wider cap would be a list the
  // user has to scroll past the map to read.
  var MAX_RESULTS = 8;

  /**
   * Fold ``text`` for accent- and case-insensitive matching.
   *
   * NFD splits an accented character into its base letter plus a
   * combining mark, and the range below strips those marks — so a query
   * typed without accents still matches the name that has them, which is
   * most of what a visitor to an Alpine map will type.
   *
   * @param {string} text
   * @returns {string}
   */
  function normalise(text) {
    return String(text == null ? '' : text)
      .toLowerCase()
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '');
  }

  /**
   * Build the search-index row for a region.
   *
   * The secondary line is the parent L2 sub-region name (e.g. "Lower
   * Valais" for CH-4115) — blank for AT/IT, where the fixtures store the
   * prefix as a placeholder and the API boundary suppresses it.
   *
   * Resort names are NOT folded in here. Before SNOW-697 they were, which
   * was the only way a resort query could find anything; now the resort
   * has its own row, folding would return two rows for one query that
   * both select the same region.
   *
   * @param {{regionID?: string, name?: string, subregion_name?: string}} props
   *   A region feature's properties.
   * @returns {{type: string, primary: string, secondary: string,
   *   primaryKey: string, regionID: string, searchable: string} | null}
   *   ``null`` when there is no ``regionID`` — an entry with nothing to
   *   select is worse than absent.
   */
  function buildRegionEntry(props) {
    if (!props || !props.regionID) return null;
    var regionID = props.regionID;
    var name = props.name || regionID;
    var subregionName = props.subregion_name || '';
    return {
      type: 'region',
      primary: name,
      secondary: subregionName,
      primaryKey: normalise(name),
      regionID: regionID,
      searchable: normalise([name, regionID, subregionName].join(' ')),
    };
  }

  /**
   * Build one search-index row per resort in a region.
   *
   * The resort's own name is the primary line and its parent region's name
   * the secondary, so a list of resorts sharing a first word ("Val …")
   * still reads unambiguously. ``regionID`` is the parent's, because that
   * is what the row selects.
   *
   * The parent's name and id go into ``searchable`` but not ``primaryKey``,
   * which puts them in the bottom score band of ``runSearch``: a query for
   * a region lists that region first and its resorts under it, so the box
   * answers "what is in here?" as well as "where is it?" without those
   * resorts ever outranking a row matched on its own name.
   *
   * A resort whose name matches its region's is skipped: "Davos" the
   * resort in "Davos" the region would render as a near-duplicate row
   * selecting exactly what the region row already selects. This is the
   * case SNOW-188's type badge existed to disambiguate — cheaper to not
   * emit the row at all.
   *
   * @param {{regionID?: string, name?: string}} props A region feature's
   *   properties — the parent of every resort in ``resorts``.
   * @param {string[]} [resorts] Resort names attached to the region.
   * @returns {Array<Object>} Possibly empty: a region with no resorts, no
   *   ``regionID``, or only a same-named resort yields no rows.
   */
  function buildResortEntries(props, resorts) {
    if (!props || !props.regionID) return [];
    var regionID = props.regionID;
    var regionName = props.name || regionID;
    var regionKey = normalise(regionName);
    var entries = [];
    (resorts || []).forEach(function (resortName) {
      if (!resortName) return;
      var primaryKey = normalise(resortName);
      if (primaryKey === regionKey) return;
      entries.push({
        type: 'resort',
        primary: resortName,
        secondary: regionName,
        primaryKey: primaryKey,
        regionID: regionID,
        searchable: normalise([resortName, regionName, regionID].join(' ')),
      });
    });
    return entries;
  }

  /**
   * Every row a region contributes: the region itself, then its resorts.
   *
   * The order matters only as a tie-break input — ``runSearch`` ranks —
   * but keeping the region first means an unranked read of the index
   * (a test, a console dump) groups sensibly.
   *
   * @param {{regionID?: string, name?: string, subregion_name?: string}} props
   * @param {string[]} [resorts] Resort names attached to the region.
   * @returns {Array<Object>} Empty when ``props`` has no ``regionID``.
   */
  function buildEntries(props, resorts) {
    var region = buildRegionEntry(props);
    if (!region) return [];
    return [region].concat(buildResortEntries(props, resorts));
  }

  /**
   * How well ``entry`` answers ``q``: lower is better, -1 is no match.
   *
   * Three bands, and the gaps between them are what the ranking is for:
   * a row whose own name starts with what you typed beats one that merely
   * contains it, which beats one that matched on something you cannot see
   * in the row at all (its region id, its L2, its parent's name).
   *
   * @param {{primaryKey: string, searchable: string}} entry
   * @param {string} q Already normalised and trimmed.
   * @returns {number} 0, 1, 2, or -1 for no match.
   */
  function matchScore(entry, q) {
    var idx = entry.primaryKey.indexOf(q);
    if (idx === 0) return 0;
    if (idx > 0) return 1;
    return entry.searchable.indexOf(q) !== -1 ? 2 : -1;
  }

  /**
   * The entries matching ``query``, ranked and capped.
   *
   * SNOW-188 sorted by EAWS region id, on the reasoning that relevance
   * ranking "would reshuffle the list as the user types, which is worse
   * for a list they are aiming at with a keyboard". With one row per
   * region that held. It does not survive resort rows (SNOW-697): there
   * are ~140 of them against ~75 regions, they sort under their parent's
   * id rather than their own name, and ``MAX_RESULTS`` is 8 — so an exact
   * match on a resort name could sit below eight unrelated regions and
   * never render at all. A list that cannot show you what you typed is
   * worse than one that reorders.
   *
   * The keyboard concern is answered rather than dismissed: ranking is a
   * pure function of the query, ties break deterministically (name, then
   * region id), and each extra character only narrows the set — so the
   * best match stays pinned at the top instead of drifting down it.
   *
   * @param {Array<{primaryKey: string, searchable: string, primary: string,
   *   regionID: string}>} index
   * @param {string} query Raw user input; normalised here.
   * @param {number} [maxResults] Defaults to ``MAX_RESULTS``.
   * @returns {Array<Object>} Empty for a blank query — an empty box must
   *   not read as "no matches".
   */
  function runSearch(index, query, maxResults) {
    var q = normalise(query).trim();
    if (!q) return [];
    var cap = typeof maxResults === 'number' ? maxResults : MAX_RESULTS;
    var scored = [];
    (index || []).forEach(function (item) {
      if (!item || !item.searchable || !item.primaryKey) return;
      var score = matchScore(item, q);
      if (score === -1) return;
      scored.push({ item: item, score: score });
    });
    scored.sort(function (a, b) {
      return (
        a.score - b.score ||
        a.item.primary.localeCompare(b.item.primary) ||
        a.item.regionID.localeCompare(b.item.regionID)
      );
    });
    return scored.slice(0, cap).map(function (scoredItem) {
      return scoredItem.item;
    });
  }

  self.pwaSearchCore = Object.freeze({
    MAX_RESULTS: MAX_RESULTS,
    normalise: normalise,
    buildRegionEntry: buildRegionEntry,
    buildResortEntries: buildResortEntries,
    buildEntries: buildEntries,
    runSearch: runSearch,
  });
})();
