/*
 * static/js/map_downloads_manager.js — the "Manage downloads" sheet
 * (SNOW-588; the way in rewritten by SNOW-634; SNOW-635 adds Rename and
 * lets more than one custom area be listed).
 *
 * The DOM half of the surface that answers "what have I stored offline on
 * this device, and what is it costing me". Every piece of arithmetic and
 * ordering lives in static/js/basemap_manage_core.js, which is unit-tested
 * directly; this file is what turns that into a sheet, and deliberately
 * holds no logic worth testing without a DOM.
 *
 * Markup: apps/public/templates/public/partials/_map_downloads_sheet.html.
 * Opened from ``#map-custom-download-control`` (the bottom-right roundel —
 * public/partials/_map_custom_download_control.html), driven by that
 * button's own click handler in map.js calling ``window.pwaDownloadsManager
 * .open()`` (this module's own bridge — see the bridges section below).
 * SNOW-588 through SNOW-632 reached this sheet only via a
 * ``[data-menu-action="manage-downloads"]`` row buried in the layers menu,
 * while the roundel opened a DIFFERENT surface directly (the "Download a
 * custom area" framing overlay); SNOW-634 collapsed the two — that menu
 * row is gone, and "Download a custom area" moved INTO this sheet instead,
 * as the ``[data-downloads-add]`` trigger handled below.
 *
 * ## What it reads and writes
 *
 * Everything here is device-local by nature: Cache Storage is per-browser,
 * so the same account on a phone and a laptop has two independent sets of
 * downloads and two independent budgets.
 *
 * The downloads themselves live in TWO ``meta:app`` rows, and this module
 * reads NEITHER directly — it goes through ``map.js``'s
 * ``basemapDownloadedAreas()`` (see the bridges section below):
 *
 *   basemap.regions      An array, one entry per downloaded region, keyed
 *                        by ``region_id``. Written by the per-region
 *                        download control.
 *   basemap.customAreas  An array (SNOW-635 — was a single row through
 *                        SNOW-634), one entry per user-framed area.
 *                        Written by the custom-area control.
 *
 * Neither key is the Cache Storage bucket id — that is
 * ``areaIdForRegion(region_id)`` or the custom area's own minted id
 * (``generateCustomAreaId`` / ``isCustomAreaId``) — which is the single
 * most important reason not to read them here. An earlier version of this
 * module read a ``basemap.areas`` row that no writer has ever produced,
 * and every test passed, because the tests seeded that row themselves. The
 * area list and the delete now both go through the code the download
 * controls and the eviction planner already use, so this surface cannot
 * disagree with them about what is on disk.
 *
 *   basemap.budgetMb    The standing budget, read by SNOW-586's eviction
 *                       planner. This surface is the only thing that ever
 *                       writes it, and the one row it touches directly.
 *
 * ## Deleting is a bucket delete, not a tile sweep
 *
 * SNOW-586 gives every download its own cache
 * (``snowdesk-basemap-pinned-<areaId>``), so removing one is a single
 * ``caches.delete()`` plus the matching record entry — atomic per area,
 * where the custom-area control previously had to re-derive the old URL
 * set from ``buildBlob`` and delete entry by entry, silently leaving
 * orphan tiles if it failed part-way. That is why this ticket depends on
 * that one: an itemised list with a working delete is only honest if
 * deleting cannot half-succeed.
 *
 * Both halves are ``map.js``'s ``evictBasemapAreas``, which this module
 * calls rather than reimplements. Deleting an area means taking an area
 * id back to the right half of the right record — the region array or the
 * custom-area array — and the eviction path already does exactly that, for
 * the budget planner. A second implementation here would be the same
 * decision made twice, and the one that ran less often would be the one
 * that rotted.
 *
 * ``evictBasemapAreas`` is best-effort per area and reports nothing, so
 * the outcome is confirmed by re-reading the area list and checking the
 * id is gone — which is a stronger check than a return value anyway: it
 * verifies the state, not the attempt.
 *
 * ## Renaming (SNOW-635)
 *
 * A custom area's default label is "Custom area N" — filled into
 * ``area.name`` by ``map.js``'s ``basemapDownloadedAreas()`` itself, from
 * the record's ``ordinal``, in memory only and never persisted (storing
 * the interpolated string would freeze the user's language at download
 * time). By the time a row reaches this module, ``row.label`` is already
 * the right text to show — stored name or numbered default, uniformly —
 * so this module needs no ``ordinal``-aware fallback of its own; it just
 * renders ``row.label``. The Rename control next to a renameable row's
 * own Remove uses ``window.prompt``, pre-filled with that same label,
 * matching the sheet's existing ``window.confirm`` for delete (itself
 * following ``pwa_reset.js``'s destructive-action idiom): no new markup,
 * no focus trap, no Escape handling — a richer inline editor is a
 * deliberate non-goal. The handler writes the trimmed result back onto
 * the area's record via ``window.pwaBasemapDownloads.rename(areaId,
 * name)`` (map.js's own bridge — see the bridges section below) and
 * re-renders. Only a ``renameable`` row (a custom area with a real record
 * behind it, per ``basemap_manage_core.js``'s ``manageRows``) ever shows
 * the control — a region's name is its real name, and an orphaned bucket
 * has no record entry left to write one onto.
 *
 * ## Why it re-clones its body on every open
 *
 * The sheet is rebuilt from its ``<template>`` each time it opens rather
 * than being updated in place. Downloads and evictions both happen while
 * this sheet is closed (a download run, or SNOW-586 evicting to make room),
 * so anything cached in the DOM between opens is a stale row waiting to be
 * shown. Rebuilding is a few dozen nodes and removes the whole class of
 * bug.
 *
 * ## Offline gating is applied at render time (SNOW-637)
 *
 * Everything this sheet does to what is ALREADY stored — listing, sizing,
 * renaming, deleting — works offline, and must: storage pressure is felt
 * exactly when there is no connection to relieve it. Starting a NEW
 * download is the one thing that cannot, so ``[data-downloads-add]`` is
 * disabled and relabelled while ``navigator.onLine`` is false, keeping the
 * control visible rather than hiding it (the treatment
 * map_layer_sync_status.js established for uncached rows offline).
 *
 * That gating lives inside ``render()`` because of the re-clone above: a
 * listener or attribute bound to the trigger at boot is thrown away with
 * the previous body on the next open. A ``snowdesk:connectivity-changed``
 * listener re-renders an OPEN sheet so a connection dropped mid-use lands
 * immediately, and the click handler keeps its own ``navigator.onLine``
 * check as the race guard for the gap between paint and tap.
 *
 * ## Its map.js dependencies are four narrow bridges
 *
 * Everything this module needs from static/js/map.js is module scope
 * there, so it reaches it through four small frozen globals that file
 * exposes:
 *
 *   window.pwaDownloadedOverlay  (SNOW-645 review) ``show()``, ``hide()``
 *                                and ``isVisible()`` for the downloaded-
 *                                tiles map overlay. ``open()`` calls
 *                                ``show()`` unconditionally; the in-sheet
 *                                "Available offline" toggle's own change
 *                                handler is the ONLY thing that ever calls
 *                                ``hide()`` — closing the sheet does not,
 *                                because it is bottom-docked and
 *                                full-width on mobile
 *                                (includes/_overlay_sheet.html), and
 *                                binding visibility to "sheet is open"
 *                                left the squares this overlay draws
 *                                permanently covered by the thing showing
 *                                them. ``render()`` reads ``isVisible()``
 *                                back to paint the toggle, rather than
 *                                keeping a flag of its own that could
 *                                drift from it.
 *   window.pwaBasemapDownloads   ``areas()``, ``evict(ids)`` and (SNOW-635)
 *                                ``rename(areaId, name)`` — the read, the
 *                                delete, and the rename. Without it the
 *                                sheet opens and honestly reports that it
 *                                can see nothing, which is the truthful
 *                                answer when the module that owns the
 *                                records hasn't loaded.
 *   window.pwaLayersMenu         ``close()``. Optional — without it the
 *                                menu is simply left open behind the
 *                                sheet (SNOW-634: the roundel that opens
 *                                this sheet lives outside that menu, but
 *                                the menu can still be open behind it —
 *                                e.g. a user who opened it, then clicked
 *                                the roundel without closing it first).
 *                                Mirroring its three DOM writes here would
 *                                be a duplicate free to drift.
 *   window.pwaCustomAreaDownload SNOW-634's third bridge: ``openFraming()``,
 *                                called by ``[data-downloads-add]`` below
 *                                once this sheet has hidden itself. Optional
 *                                — without it the trigger is a dead click,
 *                                which is no worse than the sheet failing
 *                                to open at all when its OWN bridge is
 *                                missing.
 *
 * The budget row (``basemap.budgetMb``) is the one piece of storage this
 * module touches directly, because it is the only thing that ever writes
 * it; ``map.js``'s ``basemapDownloadBudgetBytes()`` is the reader.
 */

(function mapDownloadsManagerInit() {
  'use strict';

  var SHEET_ID = 'map-downloads-sheet';
  var BODY_TEMPLATE_ID = 'map-downloads-body-template';
  var ROW_TEMPLATE_ID = 'map-downloads-row-template';

  // The one storage row this module touches directly. The downloads
  // themselves live in two records read through window.pwaBasemapDownloads
  // (see the module header); this is the only one nothing else writes.
  var BUDGET_KEY = 'basemap.budgetMb';

  var sheet = document.getElementById(SHEET_ID);
  var bodyTemplate = document.getElementById(BODY_TEMPLATE_ID);
  var rowTemplate = document.getElementById(ROW_TEMPLATE_ID);
  if (!sheet || !bodyTemplate || !rowTemplate) return;

  /**
   * Translated strings, lifted out of the strings template.
   *
   * ``makemessages`` only scans templates and Python, so every string this
   * module renders is server-rendered into a hidden template and read back
   * here rather than written as a JS literal — which would ship
   * untranslatable English to every locale.
   *
   * SNOW-620: this module was the reference implementation, and its reader
   * is now the shared one in static/js/i18n_strings.js — eight more
   * surfaces needed it, and its two non-obvious details (whitespace
   * collapsed rather than trimmed; substitution by name rather than
   * position) are exactly the kind that get lost in a re-implementation.
   *
   * @type {Object<string, string>}
   */
  var STRINGS = self.pwaStrings.read('map-downloads-strings-template', {
    // SNOW-645 review: composes a row's title from its name and kind —
    // "Verbier (Region)". An orphaned row has no usable name (its
    // `label` is the bare bucket id), so buildRow skips this format for
    // it and uses `kind-incomplete` alone instead.
    'row-title': '%(name)s (%(kind)s)',
    'kind-region': 'Region',
    'kind-custom': 'Custom area',
    'kind-incomplete': 'Incomplete download',
    usage: '%(used)s of %(budget)s used',
    'confirm-remove':
      "Remove the offline map for %(name)s? This frees %(size)s. You can " +
      "download it again when you're back online.",
    'remove-failed': "That download couldn't be removed. Try again.",
    // SNOW-634: [data-downloads-add]'s offline refusal.
    'add-offline': "You're offline — connect to download a new area.",
    // SNOW-637: the same refusal as the disabled trigger's own label — the
    // toast above is phrased as a reply to a tap, which reads wrong on a
    // control nobody has touched yet.
    'add-disabled': 'Downloading needs a connection',
    // SNOW-635: the Rename control's own prompt/failure copy. The default
    // "Custom area N" label itself lives in map.js's MAP_STRINGS
    // (_map_embed.html), not here — see this module's "Renaming" header
    // note for why.
    'rename-prompt': 'Name this area',
    'rename-failed': "That name couldn't be saved. Try again.",
  });

  var interpolate = self.pwaStrings.interpolate;

  /** @returns {Object|null} The pure core, once it has loaded. */
  function manageCore() {
    return window.pwaBasemapManageCore || null;
  }

  /** @returns {Object|null} basemap_download_core.js's exports. */
  function downloadCore() {
    return window.pwaBasemapDownloadCore || null;
  }

  // SNOW-645: `basemapLabel` (the picker-label lookup used by `buildRow`
  // below) now lives in static/js/map_basemap_downloads.js, shared with
  // the region roundel's "downloaded under another basemap" state
  // (map_region_download.js) — a second copy here would be the same
  // lookup written twice. Called lazily (only once the sheet is actually
  // opened), so it is safe to reference as a bare identifier regardless of
  // this file's own position in the document relative to that one's
  // <script> tag: by the time a user can open this sheet, every deferred
  // script on the page — including map_basemap_downloads.js — has already
  // run. tests/public/test_map_script_order.py's own docstring lists this
  // file as one of the eight map*.js modules deliberately outside
  // MAP_BUNDLE's load-order contract, which only binds modules that need
  // each other AT PARSE TIME.

  /**
   * Read a ``meta:app`` row's value (the budget row — see ``BUDGET_KEY``).
   *
   * Best-effort: IndexedDB can be unavailable (private mode, a blocked
   * upgrade — see db.js's ``reset_required``), and a manage surface that
   * throws on open is worse than one that falls back to the default
   * budget.
   *
   * @param {string} key
   * @returns {Promise<any>} ``undefined`` when absent or unreadable.
   */
  async function readMeta(key) {
    try {
      const row = await window.pwaDb?.get('meta:app', key);
      return row ? row.value : undefined;
    } catch (_err) {
      return undefined;
    }
  }

  /**
   * Write a ``meta:app`` row.
   *
   * @param {string} key
   * @param {any} value
   * @returns {Promise<boolean>} Whether the write landed — the budget
   *   control needs to know, since a silently-dropped budget would leave
   *   the select showing a setting the eviction planner never sees.
   */
  async function writeMeta(key, value) {
    try {
      await window.pwaDb?.put('meta:app', { key: key, value: value });
      return true;
    } catch (_err) {
      return false;
    }
  }

  /**
   * Every recorded area, via map.js's normalising reader.
   *
   * @returns {Promise<Array<Object>>} Empty when the bridge is absent or
   *   the read fails — the sheet then honestly reports an empty device
   *   rather than throwing on open.
   */
  async function readAreas() {
    try {
      const areas = await window.pwaBasemapDownloads?.areas();
      return Array.isArray(areas) ? areas : [];
    } catch (_err) {
      return [];
    }
  }

  /**
   * Delete one area — bucket and record entry both.
   *
   * Delegates to map.js's ``evictBasemapAreas``, which already knows how
   * to take an area id back to the right half of the right record (the
   * ``basemap.regions`` array or the ``basemap.customAreas`` array). It is
   * best-effort and returns nothing, so the outcome is confirmed by
   * re-reading the area list — verifying the state rather than trusting
   * the attempt.
   *
   * @param {string} areaId
   * @returns {Promise<boolean>} Whether the area is now genuinely gone.
   */
  async function deleteArea(areaId) {
    if (!window.pwaBasemapDownloads) return false;
    try {
      await window.pwaBasemapDownloads.evict([areaId]);
    } catch (_err) {
      return false;
    }

    const remaining = await readAreas();
    if (remaining.some((area) => area && area.id === areaId)) return false;

    // evictBasemapAreas already refreshes the downloaded-areas overlay.
    // The layers-menu dots are this module's to refresh: they claim the
    // basemap is available offline, which one fewer pinned bucket can
    // change.
    window.pwaLayerSyncStatus?.refresh();
    // SNOW-634: and the roundel that opens this sheet — a delete can take
    // the device from "done" (something downloaded) back to "idle"
    // (nothing left), and nothing else re-probes it once this sheet is
    // the only surface that ever changes what is on disk here.
    window.pwaCustomAreaDownload?.refresh();
    return true;
  }

  /**
   * Populate the budget ``<select>`` from the core's offered choices.
   *
   * Built here rather than server-rendered so the offered sizes live in
   * one place — next to the floor that pins the smallest of them to
   * ``DOWNLOAD_CEILING_MB``. The labels are numerals plus "MB", so
   * generating them costs no translatable text.
   *
   * @param {HTMLSelectElement} select
   * @param {number} selectedMb
   */
  function renderBudgetOptions(select, selectedMb) {
    const core = manageCore();
    if (!core) return;
    select.textContent = '';
    for (const mb of core.BUDGET_CHOICES_MB) {
      const option = document.createElement('option');
      option.value = String(mb);
      option.textContent = core.formatMegabytes(core.megabytesToBytes(mb));
      if (mb === selectedMb) option.selected = true;
      select.appendChild(option);
    }
  }

  /**
   * Rebuild the sheet's contents from the current records.
   *
   * @returns {Promise<void>}
   */
  async function render() {
    const core = manageCore();
    if (!core) return;

    const [list, budgetMb] = await Promise.all([
      readAreas(),
      readMeta(BUDGET_KEY),
    ]);
    const chosenMb = core.clampBudgetMb(budgetMb);
    const summary = core.budgetSummary(list, core.megabytesToBytes(chosenMb));
    const rows = core.manageRows(list, {
      // SNOW-635: a predicate, not a single id — see manageRows's own
      // docstring for why. No `customLabel` any more either — a custom
      // row's label is `area.name`, already filled by the reader.
      isCustomAreaId: downloadCore()?.isCustomAreaId,
    });

    // Re-clone rather than update in place — see the module header.
    sheet.textContent = '';
    sheet.appendChild(bodyTemplate.content.cloneNode(true));

    // SNOW-645 review: reflects the overlay's REAL visibility —
    // window.pwaDownloadedOverlay.isVisible() — rather than a flag of its
    // own that could drift from it. open() calls show() before this runs
    // (see its own comment), so a freshly opened sheet always paints this
    // already checked.
    const overlayToggle = /** @type {HTMLInputElement|null} */ (
      sheet.querySelector('[data-downloads-overlay-toggle]')
    );
    if (overlayToggle) {
      overlayToggle.checked = !!window.pwaDownloadedOverlay?.isVisible?.();
    }

    const summaryEl = sheet.querySelector('[data-downloads-summary]');
    if (summaryEl) {
      summaryEl.textContent = interpolate(STRINGS.usage, {
        used: core.formatMegabytes(summary.usedBytes),
        budget: core.formatMegabytes(summary.budgetBytes),
      });
    }

    const bar = sheet.querySelector('[data-downloads-bar]');
    if (bar) {
      bar.style.width = summary.pct + '%';
      bar.textContent = '';
      // SNOW-645 review: one segment per basemap actually stored (grouped
      // and summed by basemap_manage_core.js's budgetSegments — never one
      // per area), each sized by flex-grow in proportion to its own share
      // of the used bytes and coloured via .basemap-identity-fill's
      // data-basemap-key rules (src/css/main.css §2) — the same rules the
      // row swatches use, so the two readings of "what basemap is this"
      // agree. JS sets the key and the grow weight; nothing here touches
      // a colour or builds a class string.
      for (const segment of core.budgetSegments(list)) {
        const el = document.createElement('div');
        el.className = 'basemap-identity-fill h-full';
        el.dataset.basemapKey = segment.basemapKey;
        el.style.flexGrow = String(segment.bytes);
        el.style.flexShrink = '0';
        el.style.flexBasis = '0';
        bar.appendChild(el);
      }
    }

    const track = sheet.querySelector('[data-downloads-track]');
    if (track) {
      // The bar itself can no longer turn solid red once it is carrying
      // real basemap colours (SNOW-645 review) — the over-budget signal
      // moves to the track that holds it instead. Still toggled, not a
      // one-way class: coming back under budget (a delete, or raising the
      // budget) has to clear it on the next render.
      track.classList.toggle('ring-2', summary.overBudget);
      track.classList.toggle('ring-status-error-text', summary.overBudget);
    }

    const over = sheet.querySelector('[data-downloads-over]');
    if (over) over.hidden = !summary.overBudget;

    const empty = sheet.querySelector('[data-downloads-empty]');
    if (empty) empty.hidden = rows.length > 0;

    const listEl = sheet.querySelector('[data-downloads-list]');
    if (listEl) {
      for (const row of rows) listEl.appendChild(buildRow(row));
    }

    const select = /** @type {HTMLSelectElement|null} */ (
      sheet.querySelector('[data-downloads-budget]')
    );
    if (select) renderBudgetOptions(select, chosenMb);

    // SNOW-637: offline-integrity for the add-trigger. Listing and deleting
    // what is already stored needs no connection, but STARTING a download
    // does, so the control is disabled and relabelled rather than left live
    // to fail on tap — the disabled-but-visible treatment
    // map_layer_sync_status.js already established for uncached rows
    // offline, not a second one.
    //
    // Applied here rather than bound once at init because the body is
    // re-cloned from its <template> on every render (see the module
    // header), which discards anything bound to the previous copy. Only the
    // offline branch needs writing for the same reason: the next render
    // starts from a pristine clone, so coming back online restores the
    // enabled control and its original label without an else.
    const addButton = sheet.querySelector('[data-downloads-add]');
    if (addButton && !navigator.onLine) {
      addButton.setAttribute('disabled', '');
      // Alongside the native property, not instead of it: `disabled` is
      // what stops the click, `aria-disabled` is what a screen reader
      // announces before the user reaches for it.
      addButton.setAttribute('aria-disabled', 'true');
      addButton.textContent = STRINGS['add-disabled'] || '';
    }
  }

  /**
   * Build one list row.
   *
   * @param {{id: string, kind: string, orphaned?: boolean, label: string,
   *   renameable?: boolean, size: string}} row
   * @returns {DocumentFragment}
   */
  function buildRow(row) {
    const fragment = /** @type {DocumentFragment} */ (
      rowTemplate.content.cloneNode(true)
    );

    // SNOW-635 review: `row.label` is already the right text — a stored
    // name, or an unrenamed custom area's numbered default, filled in
    // upstream by map.js's basemapDownloadedAreas() — so this module has
    // no ordinal-aware fallback of its own to build any more.
    //
    // SNOW-645 review: the separate "Region" / "Custom area" line that
    // used to sit under the name is folded into this one via `row-title`
    // — "Verbier (Region)". An orphaned row (SNOW-612) is labelled by
    // what it IS — the leftovers of a download that never finished —
    // rather than as the region or custom area it was going to be: its
    // `label` is the bare bucket id, not a name worth showing, so it
    // skips the format entirely and reads as `kind-incomplete` alone.
    // Calling it a finished download by wrapping the id would be a lie
    // about what the device can do offline.
    const label = fragment.querySelector('[data-row-label]');
    if (label) {
      if (row.orphaned) {
        label.textContent = STRINGS['kind-incomplete'] || '';
      } else {
        const kind =
          row.kind === 'custom'
            ? STRINGS['kind-custom'] || ''
            : STRINGS['kind-region'] || '';
        label.textContent = interpolate(STRINGS['row-title'], {
          name: row.label,
          kind: kind,
        });
      }
    }

    const size = fragment.querySelector('[data-row-size]');
    if (size) size.textContent = row.size;

    // SNOW-645: which basemap this area was downloaded under. Colour is
    // never the only signal — the swatch always pairs with the picker's
    // own translated name. No key (a legacy record, or an orphan —
    // manageRows/reconcileAreas both fall back to ''), or a key the picker
    // no longer has a row for, removes the whole line rather than showing
    // an unknown or mismatched basemap.
    const basemapRow = fragment.querySelector('[data-row-basemap]');
    if (basemapRow) {
      const label = row.basemapKey
        ? window.pwaBasemapDownloads?.basemapLabel?.(row.basemapKey) || ''
        : '';
      if (label) {
        const swatch = basemapRow.querySelector('[data-row-basemap-swatch]');
        if (swatch) swatch.dataset.basemapKey = row.basemapKey;
        const name = basemapRow.querySelector('[data-row-basemap-name]');
        if (name) name.textContent = label;
      } else {
        basemapRow.remove();
      }
    }

    const button = fragment.querySelector('[data-downloads-delete]');
    if (button) {
      button.setAttribute('data-downloads-delete', row.id);
      // Carried on the element so the delegated handler can name the area
      // in its confirmation without re-reading the record.
      button.setAttribute('data-downloads-label', row.label);
      button.setAttribute('data-downloads-size', row.size);
    }

    // SNOW-635: only a renameable row (a custom area with a real record
    // behind it — see manageRows's own docstring) shows the control. A
    // region's name is its real name, and an orphaned bucket has no
    // record entry left to write one onto.
    const renameBtn = fragment.querySelector('[data-downloads-rename]');
    if (renameBtn) {
      if (row.renameable) {
        renameBtn.setAttribute('data-downloads-rename', row.id);
        // Pre-fills window.prompt with whatever is currently showing, so
        // accepting it unchanged is a no-op rather than blanking the name.
        renameBtn.setAttribute('data-downloads-current-name', row.label);
      } else {
        renameBtn.remove();
      }
    }
    return fragment;
  }

  /**
   * Open the sheet.
   *
   * @returns {Promise<void>}
   */
  async function open() {
    // SNOW-645 review: called BEFORE render(), not after — render() reads
    // window.pwaDownloadedOverlay.isVisible() to paint the in-sheet toggle,
    // and a user opening the sheet must see it already checked, not catch
    // up on the render after this one. Optional chaining because map.js's
    // IIFE (which owns the overlay) runs before this file but this module
    // sits outside the map bundle's own load-order contract — see the
    // module header.
    //
    // Turns the overlay on UNCONDITIONALLY on every open — that is what
    // makes it discoverable. It does NOT turn off when the sheet closes
    // (see the toggle's own change handler, further down, which is now the
    // ONLY thing that calls hide()): the sheet is bottom-docked and
    // full-width on mobile (includes/_overlay_sheet.html), so binding
    // visibility to "sheet is open" left the squares this draws
    // permanently covered by the thing showing them.
    window.pwaDownloadedOverlay?.show();
    await render();
    sheet.hidden = false;
    // SNOW-634: the roundel (#map-custom-download-control) is the only way
    // in now, but the layers menu can still be open behind it — a user who
    // opened the menu, then clicked the roundel without closing it first —
    // and leaving it open would cover the sheet that just opened.
    window.pwaLayersMenu?.close();
    sheet.focus();
  }

  // Delegated on the sheet: cloned along with the body template on every
  // render, so a per-element listener would have to be rebound each time.
  //
  // SNOW-634: "Download a custom area" — offline refuses with a toast (no
  // read/write of the area list needs a connection, but STARTING a new
  // download does, mirroring #map-frame-overlay's own Download button).
  // SNOW-637 disables the trigger outright while offline (see render()), so
  // this branch is now the race guard rather than the primary refusal: it
  // covers a connection lost between the paint and the tap, which no
  // render-time check can catch. Online hides this sheet and hands off to
  // map.js's own bridge, which
  // opens framing exactly as the roundel's own click used to. Hiding
  // directly (rather than a dismiss idiom) mirrors `open()`'s own
  // `sheet.hidden = false` above; the sheet does not reopen when framing
  // closes — SNOW-632 already ends a run on an in-overlay "23.4 MB
  // downloaded" + Close, so there is nothing here for a re-open to add.
  sheet.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    const addButton = target.closest('[data-downloads-add]');
    if (addButton) {
      if (!navigator.onLine) {
        window.MapSheet.toast(STRINGS['add-offline']);
        return;
      }
      sheet.hidden = true;
      // SNOW-645 review: hiding the sheet here does NOT hide the
      // downloaded-tiles overlay — only the in-sheet toggle's own change
      // handler ever calls hide() now (see this module's header and
      // open()'s own comment for why closing no longer implies off).
      window.pwaCustomAreaDownload?.openFraming();
      return;
    }
    if (_handleRenameClick(event)) return;
    _handleDeleteClick(event);
  });

  /**
   * The Rename-button half of the sheet's delegated click handler
   * (SNOW-635), factored out so the listener above can dispatch to it
   * after ruling out the add-trigger.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether a rename button was the click's target —
   *   tells the caller not to also try the delete handler.
   */
  function _handleRenameClick(event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return false;
    const button = target.closest('[data-downloads-rename]');
    if (!button) return false;

    const areaId = button.getAttribute('data-downloads-rename');
    if (!areaId) return true;

    // window.prompt, matching the sheet's own window.confirm for delete —
    // see this module's "Renaming" header note for why no richer editor.
    if (typeof window.prompt !== 'function') return true;
    const current = button.getAttribute('data-downloads-current-name') || '';
    const next = window.prompt(STRINGS['rename-prompt'], current);
    // null means Cancel; an unchanged or blank result is treated the same
    // way — nothing to write, so no write is made.
    if (next === null) return true;
    const trimmed = next.trim();
    if (!trimmed || trimmed === current) return true;

    if (!window.pwaBasemapDownloads) return true;
    window.pwaBasemapDownloads.rename(areaId, trimmed).then(function (ok) {
      if (ok) {
        render();
        return;
      }
      button.textContent = STRINGS['rename-failed'] || '';
      button.setAttribute('disabled', '');
    });
    return true;
  }

  /**
   * The delete-button half of the sheet's delegated click handler,
   * factored out so the listener above can dispatch to it after ruling
   * out the add-trigger and the rename button.
   *
   * @param {MouseEvent} event
   * @returns {void}
   */
  function _handleDeleteClick(event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    const button = target.closest('[data-downloads-delete]');
    if (!button) return;

    const areaId = button.getAttribute('data-downloads-delete');
    if (!areaId) return;

    const message = interpolate(STRINGS['confirm-remove'], {
      name: button.getAttribute('data-downloads-label') || areaId,
      size: button.getAttribute('data-downloads-size') || '',
    });
    // window.confirm, matching pwa_reset.js's destructive-action idiom —
    // the copy names what goes and what it frees, so the choice can be
    // judged before it is made.
    if (typeof window.confirm === 'function' && !window.confirm(message)) {
      return;
    }

    deleteArea(areaId).then(function (ok) {
      if (ok) {
        render();
        return;
      }
      // Nothing was removed, so the row must stay. Say so on the button
      // itself rather than in a toast: it is the control that failed, and
      // the next render (any re-open) clears it.
      button.textContent = STRINGS['remove-failed'] || '';
      button.setAttribute('disabled', '');
    });
  }

  // The budget is written on change, not behind a Save — there is nothing
  // to batch, and a setting that needs confirming reads as riskier than it
  // is. Lowering it below current usage is allowed: refusing would leave a
  // user on a full device unable to say how much room they want to give up.
  sheet.addEventListener('change', function (event) {
    const target = /** @type {HTMLSelectElement} */ (event.target);
    if (!target || !target.matches || !target.matches('[data-downloads-budget]')) {
      return;
    }
    const core = manageCore();
    if (!core) return;
    const mb = core.clampBudgetMb(target.value);
    writeMeta(BUDGET_KEY, mb).then(function () {
      // Re-render for the running total and the over-budget line, both of
      // which are stated against the budget that just changed.
      render();
    });
  });

  // SNOW-645 review: the "Available offline" toggle drives the overlay
  // DIRECTLY — show()/hide() are the only writers of
  // window.pwaDownloadedOverlay's visibility, so this is the single place
  // outside open() that ever calls them, and it never touches a flag of
  // its own (render() reads isVisible() back, above). No re-render needed:
  // nothing else on the sheet depends on this state.
  sheet.addEventListener('change', function (event) {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (!target || !target.matches || !target.matches('[data-downloads-overlay-toggle]')) {
      return;
    }
    if (target.checked) {
      window.pwaDownloadedOverlay?.show();
    } else {
      window.pwaDownloadedOverlay?.hide();
    }
  });

  // SNOW-637: a sheet left open when the connection drops has to reflect it
  // there and then — waiting for the next open would leave a live-looking
  // trigger on screen for as long as the user keeps the sheet up. Same
  // broadcast, same reaction as the two surfaces that already do this:
  // map_layer_sync_status.js's menu dots and map.js's download roundel.
  //
  // Skipped while hidden: open() renders on its own way in, and re-rendering
  // a closed sheet would re-read IndexedDB on every transition for a surface
  // nobody is looking at.
  document.addEventListener('snowdesk:connectivity-changed', function () {
    if (sheet.hidden) return;
    render();
  });

  window.pwaDownloadsManager = Object.freeze({
    open: open,
    refresh: render,
  });
})();
