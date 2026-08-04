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
 * Exports (frozen ``self.pwaSearchCore``):
 *
 *   MAX_RESULTS
 *     How many rows the dropdown shows. Capped so it stays usable on a
 *     narrow viewport rather than running off the bottom of the map.
 *   normalise(text)
 *     Lowercase, NFD-decomposed, combining marks stripped — so "Évolène"
 *     matches "evolene" and "Graubünden" matches "graubunden".
 *   buildEntry(props, resorts)
 *     One search-index entry for a region, or ``null`` for props with no
 *     ``regionID``.
 *   runSearch(index, query, maxResults)
 *     The matching rows, ordered and capped.
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
   * Build one search-index entry for a region.
   *
   * Resort names are folded into the searchable string so a query for
   * "Verbier" returns its parent region row, but they are deliberately
   * NOT surfaced in the rendered row: the resort list is either empty
   * (AT/IT/FR) or long enough that truncating it adds noise, so the
   * secondary line shows the parent L2 sub-region name instead.
   *
   * @param {{regionID?: string, name?: string, subregion_name?: string}} props
   *   A region feature's properties.
   * @param {string[]} [resorts] Resort names attached to the region.
   * @returns {{primary: string, secondary: string, subregionName: string,
   *   regionID: string, searchable: string} | null} ``null`` when there is
   *   no ``regionID`` — an entry with nothing to select is worse than
   *   absent.
   */
  function buildEntry(props, resorts) {
    if (!props || !props.regionID) return null;
    var regionID = props.regionID;
    var name = props.name || regionID;
    // The L2 parent's English name (e.g. "Lower Valais" for CH-4115).
    // Blank for AT/IT, where the fixtures store the prefix as a
    // placeholder and the API boundary suppresses it.
    var subregionName = props.subregion_name || '';
    var names = [name, regionID, subregionName].concat(resorts || []);
    return {
      primary: name,
      secondary: regionID,
      subregionName: subregionName,
      regionID: regionID,
      searchable: normalise(names.join(' ')),
    };
  }

  /**
   * The entries matching ``query``, ordered and capped.
   *
   * Ordered by EAWS region id so results group by country (AT-… before
   * CH-… before FR-… before IT-…) and run in order within each — the
   * alternative, relevance ranking, would reshuffle the list as the user
   * types, which is worse for a list they are aiming at with a keyboard.
   *
   * @param {Array<{searchable: string, regionID: string}>} index
   * @param {string} query Raw user input; normalised here.
   * @param {number} [maxResults] Defaults to ``MAX_RESULTS``.
   * @returns {Array<Object>} Empty for a blank query — an empty box must
   *   not read as "no matches".
   */
  function runSearch(index, query, maxResults) {
    var q = normalise(query).trim();
    if (!q) return [];
    var cap = typeof maxResults === 'number' ? maxResults : MAX_RESULTS;
    var hits = (index || []).filter(function (item) {
      return item && item.searchable && item.searchable.indexOf(q) !== -1;
    });
    hits.sort(function (a, b) {
      return a.regionID.localeCompare(b.regionID);
    });
    return hits.slice(0, cap);
  }

  self.pwaSearchCore = Object.freeze({
    MAX_RESULTS: MAX_RESULTS,
    normalise: normalise,
    buildEntry: buildEntry,
    runSearch: runSearch,
  });
})();
