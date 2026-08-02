/*
 * static/js/map_downloads_manager.js — the "Manage downloads" sheet (SNOW-588).
 *
 * The DOM half of the surface that answers "what have I stored offline on
 * this device, and what is it costing me". Every piece of arithmetic and
 * ordering lives in static/js/basemap_manage_core.js, which is unit-tested
 * directly; this file is what turns that into a sheet, and deliberately
 * holds no logic worth testing without a DOM.
 *
 * Markup: apps/public/templates/public/partials/_map_downloads_sheet.html.
 * Opened from the ``[data-menu-action="manage-downloads"]`` row in the
 * layers menu (public/partials/_map_embed.html) — the menu that
 * docs/offline-map.md already calls the cache-state dashboard.
 *
 * ## What it reads and writes
 *
 * Two ``meta:app`` rows, both device-local by nature (Cache Storage is
 * per-browser, so the same account on a phone and a laptop has two
 * independent sets of downloads and two independent budgets):
 *
 *   basemap.areas     SNOW-586's record — one entry per downloaded area,
 *                     carrying the ``id`` that also names its Cache
 *                     Storage bucket, plus the run's ``bytes``. Read here,
 *                     and rewritten (shortened) on delete. Never written
 *                     with a new area — that is the download controls' job.
 *   basemap.budgetMb  The standing budget, read by SNOW-586's eviction
 *                     planner. This surface is the only thing that writes
 *                     it.
 *
 * ## Deleting is a bucket delete, not a tile sweep
 *
 * SNOW-586 gives every download its own cache
 * (``snowdesk-basemap-pinned-<areaId>``), so removing one is a single
 * ``caches.delete()`` that cannot leave a half-deleted area behind. That is
 * a real gain over what the custom-area control had to do before it
 * (re-derive the old URL set from ``buildBlob`` and delete entry by entry,
 * with a failure part-way through silently leaving orphan tiles), and it is
 * why this ticket depends on that one: an itemised list with a working
 * delete is only honest if deleting is atomic.
 *
 * The record is rewritten only AFTER the bucket delete resolves. The other
 * order would let a failed delete leave tiles on disk with nothing left
 * pointing at them — bytes the user has been told are gone, which no
 * surface could then account for or remove.
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
 * ## Its map.js dependencies are two narrow bridges
 *
 * This module stays out of static/js/map.js — SNOW-586 is editing that
 * file concurrently — and reaches it through two small frozen globals it
 * exposes instead: ``window.pwaLayersMenu.close()`` (the menu's open state
 * lives in the picker IIFE's closure, and mirroring its three DOM writes
 * here would be a duplicate that could drift) and
 * ``window.pwaRegionNames.get()`` (region names come from
 * ``FEATURE_BY_REGION_ID``, which is map.js module scope). Both are
 * optional: without them the sheet still opens, lists and deletes — it
 * just leaves the menu open and falls back to region ids for labels.
 */

(function mapDownloadsManagerInit() {
  'use strict';

  var SHEET_ID = 'map-downloads-sheet';
  var BODY_TEMPLATE_ID = 'map-downloads-body-template';
  var ROW_TEMPLATE_ID = 'map-downloads-row-template';
  var STRINGS_TEMPLATE_ID = 'map-downloads-strings-template';

  var AREAS_KEY = 'basemap.areas';
  var BUDGET_KEY = 'basemap.budgetMb';

  var sheet = document.getElementById(SHEET_ID);
  var bodyTemplate = document.getElementById(BODY_TEMPLATE_ID);
  var rowTemplate = document.getElementById(ROW_TEMPLATE_ID);
  if (!sheet || !bodyTemplate || !rowTemplate) return;

  var trigger = document.querySelector('[data-menu-action="manage-downloads"]');

  /**
   * Translated strings, lifted out of the strings template.
   *
   * ``makemessages`` only scans templates and Python, so every string this
   * module renders is server-rendered into a hidden template and read back
   * here rather than written as a JS literal — which would ship
   * untranslatable English to every locale.
   *
   * Internal whitespace is collapsed, not merely trimmed. ``djangofmt``
   * reflows a long ``{% blocktrans %}`` across lines to satisfy the line
   * length it enforces, which puts a newline and a run of source
   * indentation into the middle of the string — invisible in the template,
   * and rendered verbatim into a ``window.confirm`` dialog. Collapsing on
   * read makes these strings immune to how the formatter chooses to wrap
   * them, rather than leaving a template that must never be reformatted.
   *
   * @type {Object<string, string>}
   */
  var STRINGS = (function () {
    var out = {};
    var tpl = document.getElementById(STRINGS_TEMPLATE_ID);
    if (!tpl) return out;
    tpl.content.querySelectorAll('[data-string]').forEach(function (el) {
      out[el.dataset.string] = el.textContent.replace(/\s+/g, ' ').trim();
    });
    return out;
  })();

  /**
   * Substitute ``%(name)s``-style placeholders into a translated string.
   *
   * Django's ``blocktrans`` emits named placeholders, and a locale is free
   * to reorder them — so substitution has to be by name, never positional.
   *
   * @param {string} template
   * @param {Object<string, string>} values
   * @returns {string}
   */
  function interpolate(template, values) {
    if (!template) return '';
    return template.replace(/%\((\w+)\)s/g, function (match, key) {
      return Object.prototype.hasOwnProperty.call(values, key)
        ? String(values[key])
        : match;
    });
  }

  /** @returns {Object|null} The pure core, once it has loaded. */
  function manageCore() {
    return window.pwaBasemapManageCore || null;
  }

  /** @returns {Object|null} basemap_download_core.js's exports. */
  function downloadCore() {
    return window.pwaBasemapDownloadCore || null;
  }

  /**
   * Read a ``meta:app`` row's value.
   *
   * Best-effort throughout: IndexedDB can be unavailable (private mode,
   * a blocked upgrade — see db.js's ``reset_required``), and a manage
   * surface that throws on open is worse than one that reports an empty
   * device. The empty-state copy is honest in that case too: there is
   * nothing this surface can see.
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
   * The display name for an area record.
   *
   * @param {Object} area
   * @returns {string} Empty when unresolvable — ``manageRows`` then falls
   *   back to the region id, and finally the area id, so the row is still
   *   listed and still deletable.
   */
  function labelForArea(area) {
    if (area.kind === 'custom') return STRINGS['kind-custom'] || '';
    if (!area.region_id) return '';
    return window.pwaRegionNames?.get(area.region_id) || '';
  }

  /**
   * Delete one area: its cache bucket first, then its record entry.
   *
   * @param {string} areaId
   * @returns {Promise<boolean>} Whether the area is now genuinely gone.
   */
  async function deleteArea(areaId) {
    const core = downloadCore();
    if (!core || typeof core.pinnedCacheName !== 'function') return false;
    if (!('caches' in window)) return false;

    try {
      // Bucket first. A `false` return means the cache was already absent
      // — the area's tiles are gone either way, so the record entry should
      // still go; only a THROWN error means the delete genuinely failed
      // and the record must be left alone to keep pointing at the bytes.
      await caches.delete(core.pinnedCacheName(areaId));
    } catch (_err) {
      return false;
    }

    const areas = await readMeta(AREAS_KEY);
    const remaining = (Array.isArray(areas) ? areas : []).filter(
      (area) => !area || area.id !== areaId,
    );
    await writeMeta(AREAS_KEY, remaining);

    // The map's own views of the pinned cache are now wrong — the
    // downloaded-areas overlay would keep outlining an area with no tiles
    // behind it, and the layers menu's dots would keep claiming the
    // basemap is available offline.
    window.pwaDownloadedOverlay?.refresh();
    window.pwaLayerSyncStatus?.refresh();
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

    const [areas, budgetMb] = await Promise.all([
      readMeta(AREAS_KEY),
      readMeta(BUDGET_KEY),
    ]);
    const list = Array.isArray(areas) ? areas : [];
    const chosenMb = core.clampBudgetMb(budgetMb);
    const summary = core.budgetSummary(list, core.megabytesToBytes(chosenMb));
    const rows = core.manageRows(list, labelForArea);

    // Re-clone rather than update in place — see the module header.
    sheet.textContent = '';
    sheet.appendChild(bodyTemplate.content.cloneNode(true));

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
      // The bar is the glance-level signal; over budget it stops being a
      // neutral readout and becomes the thing the sheet is about.
      bar.classList.toggle('bg-status-error-text', summary.overBudget);
      bar.classList.toggle('bg-text-2', !summary.overBudget);
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
  }

  /**
   * Build one list row.
   *
   * @param {{id: string, kind: string, label: string, size: string}} row
   * @returns {DocumentFragment}
   */
  function buildRow(row) {
    const fragment = /** @type {DocumentFragment} */ (
      rowTemplate.content.cloneNode(true)
    );
    const label = fragment.querySelector('[data-row-label]');
    if (label) label.textContent = row.label;

    const kind = fragment.querySelector('[data-row-kind]');
    if (kind) {
      kind.textContent =
        row.kind === 'custom'
          ? STRINGS['kind-custom'] || ''
          : STRINGS['kind-region'] || '';
    }

    const size = fragment.querySelector('[data-row-size]');
    if (size) size.textContent = row.size;

    const button = fragment.querySelector('[data-downloads-delete]');
    if (button) {
      button.setAttribute('data-downloads-delete', row.id);
      // Carried on the element so the delegated handler can name the area
      // in its confirmation without re-reading the record.
      button.setAttribute('data-downloads-label', row.label);
      button.setAttribute('data-downloads-size', row.size);
    }
    return fragment;
  }

  /**
   * Open the sheet.
   *
   * @returns {Promise<void>}
   */
  async function open() {
    await render();
    sheet.hidden = false;
    // The layers menu is the only way in, and leaving it open would cover
    // the sheet it just opened.
    window.pwaLayersMenu?.close();
    sheet.focus();
  }

  if (trigger) {
    // Bound directly on the row rather than delegated from `document`:
    // map.js's picker binds its own handler to every `.basemap-menu-item`
    // and calls `stopPropagation()`, which would starve a delegated
    // listener. (Its handler is a harmless no-op for this row — the row
    // carries no overlay key and no basemap url, so it returns early.)
    trigger.addEventListener('click', function () {
      open();
    });
  }

  // Delegated on the sheet: rows are re-cloned on every render, so a
  // per-button listener would have to be rebound each time.
  sheet.addEventListener('click', function (event) {
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
  });

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

  window.pwaDownloadsManager = Object.freeze({
    open: open,
    refresh: render,
  });
})();
