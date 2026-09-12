/*
 * static/js/reset_data_summary.js — paint the "Reset local data"
 * breakdown on /account/settings/ (SNOW-860).
 *
 * The DOM half of `reset_data_summary_core.js`, in the same shape as
 * `sync_log.js`: read what the device is holding, replace the "Loading…"
 * placeholder with rows, and resolve every failure to a specific sentence
 * rather than leaving the panel sitting on "Loading…" — which reads as
 * "still working" when it means "IndexedDB is gone".
 *
 * Three reads, all best-effort:
 *
 *   - the downloaded areas, through `window.pwaBasemapAreas`
 *     (`basemap_downloaded_areas.js`) and `manageRows`, which is the SAME
 *     path the map's Manage downloads sheet takes. One reader, so the two
 *     surfaces cannot disagree about what is on the device;
 *   - the depth of `queue:mutations` — SNOW-376's pending mutations. NOT
 *     `queue:events`, which is the SNOW-385 telemetry buffer: counting
 *     that would put a number of analytics payloads under a heading
 *     reading "unsent changes". `db.js`'s own header documents the
 *     distinction, and `docs/mutation-queue.md` is the contract;
 *   - `navigator.storage.estimate()`, for everything else the origin
 *     holds.
 *
 * `manageRows` excludes the shared overview maps (SNOW-867 — they are not
 * the user's downloads and not in the downloads panel's budget), so this
 * module picks them back out of the area list itself and hands them to
 * the core separately. This panel is where they ARE stated, which is what
 * that decision points at.
 *
 * It also publishes `window.pwaResetDataSummary.confirmLines()` for
 * `pwa_reset.js`, so the confirmation dialog quotes the total from the
 * same summary the user just read rather than computing a second one.
 */

(function () {
  'use strict';

  const LIST_ID = 'reset-data-summary-list';

  // Every read here is time-bounded, for the reason
  // docs/decisions/bounded-offline-read-paths.md gives for bounding every
  // read path in sw.js: storage in trouble HANGS rather than rejecting.
  // The `catch` branches below are written against a rejection this
  // device does not produce, so an IndexedDB request whose event never
  // fires left this panel reading "Loading…" for ever — reported
  // alongside the offline check stuck on "Checking…" in the same
  // screenshot, on the same iPad, in the same session.
  //
  // Four seconds because this panel paints on page load rather than on a
  // press: nobody asked for it, so nobody should wait long for it.
  const READ_BUDGET_MS = 4000;

  /** The value a read that never came back resolves to. */
  const NO_ANSWER = Symbol('no-answer');

  /**
   * Take one reading, or give up on it.
   *
   * Resolves with `NO_ANSWER` on overrun — never with an empty result,
   * because "nothing is downloaded" and "we could not find out" are
   * different things to tell someone standing over a Reset button.
   *
   * @param {function(): Promise<*>} work
   * @returns {Promise<*>}
   */
  function bounded(work) {
    return new Promise(function (resolve) {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        resolve(value);
      };
      const timer = setTimeout(() => finish(NO_ANSWER), READ_BUDGET_MS);
      let promise;
      try {
        promise = work();
      } catch (_err) {
        clearTimeout(timer);
        finish(NO_ANSWER);
        return;
      }
      Promise.resolve(promise).then(
        (value) => {
          clearTimeout(timer);
          finish(value);
        },
        () => {
          clearTimeout(timer);
          finish(NO_ANSWER);
        },
      );
    });
  }

  // SNOW-620: server-translated copy, read back from the template
  // accounts/partials/_reset_data_summary_body.html renders. The literals
  // are the English fallback — see static/js/i18n_strings.js.
  const STRINGS = self.pwaStrings.read('reset-data-summary-strings-template', {
    unavailable: 'Storage details unavailable on this device.',
    failed: 'Could not read what is stored on this device.',
    maps: 'Downloaded maps',
    'maps-none': 'Nothing downloaded on this device.',
    'shared-basemap-tiles': 'Shared basemap tiles',
    unsent: 'Unsent changes',
    'unsent-none': 'Nothing is waiting to be sent.',
    'unsent-one': '1 change has not reached the server yet. Resetting deletes it.',
    'unsent-many': '%(n)s changes have not reached the server yet. Resetting deletes them.',
    cached: 'Cached pages and tiles',
    'cached-note': 'Pages and map tiles saved by the device as you browse.',
    'cached-unknown': 'Size unknown',
    preferences: 'Map Viewport',
    'preferences-note':
      'The selected basemap and viewport will be restored to the default (OpenFreeMap).',
    // confirm-* belong to the reset dialog, not the panel. The panel has no
    // total footer; the confirmation still states a size before the user
    // commits, and still reads it from the same summary.
    'confirm-total': 'This deletes about %(size)s from this device.',
    'confirm-unsent-one': '1 change has not reached the server yet, and will be lost.',
    'confirm-unsent-many': '%(n)s changes have not reached the server yet, and will be lost.',
    'default-custom-name': 'Custom area %(n)s',
    'base-layer-name': 'Overview map',
  });

  // The last summary painted, or null before the first paint and after a
  // failed one. `pwa_reset.js` reads it through `confirmLines()` below;
  // null there means the dialog keeps its standing copy alone, which is
  // right — a figure that could not be read must not be guessed at in the
  // one dialog the user acts on.
  let LAST_SUMMARY = null;

  /**
   * Format a byte count the way every download surface already does, so
   * the panel and the Manage downloads sheet report the same size for the
   * same area. Falls back to a bare MB figure if the manage core is not
   * on the page.
   *
   * @param {number} bytes
   * @returns {string}
   */
  function formatBytes(bytes) {
    const manage = self.pwaBasemapManageCore;
    if (manage && typeof manage.formatMegabytes === 'function') {
      return manage.formatMegabytes(bytes);
    }
    return Math.round((Number(bytes) || 0) / (1024 * 1024)) + ' MB';
  }

  /**
   * Replace the list's contents with a single muted placeholder row.
   *
   * @param {HTMLElement} list
   * @param {string} text
   * @returns {void}
   */
  function renderPlaceholder(list, text) {
    list.textContent = '';
    const li = document.createElement('li');
    li.className = 'px-4 py-3 text-text-3';
    li.setAttribute('data-role', 'reset-data-summary-placeholder');
    li.textContent = text;
    list.appendChild(li);
  }

  /**
   * Build one category row: a heading, its figure on the trailing edge,
   * and the sentence saying what losing it costs.
   *
   * @param {{category: string, label: string, value: string,
   *   note: string, warn?: boolean}} spec
   * @returns {HTMLLIElement}
   */
  function buildCategory(spec) {
    const li = document.createElement('li');
    li.className = 'px-4 py-3';
    li.setAttribute('data-testid', 'reset-data-summary-row');
    li.setAttribute('data-category', spec.category);

    const head = document.createElement('div');
    head.className = 'flex items-baseline justify-between gap-3';

    const label = document.createElement('span');
    label.className = 'text-text-1 font-medium';
    label.textContent = spec.label;

    const value = document.createElement('span');
    // The warning is on the FIGURE, not the row: an unsent change is the
    // one thing here the reset destroys rather than makes you re-fetch,
    // and the number is what says how much of it there is.
    value.className = spec.warn
      ? 'shrink-0 font-mono text-caption text-status-warning-text'
      : 'shrink-0 font-mono text-caption text-text-2';
    value.setAttribute('data-role', 'reset-data-summary-value');
    value.textContent = spec.value;

    head.appendChild(label);
    head.appendChild(value);
    li.appendChild(head);

    // Omitted, not emptied: an empty <p> still carries mt-1, which reads
    // as a gap the row did not ask for.
    if (spec.note) {
      const note = document.createElement('p');
      note.className = 'mt-1 text-xs text-text-3';
      note.textContent = spec.note;
      li.appendChild(note);
    }

    return li;
  }

  /**
   * The per-area list under the Downloaded maps row.
   *
   * Named one by one rather than counted, because "3 areas" does not
   * answer the question the user is actually asking, which is whether the
   * one they need for the weekend is among them. The shared basemap
   * tiles arrive as one already-summed row from the core; they carried a
   * "Shared" pill until it turned out to say nothing the label did not.
   *
   * @param {Array<{id: string, label: string, bytes: number,
   *   shared: boolean}>} items
   * @returns {HTMLUListElement}
   */
  function buildMapList(items) {
    const ul = document.createElement('ul');
    ul.className = 'mt-2 space-y-1';
    ul.setAttribute('data-role', 'reset-data-summary-maps');
    items.forEach(function (item) {
      const li = document.createElement('li');
      li.className = 'flex items-baseline justify-between gap-3 text-xs text-text-3';
      li.setAttribute('data-testid', 'reset-data-summary-map');
      if (item.shared) li.setAttribute('data-shared', 'true');

      const name = document.createElement('span');
      name.className = 'min-w-0 truncate';
      name.textContent = item.label;

      const size = document.createElement('span');
      size.className = 'shrink-0 font-mono';
      size.textContent = formatBytes(item.bytes);

      li.appendChild(name);
      li.appendChild(size);
      ul.appendChild(li);
    });
    return ul;
  }

  /**
   * Paint the four categories from a computed summary.
   *
   * @param {HTMLElement} list
   * @param {Object} summary `pwaResetDataSummaryCore.summarise` output.
   * @returns {void}
   */
  function renderSummary(list, summary) {
    list.textContent = '';

    const maps = buildCategory({
      category: 'maps',
      label: STRINGS.maps,
      value: formatBytes(summary.maps.bytes),
      note: summary.maps.items.length ? '' : STRINGS['maps-none'],
    });
    if (summary.maps.items.length) maps.appendChild(buildMapList(summary.maps.items));
    list.appendChild(maps);

    let unsentNote = STRINGS['unsent-none'];
    if (summary.unsent.count === 1) {
      unsentNote = STRINGS['unsent-one'];
    } else if (summary.unsent.count > 1) {
      unsentNote = self.pwaStrings.interpolate(STRINGS['unsent-many'], {
        n: summary.unsent.count,
      });
    }
    list.appendChild(
      buildCategory({
        category: 'unsent',
        label: STRINGS.unsent,
        // A count, not a size — the only figure on this panel that is not
        // bytes, and the only one where zero is the good news.
        value: String(summary.unsent.count),
        note: unsentNote,
        warn: summary.unsent.warn,
      }),
    );

    list.appendChild(
      buildCategory({
        category: 'cached',
        label: STRINGS.cached,
        value: summary.cached.known
          ? formatBytes(summary.cached.bytes)
          : STRINGS['cached-unknown'],
        note: STRINGS['cached-note'],
      }),
    );

    list.appendChild(
      buildCategory({
        category: 'preferences',
        label: STRINGS.preferences,
        // No figure at all: a handful of keys in web storage is not a size
        // worth stating, and naming what reverts to the default says more
        // than a right-hand column could.
        value: '',
        note: STRINGS['preferences-note'],
      }),
    );
  }

  /**
   * Read the three sources the summary is built from.
   *
   * Each is independently best-effort. A device with no basemap cores
   * loaded still reports its queue depth; one with no
   * `navigator.storage.estimate()` still lists its downloads. Only a
   * total failure — no `pwaDb` at all — leaves the panel with nothing to
   * say, and it says that.
   *
   * @returns {Promise<Object>} The core's summary.
   */
  async function collect() {
    const download = self.pwaBasemapDownloadCore;
    const manage = self.pwaBasemapManageCore;
    const areasApi = window.pwaBasemapAreas;

    let areas = [];
    if (download && manage && areasApi) {
      areas = await bounded(() => areasApi.downloadedAreas({ strings: STRINGS }));
      // The downloads ARE this panel: a list that came back empty because
      // nothing answered would tell someone their 200 MB of maps had
      // vanished, on the one surface that offers to delete them. Throwing
      // puts the panel into its stated `failed` state instead.
      if (areas === NO_ANSWER) {
        throw new Error('downloaded areas did not answer');
      }
    }
    const rows =
      manage && download
        ? manage.manageRows(areas, {
            isCustomAreaId: download.isCustomAreaId,
            isBaseLayerAreaId: download.isBaseLayerAreaId,
          })
        : [];
    // The shared overview maps, which `manageRows` deliberately drops.
    const baseLayers = download
      ? areas.filter(function (area) {
          return area && download.isBaseLayerAreaId(area.id);
        })
      : [];

    // A queue that cannot be read — or that does not answer — reads as
    // empty, which is the pre-existing decision: see the core's own note
    // on why an unknown count must not become a warning.
    const counted = await bounded(() => window.pwaDb.count('queue:mutations'));
    const mutationCount = counted === NO_ANSWER ? 0 : counted || 0;

    // `estimate()` adds up every origin-scoped store, so on a device
    // holding a few hundred megabytes of tiles it is the slowest read on
    // this page by a distance. Bounded, and a null simply drops the byte
    // figures the core already prints as unknown.
    const estimated = await bounded(() => {
      if (!navigator.storage || typeof navigator.storage.estimate !== 'function') {
        return Promise.resolve(null);
      }
      return navigator.storage.estimate();
    });
    const storageEstimate = estimated === NO_ANSWER ? null : estimated;

    return window.pwaResetDataSummaryCore.summarise({
      rows: rows,
      baseLayers: baseLayers,
      mutationCount: mutationCount,
      storageEstimate: storageEstimate,
      sharedLabel: STRINGS['shared-basemap-tiles'],
    });
  }

  /**
   * Read what this device is holding and paint it, replacing the
   * "Loading…" placeholder from the template shell. Never throws — a
   * failed read degrades to a muted placeholder row, because the control
   * this panel sits beside has to stay usable whatever storage is doing.
   *
   * @returns {Promise<void>}
   */
  async function render() {
    const list = document.getElementById(LIST_ID);
    if (!list) return;

    if (!window.pwaDb || !window.pwaResetDataSummaryCore) {
      LAST_SUMMARY = null;
      renderPlaceholder(list, STRINGS.unavailable);
      return;
    }

    try {
      const summary = await collect();
      LAST_SUMMARY = summary;
      renderSummary(list, summary);
    } catch (_err) {
      LAST_SUMMARY = null;
      renderPlaceholder(list, STRINGS.failed);
    }
  }

  /**
   * The lines `pwa_reset.js` adds to its confirmation dialog (SNOW-860).
   *
   * The same total the user has just read on the panel, from the same
   * summary — not a second computation, which would be free to disagree
   * with the page at the exact moment disagreement is most expensive.
   * Empty before the first paint, or after one that failed: no figure is
   * better than an invented one in the dialog that authorises the wipe.
   *
   * @returns {string[]}
   */
  function confirmLines() {
    if (!LAST_SUMMARY) return [];
    const lines = [
      self.pwaStrings.interpolate(STRINGS['confirm-total'], {
        size: formatBytes(LAST_SUMMARY.totalBytes),
      }),
    ];
    if (LAST_SUMMARY.unsent.count === 1) {
      lines.push(STRINGS['confirm-unsent-one']);
    } else if (LAST_SUMMARY.unsent.count > 1) {
      lines.push(
        self.pwaStrings.interpolate(STRINGS['confirm-unsent-many'], {
          n: LAST_SUMMARY.unsent.count,
        }),
      );
    }
    return lines;
  }

  window.pwaResetDataSummary = Object.freeze({
    render: render,
    confirmLines: confirmLines,
  });

  // Deferred scripts execute in DOCUMENT ORDER, and this one's <script>
  // tag (inside the settings content block) appears BEFORE db.js's
  // (declared in base.html, after the content block) — so calling
  // render() eagerly here would race window.pwaDb into existence.
  // DOMContentLoaded fires only once every deferred script, db.js
  // included, has already run. Same reasoning as sync_log.js.
  document.addEventListener('DOMContentLoaded', render);
})();
