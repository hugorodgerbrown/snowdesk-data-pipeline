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
 * It works on areas as ``static/js/map.js``'s ``basemapDownloadedAreas()``
 * hands them over — ``{id, name?, bytes, savedAt}``, keyed by the id that
 * also names the area's Cache Storage bucket. ``name`` is always present
 * except for an orphaned bucket with no record at all (SNOW-612) —
 * SNOW-635 review: an unrenamed custom area's numbered default is filled
 * in there too, so this module never has to special-case it.
 *
 * That normalising step is SNOW-586's, and this surface consumes it rather
 * than reading storage itself, because a download is recorded in **two**
 * places: ``basemap.regions`` (an array, one entry per downloaded region,
 * keyed by ``region_id``) and (SNOW-635) ``basemap.customAreas`` (an
 * array too now — any number of user-framed areas, not just one). Neither
 * key is the bucket id — that is ``areaIdForRegion(region_id)`` or the
 * custom area's own minted id (``generateCustomAreaId`` /
 * ``isCustomAreaId``) — so a second reader here would have to re-derive
 * the mapping and would be free to drift from the eviction path that
 * already owns it.
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
 *   manageRows(areas, options)
 *     The ordered, labelled row model the sheet renders. SNOW-635: every
 *     row's ``label`` is ``area.name || id`` uniformly — an unrenamed
 *     custom area's numbered "Custom area N" default is filled into
 *     ``name`` upstream, by ``map.js``'s ``basemapDownloadedAreas()``
 *     (which has the translation catalogue this module does not), not
 *     built here.
 *   groupRowsByKind(rows)
 *     SNOW-645 review: manageRows' own rows partitioned into
 *     {region, custom} — the sheet's REGIONS / CUSTOM AREAS grouping —
 *     without re-sorting either group.
 *   reconcileAreas(recorded, storedAreaIds, bytesById)
 *     The recorded areas unioned with the pinned buckets actually on
 *     disk, so a download that failed partway is visible rather than
 *     stranded (SNOW-612).
 *   budgetSegments(areas)
 *     SNOW-645 review: the budget bar's segments, one per basemap
 *     actually stored (grouped and summed, not one per area), largest
 *     first with the keyless group always last.
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
   * @param {Array<{id: string, name?: string, bytes?: number,
   *   savedAt?: string, basemapKey?: string|null}>} areas Areas as
   *   ``map.js``'s ``basemapDownloadedAreas()`` normalises them — the union
   *   of the ``basemap.regions`` array and (SNOW-635) the
   *   ``basemap.customAreas`` array, each already keyed by the id that
   *   names its Cache Storage bucket. ``name`` is always populated for a
   *   non-orphaned area — a region's is stored by the download itself; an
   *   unrenamed custom area's default "Custom area N" is filled in by
   *   ``basemapDownloadedAreas()`` itself (SNOW-635 review — see that
   *   function's own comment for why the defaulting lives there and not
   *   here or in the DOM layer). Only ``reconcileAreas``' orphan entries
   *   (SNOW-612 — a bucket with no record at all) ever leave it unset.
   *   ``basemapKey`` (SNOW-645) is null on a record written before this
   *   ticket shipped, and always null on an orphan — both read as an
   *   unknown basemap, never a wrong one.
   * @param {{isCustomAreaId?: function(string): boolean}} [options]
   *   ``isCustomAreaId`` is ``pwaBasemapDownloadCore.isCustomAreaId`` — an
   *   area whose id it accepts is a user-framed download rather than a
   *   region. A predicate rather than a single id (SNOW-586's
   *   ``customAreaId`` string) because SNOW-635 gives every custom area
   *   its OWN id (``generateCustomAreaId``); asking the core rather than
   *   comparing against one fixed value keeps that id family private to
   *   the module that mints it, the same way the ``region-<id>`` format
   *   is never parsed out elsewhere either. There is no ``customLabel``
   *   option any more (SNOW-635 review) — a custom area's label comes
   *   from ``area.name`` like every other row's does, so this module no
   *   longer needs a caller-supplied fallback string for it.
   * @returns {Array<{id: string, kind: string, orphaned: boolean,
   *   label: string, renameable: boolean, bytes: number, savedAt: string,
   *   size: string, basemapKey: string}>}
   */
  function manageRows(areas, options) {
    var list = Array.isArray(areas) ? areas : [];
    var opts = options || {};
    var rows = [];

    for (var i = 0; i < list.length; i += 1) {
      var area = list[i];
      // No id means nothing could name its bucket, so nothing could
      // delete it — a row that cannot be acted on is worse than absent.
      if (!area || !area.id) continue;

      var id = String(area.id);
      var isCustom = typeof opts.isCustomAreaId === 'function' && opts.isCustomAreaId(id);
      // SNOW-635: a region's name is its real name; a custom area's own
      // name (stored or defaulted — see this function's own docstring) is
      // still something the user can override — REGIONS are never
      // renameable, and neither is an ORPHANED custom bucket (SNOW-612):
      // there is no record entry left to write a name onto, only a bare
      // bucket id.
      var renameable = isCustom && !area.orphaned;

      // Uniform for every row now: the record's own name (always present
      // except for an orphan — see the docstring) falls back to the id,
      // the only identifier left for something with no name at all.
      var label = area.name || id;

      var bytes = Number(area.bytes);
      if (!Number.isFinite(bytes) || bytes < 0) bytes = 0;

      rows.push({
        id: id,
        // SNOW-612: an orphaned bucket is still a region or a custom
        // area — `orphaned` says the record is missing, not that it is a
        // third kind of thing, so the sheet can label it without the
        // caller having to re-derive which it was.
        kind: isCustom ? 'custom' : 'region',
        orphaned: !!area.orphaned,
        label: String(label),
        renameable: renameable,
        bytes: bytes,
        savedAt: area.savedAt || '',
        size: formatMegabytes(bytes),
        // SNOW-645: absent on a record written before this ticket shipped,
        // or on an orphan (no record at all) — both read as '', never a
        // wrong basemap, so map_downloads_manager.js's buildRow removes
        // the swatch+name line rather than showing an unknown one.
        basemapKey: area.basemapKey || '',
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

  /**
   * Partition ``manageRows``' own rows into REGIONS and CUSTOM AREAS
   * (SNOW-645 review — Hugo's "grouped by kind" sheet redesign).
   *
   * A ``filter``, not a re-sort: each group keeps ``manageRows``' own
   * largest-first (then recency, then id) order, so a group's own biggest
   * area still leads it and the sheet's own render() never has to re-apply
   * that ordering itself.
   *
   * @param {Array<{kind: string}>} rows As ``manageRows`` returns them.
   * @returns {{region: Array<Object>, custom: Array<Object>}}
   */
  function groupRowsByKind(rows) {
    var list = Array.isArray(rows) ? rows : [];
    return {
      region: list.filter(function (row) {
        return row && row.kind !== 'custom';
      }),
      custom: list.filter(function (row) {
        return row && row.kind === 'custom';
      }),
    };
  }


  /**
   * The union of what is RECORDED as downloaded and what is actually
   * stored in Cache Storage (SNOW-612).
   *
   * A download that fails partway leaves its pinned bucket on disk with no
   * ``basemap.regions`` / ``basemap.customAreas`` record, because the
   * record is only written when a run completes. The byte budget never
   * counted that bucket and the manage sheet could not list it, so the
   * stranded quota was invisible to the user and to the planner —
   * accumulating silently across failed attempts until the origin ran out
   * of room.
   *
   * Reading the buckets is what makes the orphan visible; this is the
   * arithmetic half, kept here so it can be tested without Cache Storage.
   * The recorded entry always wins for an id present in both, because it
   * carries the name and the timestamp a bucket id cannot.
   *
   * @param {Array<{id: string, name?: string, bytes?: number,
   *   savedAt?: string}>} recorded Areas derived from the stored records.
   * @param {string[]} storedAreaIds Area ids with a pinned bucket present
   *   in Cache Storage, as ``map.js``'s ``pinnedBucketAreaIds()`` reads
   *   them back off ``caches.keys()``.
   * @param {Object<string, number>} [bytesById] Measured sizes for the
   *   orphans, keyed by area id. An orphan with no measurement counts as
   *   0 bytes — visible and deletable, but not guessed at.
   * @returns {Array<{id: string, name?: string, bytes: number,
   *   savedAt?: string, orphaned: boolean}>} The recorded areas in their
   *   original order, then any orphans, id-ascending so the sheet's own
   *   sort has a stable input.
   */
  function reconcileAreas(recorded, storedAreaIds, bytesById) {
    var list = Array.isArray(recorded) ? recorded : [];
    var stored = Array.isArray(storedAreaIds) ? storedAreaIds : [];
    var sizes = bytesById || {};

    var out = [];
    var known = Object.create(null);
    for (var i = 0; i < list.length; i += 1) {
      var area = list[i];
      if (!area || !area.id) continue;
      known[String(area.id)] = true;
      out.push({
        id: String(area.id),
        name: area.name,
        bytes: Number(area.bytes) || 0,
        savedAt: area.savedAt,
        orphaned: false,
        // SNOW-645: absent on a pre-SNOW-645 record — see manageRows.
        basemapKey: area.basemapKey || null,
      });
    }

    var orphans = [];
    for (var j = 0; j < stored.length; j += 1) {
      var id = stored[j] ? String(stored[j]) : '';
      if (!id || known[id]) continue;
      known[id] = true;
      var bytes = Number(sizes[id]);
      orphans.push({
        id: id,
        // No record means no stored name — the id is all there is, and
        // manageRows already falls back to it.
        name: undefined,
        bytes: Number.isFinite(bytes) && bytes > 0 ? bytes : 0,
        // No record means no completion timestamp either. Left empty
        // rather than stamped with "now", which would sort an orphan as
        // the newest download on a surface ordered partly by recency.
        savedAt: undefined,
        orphaned: true,
        // No record means no known basemap either.
        basemapKey: null,
      });
    }
    orphans.sort(function (a, b) {
      return a.id.localeCompare(b.id);
    });

    return out.concat(orphans);
  }

  /**
   * The budget bar's segments — one per basemap actually stored.
   *
   * SNOW-645 review: the bar used to be one flat fill for the whole used
   * total. Grouped by basemap here, one segment per KEY (summed across
   * however many areas share it), not one per area — a device with a
   * dozen small custom areas under the same basemap would otherwise
   * render a dozen slivers, which reads as noise rather than as "here is
   * what OpenFreeMap is costing you".
   *
   * @param {Array<{bytes?: number, basemapKey?: string|null}>} areas As
   *   ``manageRows`` returns them (or anything shaped the same — only
   *   ``bytes`` and ``basemapKey`` are read).
   * @returns {Array<{basemapKey: string, bytes: number}>} Sorted largest
   *   first, with the keyless group (legacy records, orphaned buckets —
   *   ``basemapKey`` falsy) always LAST regardless of its size. Stable
   *   across renders for the same underlying data: two groups of equal
   *   size never swap because sort itself is stable and the input order
   *   (``manageRows``' own largest-first, tie-broken by recency then id)
   *   is already deterministic. ``basemapKey`` is always a string, never
   *   ``null`` — ``''`` for the keyless group — so a caller can hand it
   *   straight to a ``data-basemap-key`` attribute or a MapLibre ``match``
   *   arm without an extra null check.
   */
  function budgetSegments(areas) {
    var list = Array.isArray(areas) ? areas : [];
    var totals = Object.create(null);
    var order = [];

    for (var i = 0; i < list.length; i += 1) {
      var area = list[i];
      var bytes = Number(area && area.bytes);
      if (!Number.isFinite(bytes) || bytes <= 0) continue;
      var key = (area && area.basemapKey) || '';
      if (!(key in totals)) {
        totals[key] = 0;
        order.push(key);
      }
      totals[key] += bytes;
    }

    var keyed = order.filter(function (key) {
      return key !== '';
    });
    keyed.sort(function (a, b) {
      return totals[b] - totals[a];
    });

    var segments = keyed.map(function (key) {
      return { basemapKey: key, bytes: totals[key] };
    });
    if ('' in totals) segments.push({ basemapKey: '', bytes: totals[''] });

    return segments;
  }

  self.pwaBasemapManageCore = Object.freeze({
    megabytesToBytes: megabytesToBytes,
    formatMegabytes: formatMegabytes,
    clampBudgetMb: clampBudgetMb,
    budgetSummary: budgetSummary,
    manageRows: manageRows,
    groupRowsByKind: groupRowsByKind,
    reconcileAreas: reconcileAreas,
    budgetSegments: budgetSegments,
    BUDGET_CHOICES_MB: BUDGET_CHOICES_MB,
    MIN_BUDGET_MB: MIN_BUDGET_MB,
    DEFAULT_BUDGET_MB: DEFAULT_BUDGET_MB,
    BYTES_PER_MB: BYTES_PER_MB,
  });
})();
