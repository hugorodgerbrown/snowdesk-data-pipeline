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
 *   groupRowsByBasemap(rows, order)
 *     SNOW-832: manageRows' own rows grouped by the BASEMAP each was
 *     downloaded under, in the picker's own order — the sheet's group
 *     headings — without re-sorting any group. Replaces SNOW-645's
 *     groupRowsByKind; a row's kind moved down into its own meta line.
 *   reconcileAreas(recorded, storedAreaIds, bytesById, accountAreas)
 *     The recorded areas unioned with the pinned buckets actually on
 *     disk, so a download that failed partway is visible rather than
 *     stranded (SNOW-612), and (SNOW-749) with the areas on the user's
 *     ACCOUNT, so an area downloaded on another device — or evicted here
 *     to make room — is listed as a one-tap re-download rather than
 *     vanishing. Every entry carries ``onDevice`` and ``synced``; with no
 *     account list the output is what it always was.
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
   * Ordered **regions first, then custom areas, alphabetically within
   * each** (SNOW-832), tie-broken by ``id`` so a set of identically-named
   * areas renders in a stable order across re-opens.
   *
   * It was largest-first through SNOW-831, on the reasoning that the
   * surface exists so a user can decide what to remove and the download
   * worth removing is the big one. Hugo's handoff settles it the other
   * way, and the reason is that the sheet stopped being a list: it is
   * grouped by basemap now, so a row is FOUND before it is judged, and a
   * list a reader has to scan for a name cannot be ordered by a number.
   * The sizes are still on every row, and ``budgetSegments()`` re-sorts by
   * bytes itself for the bar, so nothing that reads by size lost its
   * ordering — only the list did.
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
   *   unknown basemap, never a wrong one. ``onDevice`` / ``synced`` /
   *   ``bbox`` / ``regionId`` (SNOW-749) come from ``reconcileAreas``;
   *   an area list built without it reads as on-device and unsynced,
   *   which is what every such list was before that ticket.
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
   *   size: string, basemapKey: string, onDevice: boolean,
   *   synced: boolean, redownloadable: boolean, bbox: number[]|null,
   *   regionId: string}>} SNOW-749: ``onDevice`` and ``synced`` are the
   *   two independent facts a row now carries, and the four combinations
   *   are all reachable — on both, on the device only (a download made
   *   before the account gate, or one whose sync has not drained yet),
   *   on the account only (another device's, or one evicted here), and
   *   neither (an orphaned bucket on a signed-out device).
   *   ``redownloadable`` is the account-only row's own affordance: the
   *   sheet offers "Download here" instead of a size, and it is false
   *   unless the row carries enough to act on — a region id, or a bbox.
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
      // SNOW-749: default true, so an area list built before this ticket
      // (or by a caller that does not reconcile) reads as on-device —
      // which is what it was.
      var onDevice = area.onDevice !== false;
      // SNOW-635: a region's name is its real name; a custom area's own
      // name (stored or defaulted — see this function's own docstring) is
      // still something the user can override — REGIONS are never
      // renameable, and neither is an ORPHANED custom bucket (SNOW-612):
      // there is no record entry left to write a name onto, only a bare
      // bucket id.
      //
      // SNOW-749: nor is an ACCOUNT-ONLY row. The rename this flag gates
      // is the local one (`renameCustomArea` writes to
      // `basemap.customAreas`), and there is no local record to write to
      // for an area this device has never downloaded — the editor would
      // accept a name and drop it. Renaming it where it does exist is a
      // download away.
      var renameable = isCustom && !area.orphaned && onDevice;

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
        // SNOW-749: the two facts the sheet paints four row states from.
        // `synced` says the account knows about this area, which is NOT
        // the same as it being available offline — the sheet must never
        // let it green a sync dot.
        onDevice: onDevice,
        synced: !!area.synced,
        // Only an account-only row is offered a download; one already on
        // the device has nothing to fetch. A region needs its id, a
        // custom area its box — without either there is nothing to hand
        // the download control, so the affordance is withheld rather than
        // rendered dead.
        redownloadable:
          !onDevice && (isCustom ? Array.isArray(area.bbox) : !!area.regionId),
        bbox: Array.isArray(area.bbox) ? area.bbox : null,
        regionId: area.regionId || '',
      });
    }

    // SNOW-832: kind, then name. See this function's own docstring for why
    // the size axis stopped being the ordering one.
    rows.sort(function (a, b) {
      if (a.kind !== b.kind) return a.kind === 'custom' ? 1 : -1;
      var byLabel = a.label.localeCompare(b.label);
      if (byLabel !== 0) return byLabel;
      return a.id.localeCompare(b.id);
    });

    return rows;
  }

  /**
   * ``manageRows``' own rows grouped by the BASEMAP each was downloaded
   * under (SNOW-832 — Hugo's "group by basemap, not by kind" handoff).
   *
   * This replaces ``groupRowsByKind``. SNOW-645 split the sheet under two
   * fixed REGIONS / CUSTOM AREAS headings and carried the basemap as each
   * row's subtitle; the handoff turns that inside out, because the basemap
   * is the axis along which a download is or is not usable — switching
   * basemap is switching to a map you have not stored — while the kind is
   * merely how it was framed, which is why the kind moves down into the
   * row's own meta line beside the size.
   *
   * A grouping, not a re-sort: each group keeps ``manageRows``' own
   * regions-then-custom, alphabetical order, so the sheet's ``render()``
   * never has to re-apply that ordering itself.
   *
   * @param {Array<{kind: string, basemapKey?: string, bytes?: number}>} rows
   *   As ``manageRows`` returns them.
   * @param {string[]} [order] The canonical basemap order — the picker's
   *   own, via ``map_basemap_downloads.js``'s ``basemapOrder()``, so the
   *   sheet lists basemaps in the order the user is offered them rather
   *   than in whatever order they happen to have been downloaded. A key
   *   the order does not mention still gets its group (a deployment
   *   ``BASEMAP=`` override, or a style since retired from the picker):
   *   dropping the rows would hide real downloads, so those groups follow
   *   the known ones, in first-appearance order.
   * @returns {Array<{basemapKey: string, rows: Array<Object>,
   *   totalBytes: number}>} One entry per basemap that actually has rows —
   *   an empty group is never returned, because a heading with nothing
   *   under it says a basemap has downloads when it has none. The KEYLESS
   *   group (``basemapKey: ''`` — a record written before SNOW-645, an
   *   orphaned bucket, an account row with no basemap) is always LAST,
   *   whatever the order says: it is the group that cannot be named, so it
   *   is the one to read after the ones that can. ``totalBytes`` is what
   *   THIS DEVICE holds for the group — an account-only row contributes 0,
   *   so a group's total can legitimately read smaller than its row count
   *   suggests.
   */
  function groupRowsByBasemap(rows, order) {
    var list = Array.isArray(rows) ? rows : [];
    var canonical = Array.isArray(order) ? order : [];

    var byKey = Object.create(null);
    var seen = [];
    for (var i = 0; i < list.length; i += 1) {
      var row = list[i];
      if (!row) continue;
      var key = row.basemapKey || '';
      if (!(key in byKey)) {
        byKey[key] = { basemapKey: key, rows: [], totalBytes: 0 };
        seen.push(key);
      }
      byKey[key].rows.push(row);
      var bytes = Number(row.bytes);
      if (Number.isFinite(bytes) && bytes > 0) byKey[key].totalBytes += bytes;
    }

    // Canonical order first, then anything the order does not mention in
    // the order it was met, then the keyless group. Built as a list of
    // keys rather than by sorting the groups, so "not in the order at all"
    // needs no sentinel index to stand in for a position it does not have.
    var ordered = [];
    var taken = Object.create(null);
    for (var c = 0; c < canonical.length; c += 1) {
      var known = canonical[c];
      if (!known || known === '' || taken[known] || !(known in byKey)) continue;
      taken[known] = true;
      ordered.push(byKey[known]);
    }
    for (var s = 0; s < seen.length; s += 1) {
      var key2 = seen[s];
      if (key2 === '' || taken[key2]) continue;
      taken[key2] = true;
      ordered.push(byKey[key2]);
    }
    if ('' in byKey) ordered.push(byKey['']);

    return ordered;
  }

  /**
   * The union of what is RECORDED as downloaded, what is actually stored
   * in Cache Storage (SNOW-612), and what is on the user's ACCOUNT
   * (SNOW-749).
   *
   * Three sources, one keyed join on the area id — the id that names the
   * Cache Storage bucket and is also what the server stores verbatim, so
   * there is no id mapping to maintain anywhere.
   *
   * **The record.** A download that fails partway leaves its pinned bucket
   * on disk with no ``basemap.regions`` / ``basemap.customAreas`` record,
   * because the record is only written when a run completes. The byte
   * budget never counted that bucket and the manage sheet could not list
   * it, so the stranded quota was invisible to the user and to the
   * planner — accumulating silently across failed attempts until the
   * origin ran out of room.
   *
   * **The account.** An area the user downloaded on another device, or
   * downloaded here and later evicted to make room, has a row on the
   * account and nothing on this device. It becomes an entry with
   * ``onDevice: false`` and ``bytes: 0``, carrying the account row's own
   * name, basemap and geometry — enough for the sheet to offer it as a
   * one-tap re-download. It is NOT ``orphaned``: an orphan is a failed
   * download's leftovers on THIS device, which is a different fact with a
   * different remedy.
   *
   * Reading the buckets and the account is what makes both visible; this
   * is the arithmetic half, kept here so it can be tested without Cache
   * Storage or a network.
   *
   * Precedence is local-first for everything except ``synced``. The
   * recorded entry always wins for an id present in more than one source,
   * because it carries the name, the timestamp and the byte figure a
   * bucket id cannot and an account row does not have.
   *
   * With ``accountAreas`` empty or absent the output is what it was before
   * SNOW-749 plus two flags — ``onDevice: true`` and ``synced: false`` on
   * every entry, which is the truth for a device whose account list could
   * not be read. That is the anonymous path, the flag-off path and the
   * offline path, and it is the one that must not move.
   *
   * @param {Array<{id: string, name?: string, bytes?: number,
   *   savedAt?: string, basemapKey?: string|null, bbox?: number[]|null,
   *   regionId?: string}>} recorded Areas derived from the stored records.
   * @param {string[]} storedAreaIds Area ids with a pinned bucket present
   *   in Cache Storage, as ``map.js``'s ``pinnedBucketAreaIds()`` reads
   *   them back off ``caches.keys()``.
   * @param {Object<string, number>} [bytesById] Measured sizes for the
   *   orphans, keyed by area id. An orphan with no measurement counts as
   *   0 bytes — visible and deletable, but not guessed at.
   * @param {Array<{area_id: string, name?: string, basemap_key?: string,
   *   bbox?: number[]|null, region_id?: string, created_at?: string}>}
   *   [accountAreas] The rows on the user's account, in the server's own
   *   JSON shape (``downloads/areas.json``) — snake_case, because
   *   translating it in the transport layer would put the same mapping in
   *   two places. Absent for an anonymous, offline or flag-off page.
   * @returns {Array<{id: string, name?: string, bytes: number,
   *   savedAt?: string, orphaned: boolean, basemapKey: string|null,
   *   onDevice: boolean, synced: boolean, bbox: number[]|null,
   *   regionId: string}>} The recorded areas in their original order, then
   *   any orphans id-ascending, then any account-only areas id-ascending —
   *   so the sheet's own sort has a stable input.
   */
  function reconcileAreas(recorded, storedAreaIds, bytesById, accountAreas) {
    var list = Array.isArray(recorded) ? recorded : [];
    var stored = Array.isArray(storedAreaIds) ? storedAreaIds : [];
    var sizes = bytesById || {};
    var account = Array.isArray(accountAreas) ? accountAreas : [];

    // The account rows, keyed by area id. Built first because every
    // branch below asks it the same question — "does the account know
    // about this one" — and it is what decides `synced`.
    var accountById = Object.create(null);
    for (var a = 0; a < account.length; a += 1) {
      var row = account[a];
      if (!row || !row.area_id) continue;
      accountById[String(row.area_id)] = row;
    }

    var out = [];
    var known = Object.create(null);
    for (var i = 0; i < list.length; i += 1) {
      var area = list[i];
      if (!area || !area.id) continue;
      var recordedId = String(area.id);
      known[recordedId] = true;
      out.push({
        id: recordedId,
        name: area.name,
        bytes: Number(area.bytes) || 0,
        savedAt: area.savedAt,
        orphaned: false,
        // SNOW-645: absent on a pre-SNOW-645 record — see manageRows.
        basemapKey: area.basemapKey || null,
        // SNOW-749: the tiles are here.
        onDevice: true,
        synced: recordedId in accountById,
        // SNOW-749: what a re-download on another device would need. Null
        // for a region, whose tiles are computed server-side from its real
        // boundary — a box would be a second, coarser answer to a question
        // already answered.
        bbox: Array.isArray(area.bbox) ? area.bbox : null,
        regionId: area.regionId || '',
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
        // The bucket is on this device, whatever state it is in — which is
        // exactly why the sheet has to be able to delete it.
        onDevice: true,
        synced: id in accountById,
        bbox: null,
        regionId: '',
      });
    }
    orphans.sort(function (a2, b2) {
      return a2.id.localeCompare(b2.id);
    });

    // SNOW-749: whatever the account knows about that this device does
    // not. Sorted by id for the same stable-render reason as the orphans,
    // and appended after them so the two never interleave — they are
    // different kinds of "not a normal download" and the sheet labels them
    // differently.
    var accountOnly = [];
    for (var k = 0; k < account.length; k += 1) {
      var accountRow = account[k];
      if (!accountRow || !accountRow.area_id) continue;
      var accountId = String(accountRow.area_id);
      if (known[accountId]) continue;
      known[accountId] = true;
      accountOnly.push({
        id: accountId,
        name: accountRow.name || undefined,
        // Nothing of it is on this device, so it costs this device
        // nothing — which is what keeps it out of the byte budget.
        bytes: 0,
        // The account's own timestamp, not a local one: there is no local
        // event to date it by, and leaving it unset would sort every
        // account-only row against every other by id alone.
        savedAt: accountRow.created_at,
        // Emphatically not an orphan: nothing failed here, and there is
        // nothing on disk to reclaim.
        orphaned: false,
        basemapKey: accountRow.basemap_key || null,
        onDevice: false,
        synced: true,
        bbox: Array.isArray(accountRow.bbox) ? accountRow.bbox : null,
        regionId: accountRow.region_id || '',
      });
    }
    accountOnly.sort(function (a3, b3) {
      return a3.id.localeCompare(b3.id);
    });

    return out.concat(orphans, accountOnly);
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
    groupRowsByBasemap: groupRowsByBasemap,
    reconcileAreas: reconcileAreas,
    budgetSegments: budgetSegments,
    BUDGET_CHOICES_MB: BUDGET_CHOICES_MB,
    MIN_BUDGET_MB: MIN_BUDGET_MB,
    DEFAULT_BUDGET_MB: DEFAULT_BUDGET_MB,
    BYTES_PER_MB: BYTES_PER_MB,
  });
})();
