// @ts-check
/*
 * static/js/map_downloads_sync_all.js — the Downloads sheet's answer to
 * "will this work offline?", and its Sync all (SNOW-1420).
 *
 * The Downloads sheet is the one home for offline
 * (docs/decisions/downloads-is-the-home-for-offline.md). Each of its rows
 * carries one area's verdict as a traffic-light dot, and its foot carries
 * one Sync all. This module owns both halves of that answer and nothing
 * else; `map_downloads_manager.js` paints them on every render.
 *
 * The verdict
 * -----------
 * Read from the stored record alone, through `offline_journey_core.js`'s
 * `stagesFromRecord` and `verdictFor` — the reading SNOW-1415's connection
 * panel used, moved here with it, so a verdict cannot differ between the
 * surfaces that still read the same core (/offline/'s own journey). One
 * rule is this surface's own: an area no tile-capable page has checked
 * (an unknown map) reads "Not checked yet" whatever its content age,
 * because `verdictFor` would let a stale bulletin outrank the unknown map
 * and say "Mostly", which claims the map is there when nothing has looked.
 *
 * Sync all
 * --------
 * The queue, then every area on this device, one at a time:
 * `offline_ready_core.js`'s `run`, over `pwaBasemapDownloads.syncArea`.
 * That core stays loaded by base.html because /offline/'s audit uses it
 * too. The run lives in module state rather than in the DOM, because the
 * sheet re-clones every row on each render and a run in flight must
 * survive that.
 */

(function () {
  'use strict';

  const MUTATION_STORE = 'queue:mutations';

  // The English fallbacks for the run's words, read from
  // `_map_downloads_sheet.html`'s strings template. Only the keys
  // `offline_ready_core.js` reads.
  var STRING_FALLBACKS = {
    'ready-running': 'Syncing…',
    'ready-progress': 'Syncing… (%(n)s of %(total)s)',
    'ready-all': 'Ready — %(total)s areas up to date',
    'ready-all-one': 'Ready — 1 area up to date',
    'ready-some': '%(ready)s of %(total)s ready',
    'queue-waiting': '%(n)s changes still waiting to send',
    'queue-waiting-one': '1 change still waiting to send',
    'sync-stage-map': 'Checking the map… (%(n)s of %(total)s)',
    'sync-stage-bulletins': 'Saving bulletins… (%(n)s of %(total)s)',
    'sync-stage-weather': 'Saving weather… (%(n)s of %(total)s)',
    'sync-stage-things': 'Saving your trips and pins… (%(n)s of %(total)s)',
  };

  /**
   * The run's words, read lazily: the sheet's template is markup and
   * always there by the time a press happens.
   *
   * @returns {Object<string, string>}
   */
  function strings() {
    if (!window.pwaStrings || typeof window.pwaStrings.read !== 'function') return STRING_FALLBACKS;
    return window.pwaStrings.read('downloads-sync-all-strings-template', STRING_FALLBACKS);
  }

  /**
   * The account this page was rendered for — base.html's `pwa-user-id`,
   * '' when signed out. A stamped things answer is read back only to the
   * account it was stamped for.
   *
   * @returns {string}
   */
  function currentPrincipal() {
    const meta = document.querySelector('meta[name="pwa-user-id"]');
    return (meta && meta.getAttribute('content')) || '';
  }

  /**
   * The pinned bucket id a stored record names: a custom area's own `id`,
   * or a region's id through `basemap_download_core.js`'s
   * `areaIdForRegion`, which owns that format.
   *
   * @param {any} entry A `basemap.regions` or `basemap.customAreas` row.
   * @param {boolean} isRegion Which store it came out of.
   * @returns {string}
   */
  function bucketIdFor(entry, isRegion) {
    if (!isRegion) return entry && entry.id ? String(entry.id) : '';
    if (!entry || !entry.region_id) return '';
    const core = /** @type {any} */ (self).pwaBasemapDownloadCore;
    if (!core || typeof core.areaIdForRegion !== 'function') return '';
    return core.areaIdForRegion(entry.region_id);
  }

  /**
   * Every stored area record, keyed by its bucket id — the id the sheet's
   * rows carry. Never throws: an IndexedDB that will not answer yields an
   * empty map, and every row then reads "Not checked yet".
   *
   * @returns {Promise<Map<string, Object>>}
   */
  async function records() {
    /** @type {Map<string, Object>} */
    const byId = new Map();
    const db = /** @type {any} */ (window).pwaDb;
    if (!db || typeof db.get !== 'function') return byId;
    try {
      const rows = await Promise.all([
        db.get('meta:app', 'basemap.regions'),
        db.get('meta:app', 'basemap.customAreas'),
      ]);
      rows.forEach((row, index) => {
        const list = row && row.value;
        if (!Array.isArray(list)) return;
        list.forEach((entry) => {
          const id = bucketIdFor(entry, index === 0);
          if (id) byId.set(id, entry);
        });
      });
    } catch (_err) {
      // Best-effort, per the docstring.
    }
    return byId;
  }

  /**
   * One area's answer, from its record alone.
   *
   * @param {Object|undefined} record
   * @param {Date} [now]
   * SNOW-1483: an area that should hold terrain data and was saved
   * without it (`pwaBasemapDownloads.terrainExpected`) reads "refresh"
   * with reason `terrain`, the same flag the inventory's Terrain row reads.
   *
   * @returns {{verdict: 'ready'|'refresh'|'open-map'|'unknown', reason: ''|'conditions'|'things'|'terrain'}}
   */
  function verdict(record, now) {
    const journey = /** @type {any} */ (window).pwaOfflineJourneyCore;
    if (!journey || !record) return { verdict: 'unknown', reason: '' };
    const principal = currentPrincipal();
    const bridge = /** @type {any} */ (window).pwaBasemapDownloads;
    const stages = journey.stagesFromRecord(record, now || new Date(), {
      signedIn: !!principal,
      principal: principal,
      terrainExpected: !!(bridge && typeof bridge.terrainExpected === 'function' && bridge.terrainExpected(record)),
    });
    if (stages.map.status === 'unknown') return { verdict: 'unknown', reason: '' };
    const answer = journey.verdictFor(stages);
    /** @type {''|'conditions'|'things'|'terrain'} */
    let reason = '';
    if (answer === 'refresh') {
      if (stages.map.status === 'no') reason = 'terrain';
      else reason = stages.conditions.status === 'no' ? 'conditions' : 'things';
    }
    return { verdict: answer, reason: reason };
  }

  /**
   * How many queued edits are still waiting to send.
   *
   * @returns {Promise<number>}
   */
  async function waitingMutations() {
    const db = /** @type {any} */ (window).pwaDb;
    const core = /** @type {any} */ (window).pwaOfflineReadyCore;
    if (!db || typeof db.getAll !== 'function' || !core) return 0;
    try {
      return core.waitingCount(await db.getAll(MUTATION_STORE));
    } catch (_err) {
      return 0;
    }
  }

  // The current Sync all: whether it is running, the area it is on and the
  // step it reported last, then its result once settled.
  /** @type {{running: boolean, current: string, step: Object|null, result: Object|null}|null} */
  let run = null;

  /**
   * Run Sync all over `areaIds`, calling `onChange` after every step and
   * once more when the run settles. A second press while one is running
   * is ignored and resolves to null.
   *
   * @param {Array<string>} areaIds Bucket ids, in the sheet's order.
   * @param {function(): void} onChange
   * @returns {Promise<Object|null>} The run's result.
   */
  async function press(areaIds, onChange) {
    if (run && run.running) return null;
    const core = /** @type {any} */ (window).pwaOfflineReadyCore;
    const bridge = /** @type {any} */ (window).pwaBasemapDownloads;
    if (!core || !bridge || typeof bridge.syncArea !== 'function') return null;
    const notify = typeof onChange === 'function' ? onChange : () => {};
    // SNOW-1416: the one content run, taken as 'user' — a background sync
    // finishes the area it is on, then gives way.
    const contentRun = bridge.contentRun;
    /** @type {{running: boolean, current: string, step: Object|null, result: Object|null}} */
    const current = { running: true, current: '', step: null, result: null };
    run = current;
    notify();
    if (contentRun && !(await contentRun.take())) {
      run = null;
      notify();
      return null;
    }
    const queue = /** @type {any} */ (window).pwaMutationQueue;
    try {
      current.result = await core.run({
        drain: () => (queue && typeof queue.drain === 'function' ? queue.drain() : undefined),
        waiting: waitingMutations,
        areas: areaIds.slice(),
        syncOne: async (/** @type {string} */ areaId, /** @type {Function} */ onStage) =>
          core.areaOutcome(await bridge.syncArea(areaId, onStage)),
        onStep: (/** @type {any} */ step) => {
          current.step = step;
          if (step.phase === 'area') current.current = step.id;
          if (step.phase === 'done') current.current = '';
          notify();
        },
      });
    } catch (_err) {
      current.result = { total: areaIds.length, ready: 0, waiting: 0, outcomes: [] };
    }
    if (contentRun) contentRun.release('user');
    current.running = false;
    current.current = '';
    notify();
    return current.result;
  }

  /**
   * The run's state, for the painter.
   *
   * @returns {{running: boolean, current: string, label: string}}
   */
  function state() {
    const core = /** @type {any} */ (window).pwaOfflineReadyCore;
    const running = !!(run && run.running);
    const label = running && core ? core.progressText(run && run.step, strings()) : '';
    if (!running) {
      // SNOW-1416: the background sync's area shows on its row too.
      const contentRun = /** @type {any} */ (window).pwaBasemapDownloads?.contentRun;
      const auto = contentRun && contentRun.current() === 'auto' ? contentRun.area() : '';
      return { running: false, current: auto || '', label: '' };
    }
    return { running: running, current: run ? run.current : '', label: label };
  }

  /**
   * One sentence for a settled run that did not bring every area up to
   * date, or '' for one that did.
   *
   * @param {Object|null} result
   * @returns {string}
   */
  function shortfall(result) {
    const core = /** @type {any} */ (window).pwaOfflineReadyCore;
    if (!result || !core) return '';
    const described = core.describe(result, strings());
    const r = /** @type {any} */ (result);
    if (r.ready === r.total && !r.waiting) return '';
    return [described.summary, described.queue].filter(Boolean).join('. ');
  }

  window.pwaDownloadsSyncAll = Object.freeze({
    records: records,
    verdict: verdict,
    press: press,
    state: state,
    shortfall: shortfall,
  });
})();
