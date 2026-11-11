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
 * SNOW-1487: the run says which area of how many it is on, and the step
 * and counts of that area; Stop, or the page going out of sight, ends it
 * (`stop`), and the closing toast names every area with its outcome
 * (`closing`).
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
    'sync-all-progress': 'Syncing %(n)s of %(total)s areas',
    'sync-stage-map': 'Checking the map… (%(n)s of %(total)s)',
    'sync-stage-terrain': 'Saving terrain… (%(n)s of %(total)s)',
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

  /**
   * The current Sync all.
   *
   * @typedef {Object} RunState
   * @property {boolean} running
   * @property {string} current The area it is on, '' between areas.
   * @property {Object|null} step The step it reported last.
   * @property {Object|null} result Its result, once settled.
   * @property {string[]} areas The areas it runs over, in order.
   * @property {AbortController|null} controller Stops it (SNOW-1487).
   * @property {Map<string, Object>} answers Each area's `syncArea` answer.
   */
  /** @type {RunState|null} */
  let run = null;

  /**
   * SNOW-1487: stop the run in flight. The area it is on stops its fetch
   * (`syncArea`'s signal), and the areas after it are left as they were.
   * Nothing to stop is a no-op.
   *
   * @returns {void}
   */
  function stop() {
    if (run && run.running && run.controller) run.controller.abort();
  }

  // SNOW-1487: a page that goes out of sight stops its sync. A phone
  // locked in a pocket with a run going would otherwise keep fetching on
  // a radio about to go, and the user would not see it end; the notice
  // under each row offers Carry on instead.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') stop();
  });

  /**
   * Run Sync all over `areaIds`, calling `onChange` after every step and
   * once more when the run settles. A second press while one is running
   * is ignored and resolves to null.
   *
   * SNOW-1487: `onChange` is called with `{progress: true}` when all that
   * moved is the counts of the stage an area is on, so the sheet can
   * repaint that one row rather than every row.
   *
   * @param {Array<string>} areaIds Bucket ids, in the sheet's order.
   * @param {function({progress: boolean}=): void} onChange
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
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    /** @type {RunState} */
    const current = {
      running: true,
      current: '',
      step: null,
      result: null,
      areas: areaIds.slice(),
      controller: controller,
      answers: new Map(),
    };
    run = current;
    notify();
    if (contentRun && !(await contentRun.take())) {
      run = null;
      notify();
      return null;
    }
    const queue = /** @type {any} */ (window).pwaMutationQueue;
    const signal = controller ? controller.signal : undefined;
    try {
      current.result = await core.run({
        drain: () => (queue && typeof queue.drain === 'function' ? queue.drain() : undefined),
        waiting: waitingMutations,
        areas: areaIds.slice(),
        signal: signal,
        syncOne: async (/** @type {string} */ areaId, /** @type {Function} */ onStage) => {
          const answer = await bridge.syncArea(areaId, onStage, signal);
          current.answers.set(areaId, answer);
          return core.areaOutcome(answer);
        },
        onStep: (/** @type {any} */ step) => {
          const previous = /** @type {any} */ (current.step);
          current.step = step;
          if (step.phase === 'area') current.current = step.id;
          if (step.phase === 'done') current.current = '';
          const countsOnly =
            step.phase === 'stage' &&
            !!previous &&
            previous.phase === 'stage' &&
            previous.id === step.id &&
            previous.name === step.name;
          notify(countsOnly ? { progress: true } : undefined);
        },
      });
    } catch (_err) {
      current.result = { total: areaIds.length, ready: 0, waiting: 0, outcomes: [], skipped: [] };
    }
    if (contentRun) contentRun.release('user');
    const settled = /** @type {any} */ (current.result);
    settled.answers = current.answers;
    current.running = false;
    current.current = '';
    notify();
    return current.result;
  }

  /**
   * The run's state, for the painter.
   *
   * SNOW-1487: `index` and `count` are the area it is on and how many
   * there are ("Syncing 2 of 5 areas"); `step` the stage that area is on
   * with its counts; `pending` the areas still to come, which the sheet
   * says are waiting.
   *
   * @returns {{running: boolean, current: string, label: string, index: number,
   *   count: number, step: ({name: string, done: number|null, total: number|null}|null),
   *   pending: string[]}}
   */
  function state() {
    const running = !!(run && run.running);
    if (!running || !run) {
      // SNOW-1416: the background sync's area shows on its row too.
      const contentRun = /** @type {any} */ (window).pwaBasemapDownloads?.contentRun;
      const auto = contentRun && contentRun.current() === 'auto' ? contentRun.area() : '';
      return { running: false, current: auto || '', label: '', index: 0, count: 0, step: null, pending: [] };
    }
    const step = /** @type {any} */ (run.step);
    const t = strings();
    const count = run.areas.length;
    const index = step && (step.phase === 'area' || step.phase === 'stage') ? step.n : 0;
    const label = index
      ? fill(t['sync-all-progress'], { n: index, total: count })
      : fill(t['ready-running'], {});
    const at = run.current ? run.areas.indexOf(run.current) : -1;
    const pending = run.areas.slice(at >= 0 ? at + 1 : index);
    const counts = step && step.phase === 'stage' && step.counts ? step.counts : null;
    return {
      running: true,
      current: run.current,
      label: label,
      index: index,
      count: count,
      step:
        step && step.phase === 'stage'
          ? { name: step.name, done: counts ? counts.done : null, total: counts ? counts.total : null }
          : null,
      pending: pending,
    };
  }

  /**
   * SNOW-1487: one area's outcome, in the closing toast's terms — up to
   * date, did not finish (a failed fetch or addition), stopped, or no
   * longer on this device.
   *
   * @param {any} answer `syncArea`'s answer, or undefined for an area the
   *   run never reached.
   * @returns {'ready'|'unfinished'|'stopped'|'absent'}
   */
  function outcomeOf(answer) {
    const core = /** @type {any} */ (window).pwaOfflineReadyCore;
    if (!answer) return 'stopped';
    if (answer.cancelled) return 'stopped';
    const outcome = core ? core.areaOutcome(answer) : 'partial';
    if (outcome === 'absent') return 'absent';
    const steps = Array.isArray(answer.steps) ? answer.steps : [];
    return outcome === 'ready' && steps.length === 0 ? 'ready' : 'unfinished';
  }

  /**
   * SNOW-1487: what the closing toast says — every area the run was
   * pressed over, in order, with its outcome, and the queue line when
   * edits were left waiting.
   *
   * @param {Object|null} result `press`'s result.
   * @param {string[]} areaIds The areas it was pressed over.
   * @returns {{areas: Array<{id: string, outcome: string}>, queue: string}}
   */
  function closing(result, areaIds) {
    const core = /** @type {any} */ (window).pwaOfflineReadyCore;
    const r = /** @type {any} */ (result || {});
    const answers = r.answers instanceof Map ? r.answers : new Map();
    const skipped = Array.isArray(r.skipped) ? r.skipped : [];
    // An area the run reached with no answer threw: it did not finish.
    const areas = (areaIds || []).map((id) => ({
      id: id,
      outcome: answers.has(id) || skipped.indexOf(id) >= 0 ? outcomeOf(answers.get(id)) : 'unfinished',
    }));
    const queue = result && core ? core.describe(result, strings()).queue : '';
    return { areas: areas, queue: queue };
  }

  /**
   * `%(name)s` substitution, by name, as `pwaStrings.interpolate` does.
   *
   * @param {string|undefined} template
   * @param {Record<string, string|number>} values
   * @returns {string}
   */
  function fill(template, values) {
    return String(template || '').replace(/%\((\w+)\)s/g, (match, key) =>
      Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match,
    );
  }

  window.pwaDownloadsSyncAll = Object.freeze({
    records: records,
    verdict: verdict,
    press: press,
    state: state,
    stop: stop,
    outcomeOf: outcomeOf,
    closing: closing,
  });
})();
