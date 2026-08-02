/*
 * static/js/basemap_manage_core.js — pure helpers behind the
 * "Manage downloads" sheet (SNOW-588).
 *
 * The sheet answers two questions the map could not: *what have I stored
 * offline on this device*, and *what is it costing me*. This module holds
 * every part of that answer which is arithmetic or ordering rather than
 * DOM — the same split ``basemap_download_core.js`` uses, and for the same
 * reason: ``static/js/map.js`` is one file of IIFEs with no module
 * boundary and is not unit-tested, so logic that matters has to live
 * somewhere a test can reach it. ``static/js/map_downloads_manager.js`` is
 * the DOM half and does nothing this module could do instead.
 *
 * It reads the SNOW-586 ``meta:app`` row ``basemap.areas`` — one array of
 * area records, each with an ``id``, a ``kind``, a ``bytes`` figure and a
 * ``savedAt``, keyed by the same ``id`` that names its Cache Storage
 * bucket. That record is written by SNOW-586 and only read here; the shape
 * is documented on both tickets and in ``docs/offline-map.md``.
 *
 * Sizes are the STORED byte figure, never a live measurement. An area is
 * thousands of cache entries, so summing them at render time would mean a
 * ``cache.match()`` per entry every time the sheet opens. SNOW-586 already
 * has to keep the standing total for ``planEviction``, so the number
 * exists and this surface consumes it rather than deriving a second,
 * subtly different one.
 *
 * Exports (frozen ``self.pwaBasemapManageCore``):
 *
 *   BUDGET_CHOICES_MB
 *     The offerable budgets, in megabytes. A fixed set of presets rather
 *     than a free-entry field or a slider: SNOW-586 settled that a budget
 *     should be "predictable and explainable", and four named sizes are
 *     legible in a way "you have chosen 743 MB" is not.
 *   MIN_BUDGET_MB
 *     The smallest offerable budget, pinned to
 *     ``basemap_download_core.js``'s ``DOWNLOAD_CEILING_MB`` — a standing
 *     budget below the per-run ceiling would make some individual
 *     downloads structurally impossible to keep, which is exactly the
 *     "two limits that do not talk to each other" bug SNOW-586 exists to
 *     fix. Re-declared rather than imported: these are two separate
 *     page-scope IIFEs with no module system between them, so the
 *     round-trip assertion in tests/js/test_basemap_manage_core.js is what
 *     keeps the two honest.
 *   megabytesToBytes(mb) / formatMegabytes(bytes)
 *     The MB ↔ byte conversion and its display form. "MB" here means
 *     1024×1024, matching ``basemap_download_core.js``'s ``buildBlob`` and
 *     ``apps/regions/services/basemap_tiles.py`` — every size the user has
 *     ever been shown for a download is computed that way, so a manage
 *     surface using decimal megabytes would report a different number for
 *     the same download.
 *   clampBudgetMb(mb)
 *     The nearest offerable budget to ``mb``, for reading a stored (or
 *     absent, or corrupt) ``basemap.budgetMb`` back.
 *   budgetSummary(areas, budgetBytes)
 *     Standing usage against the budget — the sheet's running total.
 *   manageRows(areas, labelFor)
 *     The ordered, labelled row model the sheet renders.
 */

(function () {
  'use strict';

  var BYTES_PER_MB = 1024 * 1024;

  // Mirrors basemap_download_core.js's DOWNLOAD_CEILING_MB. See the module
  // header for why this is a re-declaration rather than a read of that
  // module, and which test holds the two together.
  var MIN_BUDGET_MB = 200;

  // The offerable budgets. MIN_BUDGET_MB is the floor by construction (a
  // budget under the per-run ceiling could never hold a maximal download);
  // 500 is SNOW-586's default and the middle option rather than the
  // smallest, so the control reads as a dial with room in both directions
  // rather than a ladder the user starts at the bottom of.
  var BUDGET_CHOICES_MB = Object.freeze([MIN_BUDGET_MB, 500, 1000, 2000]);

  // SNOW-586's default, and the value clampBudgetMb falls back to when it
  // is handed something that is not a number at all.
  var DEFAULT_BUDGET_MB = 500;

  /**
   * ``mb`` megabytes as a byte count.
   *
   * @param {number} mb
   * @returns {number} ``0`` for anything non-finite or negative — a
   *   corrupt stored budget must never produce a NaN the caller then
   *   compares against, which would make every comparison false and read
   *   as "always under budget".
   */
  function megabytesToBytes(mb) {
    var n = Number(mb);
    if (!Number.isFinite(n) || n < 0) return 0;
    return n * BYTES_PER_MB;
  }

  /**
   * ``bytes`` as a display string in megabytes.
   *
   * One decimal place below 100 MB and none at or above it: the decimal
   * is what distinguishes two small downloads from each other, and is
   * noise on a figure in the hundreds. Rounds to nearest rather than up —
   * ``buildBlob`` rounds a download's ESTIMATE up (an estimate that
   * under-reads is the harmful direction), but this reports a size already
   * on disk, where nearest is simply the truthful answer.
   *
   * Sub-megabyte sizes report as ``< 0.1 MB`` rather than ``0.0 MB``, so a
   * real download never renders as costing nothing.
   *
   * @param {number} bytes
   * @returns {string}
   */
  function formatMegabytes(bytes) {
    var n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return '0 MB';
    var mb = n / BYTES_PER_MB;
    if (mb < 0.1) return '< 0.1 MB';
    if (mb < 100) return mb.toFixed(1) + ' MB';
    return String(Math.round(mb)) + ' MB';
  }

  /**
   * The offerable budget nearest to ``mb``.
   *
   * Snapping to a choice rather than clamping to a range is what lets the
   * stored ``basemap.budgetMb`` row be read straight into a ``<select>``
   * without a "no option matches" state: an absent row, a hand-edited one,
   * or one written by a future build offering different presets all
   * resolve to something the control can actually show. Ties go to the
   * larger choice — rounding a user's budget DOWN without being asked is
   * the direction that silently evicts their downloads.
   *
   * ``null`` and ``''`` are rejected BEFORE the numeric coercion, not
   * left to it. ``Number(null)`` and ``Number('')`` are both ``0``, which
   * is perfectly finite and would snap to the SMALLEST offered budget —
   * so an unset or blanked-out row would hand the user the minimum
   * instead of the default, shrinking their budget by the same unasked
   * step the tie-break rule above exists to prevent.
   *
   * @param {number} mb
   * @returns {number} One of ``BUDGET_CHOICES_MB``.
   */
  function clampBudgetMb(mb) {
    if (mb === null || mb === undefined || mb === '') return DEFAULT_BUDGET_MB;
    var n = Number(mb);
    if (!Number.isFinite(n)) return DEFAULT_BUDGET_MB;
    var best = BUDGET_CHOICES_MB[0];
    var bestDistance = Math.abs(n - best);
    for (var i = 1; i < BUDGET_CHOICES_MB.length; i += 1) {
      var choice = BUDGET_CHOICES_MB[i];
      var distance = Math.abs(n - choice);
      if (distance <= bestDistance) {
        best = choice;
        bestDistance = distance;
      }
    }
    return best;
  }

  /**
   * Standing usage across every stored area, against the budget.
   *
   * @param {Array<{bytes?: number}>} areas The ``basemap.areas`` record.
   *   Best-effort: a malformed entry counts as ``0`` rather than poisoning
   *   the total with ``NaN``.
   * @param {number} budgetBytes
   * @returns {{usedBytes: number, budgetBytes: number, freeBytes: number,
   *   pct: number, overBudget: boolean, count: number}} ``pct`` is clamped
   *   to 0–100 so it can drive a bar's width directly; ``overBudget``
   *   carries the un-clamped truth, since a total above budget must still
   *   be sayable once the bar has run out of room to say it. ``freeBytes``
   *   floors at 0 for the same reason.
   */
  function budgetSummary(areas, budgetBytes) {
    var list = Array.isArray(areas) ? areas : [];
    var budget = Number(budgetBytes);
    if (!Number.isFinite(budget) || budget < 0) budget = 0;

    var used = 0;
    for (var i = 0; i < list.length; i += 1) {
      var bytes = Number(list[i] && list[i].bytes);
      if (Number.isFinite(bytes) && bytes > 0) used += bytes;
    }

    var pct = budget > 0 ? (used / budget) * 100 : 0;
    return {
      usedBytes: used,
      budgetBytes: budget,
      freeBytes: Math.max(0, budget - used),
      pct: Math.max(0, Math.min(100, pct)),
      overBudget: used > budget,
      count: list.length,
    };
  }

  /**
   * The ordered, labelled rows the sheet renders.
   *
   * Ordered **largest first**. The surface exists so a user can decide
   * what to remove, and the download worth removing is the big one;
   * newest-first would instead order by an axis the user is not choosing
   * along. ``savedAt`` (newest first) then ``id`` break ties, so a set of
   * equal-sized areas renders in a stable order across re-opens rather
   * than reshuffling on every render.
   *
   * @param {Array<Object>} areas The ``basemap.areas`` record.
   * @param {function(Object): string} [labelFor] Resolves an area record
   *   to its display name — on the map that reads ``FEATURE_BY_REGION_ID``,
   *   which is why this is injected rather than looked up here. A missing
   *   resolver, or one returning nothing, falls back to the area's own
   *   ``region_id``, and finally its ``id``: a row whose name cannot be
   *   resolved (a region retired upstream, or a ``regions.geojson`` that
   *   is not cached) must still be listed and still be deletable — a
   *   download the user cannot see is the bug this ticket is fixing.
   * @returns {Array<{id: string, kind: string, label: string,
   *   bytes: number, savedAt: string, size: string}>}
   */
  function manageRows(areas, labelFor) {
    var list = Array.isArray(areas) ? areas : [];
    var rows = [];

    for (var i = 0; i < list.length; i += 1) {
      var area = list[i];
      if (!area || !area.id) continue;

      var label = '';
      if (typeof labelFor === 'function') {
        try {
          label = labelFor(area) || '';
        } catch (_err) {
          // A throwing resolver is treated as an unresolved name, not as a
          // reason to drop the row.
          label = '';
        }
      }
      if (!label) label = area.region_id || area.id;

      var bytes = Number(area.bytes);
      if (!Number.isFinite(bytes) || bytes < 0) bytes = 0;

      rows.push({
        id: String(area.id),
        kind: area.kind === 'custom' ? 'custom' : 'region',
        label: String(label),
        bytes: bytes,
        savedAt: area.savedAt || '',
        size: formatMegabytes(bytes),
      });
    }

    rows.sort(function (a, b) {
      if (a.bytes !== b.bytes) return b.bytes - a.bytes;
      var ta = Date.parse(a.savedAt) || 0;
      var tb = Date.parse(b.savedAt) || 0;
      if (ta !== tb) return tb - ta;
      return a.id.localeCompare(b.id);
    });

    return rows;
  }

  self.pwaBasemapManageCore = Object.freeze({
    megabytesToBytes: megabytesToBytes,
    formatMegabytes: formatMegabytes,
    clampBudgetMb: clampBudgetMb,
    budgetSummary: budgetSummary,
    manageRows: manageRows,
    BUDGET_CHOICES_MB: BUDGET_CHOICES_MB,
    MIN_BUDGET_MB: MIN_BUDGET_MB,
    DEFAULT_BUDGET_MB: DEFAULT_BUDGET_MB,
    BYTES_PER_MB: BYTES_PER_MB,
  });
})();
