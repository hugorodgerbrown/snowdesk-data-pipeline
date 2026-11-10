// @ts-check
/*
 * static/js/offline_ready_core.js — "Get ready for offline", the one press
 * (SNOW-1141).
 *
 * The network menu used to offer one Sync button per downloaded area
 * (SNOW-951). Nothing sent the edits waiting in the mutation queue, nothing
 * refreshed every area at once, and a person in a car park with a bar of
 * signal had to press each row in turn and read each answer. This module is
 * the one press that replaced them: its order, and the summary it ends on.
 *
 * No DOM, no strings of its own, no storage. Every effect is an injected
 * dependency, so the rules are unit-tested directly
 * (tests/js/test_offline_ready_core.js) and the two hosts supply the real
 * ones:
 *
 *   - the network menu on the map page (``pwa_offline.js``), where each
 *     area goes through ``window.pwaBasemapDownloads.syncArea``;
 *   - ``/offline/?sync=all`` (``offline_audit.js``), where each area goes
 *     through that page's own content fetch and the report re-runs over
 *     the result.
 *
 * ## The order
 *
 *   1. The mutation queue drains FIRST. A queued edit is the user's own
 *      work and the only thing on this device the server does not already
 *      have; an area's bulletins can be fetched again tomorrow, a pin
 *      dropped offline cannot be. The drain is bounded (``drainTimeoutMs``)
 *      because a dead radio hangs rather than rejects
 *      (docs/decisions/bounded-offline-read-paths.md), and a hung POST must
 *      not stop the areas from being tried.
 *   2. Then every area, ONE AT A TIME, in the order given. Each run already
 *      fetches its own list in turn, and two at once over a connection
 *      about to go would leave two half-done areas rather than one whole
 *      one.
 *
 * An area that throws is not an exception to the run: it is an area that
 * is not ready, and the next one is still tried.
 *
 * ## Outcomes
 *
 *   ready    The area's tiles are established and its content landed.
 *   partial  Something fell short — a repair that failed, content that did
 *            not land, or tiles this page could not judge at all.
 *   absent   This device no longer holds the area's tiles. A sync cannot
 *            mend that and never claims to; the remedy is a re-download.
 *
 * Exposes ``window.pwaOfflineReadyCore``:
 *
 *   run(deps)                    → Promise<RunResult>
 *   areaOutcome(syncResult)      → Outcome
 *   waitingCount(rows)           → number
 *   describe(result, strings)    → {summary: string, queue: string}
 *   progressText(step, strings)  → string
 *   STAGES
 *   DEFAULT_DRAIN_TIMEOUT_MS
 */
(function () {
  'use strict';

  // Long enough for one batch of queued edits over a slow connection; short
  // enough that a hung request does not cost the areas behind it the signal
  // they were waiting for.
  const DEFAULT_DRAIN_TIMEOUT_MS = 20000;

  // The stage names a sync may report, in the order it fetches them. A
  // name outside the list is dropped rather than painted as a blank.
  // SNOW-1487: `terrain` sits between the map and the content, as a sync
  // adds the terrain data after the map.
  const STAGES = ['map', 'terrain', 'bulletins', 'weather', 'things'];

  /** @typedef {'ready'|'partial'|'absent'} Outcome */
  /** @typedef {{tiles?: string, content?: boolean}} SyncResult */
  /**
   * SNOW-1487: how far a stage has got — files landed of files needed.
   *
   * @typedef {{done: number, total: number}} StageCounts
   */
  /**
   * @typedef {{phase: 'queue'} | {phase: 'area', n: number, total: number, id: string}
   *   | {phase: 'stage', n: number, total: number, id: string, name: StageName,
   *      counts?: StageCounts}
   *   | {phase: 'done', id: string, outcome: Outcome}} Step
   */
  /**
   * What an area's sync is fetching now (SNOW-1415): the tiles, the
   * terrain data (SNOW-1487), the bulletins, the weather or the reader's
   * own things.
   *
   * @typedef {'map'|'terrain'|'bulletins'|'weather'|'things'} StageName
   */
  /**
   * @typedef {Object} RunDeps
   * @property {function(): unknown} drain Sends the queued edits.
   * @property {function(): Promise<number>|number} waiting How many edits
   *   are still waiting once the drain has had its turn.
   * @property {string[]} areas Area ids, in the order to run them.
   * @property {function(string, function(StageName, StageCounts=): void): Promise<Outcome>|Outcome} syncOne
   *   Runs one area. The second argument is told each stage as the area's
   *   sync starts it (SNOW-1415), and SNOW-1487 its counts as they move;
   *   a host that cannot name its stages ignores it.
   * @property {function(Step): void} [onStep] Told of every step as it starts
   *   and every area as it settles.
   * @property {AbortSignal} [signal] SNOW-1487: once aborted, no further
   *   area is started; the ones left are `skipped`.
   * @property {number} [drainTimeoutMs]
   * @property {function(number): Promise<void>} [wait] Injected timer.
   */
  /**
   * @typedef {Object} RunResult
   * @property {number} total
   * @property {number} ready
   * @property {number} waiting
   * @property {Array<{id: string, outcome: Outcome}>} outcomes
   * @property {string[]} skipped SNOW-1487: the areas a stop left unsynced.
   */

  /**
   * One ``syncArea`` answer as an outcome.
   *
   * Only ``'ok'`` (mended what was missing) and ``'none'`` (nothing was
   * missing) are claims that the tiles are established — see
   * docs/decisions/a-pre-departure-sync-is-unconditional.md. ``'unknown'``
   * is not, so it is ``partial`` rather than ``ready``.
   *
   * @param {SyncResult|null|undefined} result
   * @returns {Outcome}
   */
  function areaOutcome(result) {
    if (!result) return 'partial';
    if (result.tiles === 'absent') return 'absent';
    const tilesOk = result.tiles === 'ok' || result.tiles === 'none';
    return tilesOk && !!result.content ? 'ready' : 'partial';
  }

  /**
   * How many ``queue:mutations`` rows are still waiting to send.
   *
   * A row marked ``failed`` is not waiting: it will never be retried, and
   * the nav's sync badge already reports it as an error
   * (docs/mutation-queue.md). Counting it here would promise a send that is
   * not coming.
   *
   * @param {unknown} rows
   * @returns {number}
   */
  function waitingCount(rows) {
    if (!Array.isArray(rows)) return 0;
    return rows.filter((row) => !!row && row.status !== 'failed').length;
  }

  /**
   * @param {number} ms
   * @returns {Promise<void>}
   */
  function defaultWait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /**
   * Drain, then every area in turn.
   *
   * @param {RunDeps} deps
   * @returns {Promise<RunResult>}
   */
  async function run(deps) {
    const onStep = typeof deps.onStep === 'function' ? deps.onStep : () => {};
    const wait = typeof deps.wait === 'function' ? deps.wait : defaultWait;
    const timeoutMs = Number.isFinite(deps.drainTimeoutMs)
      ? Number(deps.drainTimeoutMs)
      : DEFAULT_DRAIN_TIMEOUT_MS;
    const areas = Array.isArray(deps.areas) ? deps.areas.slice() : [];

    onStep({ phase: 'queue' });
    try {
      await Promise.race([Promise.resolve(deps.drain()), wait(timeoutMs)]);
    } catch (_err) {
      // A drain that throws has still left the rows where they were; the
      // count below says so.
    }
    let waiting = 0;
    try {
      const count = await deps.waiting();
      waiting = Number.isFinite(count) && count > 0 ? count : 0;
    } catch (_err) {
      waiting = 0;
    }

    /** @type {Array<{id: string, outcome: Outcome}>} */
    const outcomes = [];
    /** @type {string[]} */
    const skipped = [];
    let ready = 0;
    for (let index = 0; index < areas.length; index += 1) {
      const id = areas[index];
      const n = index + 1;
      // SNOW-1487: a stop leaves every area not yet started as it was.
      if (deps.signal && deps.signal.aborted) {
        skipped.push(...areas.slice(index));
        break;
      }
      onStep({ phase: 'area', n: n, total: areas.length, id: id });
      // A host that reports progress repeats the stage it is on, so only
      // a change of stage is a step — or, SNOW-1487, a change of counts
      // within it, which is how a row says "212 of 528".
      let lastStage = '';
      let lastCounts = '';
      /**
       * @param {StageName} name
       * @param {StageCounts} [counts]
       */
      const onStage = (name, counts) => {
        if (STAGES.indexOf(name) < 0) return;
        const known = !!counts && Number.isFinite(counts.done) && Number.isFinite(counts.total);
        const key = known ? counts.done + '/' + counts.total : '';
        if (name === lastStage && key === lastCounts) return;
        if (name === lastStage && !known) return;
        lastStage = name;
        lastCounts = key;
        /** @type {Step} */
        const step = { phase: 'stage', n: n, total: areas.length, id: id, name: name };
        if (known) step.counts = { done: counts.done, total: counts.total };
        onStep(step);
      };
      /** @type {Outcome} */
      let outcome = 'partial';
      try {
        const answer = await deps.syncOne(id, onStage);
        if (answer === 'ready' || answer === 'absent') outcome = answer;
      } catch (_err) {
        outcome = 'partial';
      }
      if (outcome === 'ready') ready += 1;
      outcomes.push({ id: id, outcome: outcome });
      onStep({ phase: 'done', id: id, outcome: outcome });
    }
    return { total: areas.length, ready: ready, waiting: waiting, outcomes: outcomes, skipped: skipped };
  }

  /**
   * ``%(name)s`` substitution, by name — the same rule as
   * ``pwaStrings.interpolate``, restated so this core needs no other
   * module loaded.
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

  /**
   * The button's words while a run is going.
   *
   * A ``stage`` step names what is being fetched ("Weather (2 of 3)")
   * from ``sync-stage-<name>``, falling back to the plain count where the
   * caller has no word for that stage.
   *
   * @param {Step|null} step
   * @param {Record<string, string>} strings Carries ``ready-running``,
   *   ``ready-progress`` and, optionally, ``sync-stage-map``,
   *   ``sync-stage-terrain``, ``sync-stage-bulletins``,
   *   ``sync-stage-weather`` and ``sync-stage-things``.
   * @returns {string}
   */
  function progressText(step, strings) {
    if (step && step.phase === 'stage' && strings['sync-stage-' + step.name]) {
      return fill(strings['sync-stage-' + step.name], { n: step.n, total: step.total });
    }
    if (step && (step.phase === 'area' || step.phase === 'stage')) {
      return fill(strings['ready-progress'], { n: step.n, total: step.total });
    }
    return fill(strings['ready-running'], {});
  }

  /**
   * The press's outcome, in the caller's words.
   *
   * One summary line — every area ready, or how many of them are — and a
   * second line only when the drain left edits behind.
   *
   * @param {RunResult} result
   * @param {Record<string, string>} strings Carries ``ready-all``,
   *   ``ready-all-one``, ``ready-some``, ``queue-waiting`` and
   *   ``queue-waiting-one``.
   * @returns {{summary: string, queue: string}}
   */
  function describe(result, strings) {
    let summary = '';
    if (result.total > 0) {
      if (result.ready === result.total) {
        summary =
          result.total === 1
            ? fill(strings['ready-all-one'], {})
            : fill(strings['ready-all'], { total: result.total });
      } else {
        summary = fill(strings['ready-some'], { ready: result.ready, total: result.total });
      }
    }
    let queue = '';
    if (result.waiting === 1) queue = fill(strings['queue-waiting-one'], {});
    else if (result.waiting > 1) queue = fill(strings['queue-waiting'], { n: result.waiting });
    return { summary: summary, queue: queue };
  }

  window.pwaOfflineReadyCore = Object.freeze({
    DEFAULT_DRAIN_TIMEOUT_MS: DEFAULT_DRAIN_TIMEOUT_MS,
    run: run,
    areaOutcome: areaOutcome,
    waitingCount: waitingCount,
    describe: describe,
    progressText: progressText,
    STAGES: STAGES,
  });
})();
