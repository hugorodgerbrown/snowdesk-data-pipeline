// @ts-check
/*
 * static/js/downloads_restore_queue_core.js — the "Download all" queue
 * (SNOW-1140).
 *
 * A signed-in user on a new device sees their other device's areas under
 * "Not on this device" in the Manage downloads sheet. Each row has its own
 * "Download here"; "Download all" runs those same per-row paths for every
 * restorable row, ONE AT A TIME. This module is the ordering and the stop
 * rules, and nothing else: no DOM, no strings, no storage. Every effect is
 * an injected dependency, so the rules are unit-tested directly
 * (tests/js/test_downloads_restore_queue_core.js) and the sheet
 * (map_downloads_manager.js) supplies the real ones.
 *
 * ## Why one at a time
 *
 * Every pinned download on the page takes one lock
 * (``window.pwaBasemapDownloads.isBusy()`` — ``pinnedDownloadBusy`` in
 * map_basemap_downloads.js): the runs share one progress grid and one
 * storage budget, and a second start while the first holds the lock is
 * refused with ``'busy'``. Both start paths resolve once the run has
 * STARTED, not once it has finished, so the queue waits for the lock to
 * clear before it starts the next. The wait listens for the page's
 * ``snowdesk:downloads-changed`` broadcast and re-checks ``isBusy()``; a
 * poll backs the event up, because a run that fails records nothing and so
 * broadcasts nothing; and an upper bound stops a hung run from wedging the
 * queue for the life of the page.
 *
 * ## The stop rules, in the order they are asked
 *
 *   offline  The network mode reads offline before a start — the queue
 *            stops and the rest stay rows. Asked before and after the wait,
 *            because the wait can be long.
 *   timeout  The lock did not clear within the bound.
 *   failed   The PREVIOUS run settled and its area is not on this device —
 *            it failed, or the user declined an eviction it asked about.
 *            That run has already said so (the shared download-error toast,
 *            or the user's own "no"), so this stop carries no reason of its
 *            own to show; ``silent`` is true.
 *   refusal  A start resolved ``started: false`` — over budget, quota,
 *            sign-in, busy, an area too big for this device. The refusal's
 *            own ``reason`` travels back, so the caller words it with the
 *            same toast the row's own "Download here" would have shown.
 *
 * An area that became present while the queue ran (another tab, a tap on
 * its own row) is SKIPPED, not re-fetched: running again would replace a
 * good download.
 *
 * Exposes ``window.pwaDownloadsRestoreQueueCore``:
 *
 *   runQueue(items, deps, options) → Promise<QueueResult>
 *   waitForIdle(deps, options)     → Promise<boolean>
 *   DEFAULT_TIMEOUT_MS, DEFAULT_POLL_MS
 */
(function () {
  'use strict';

  // Fifteen minutes for ONE run to release the lock. A region at the
  // ceiling over a slow connection takes minutes, not tens of them; a run
  // still holding the lock after this is stuck (or waiting on an eviction
  // banner nobody is answering), and the queue gives up rather than start
  // the next behind it.
  var DEFAULT_TIMEOUT_MS = 15 * 60 * 1000;

  // The backstop for the broadcast. A run that fails releases the lock
  // without recording anything, so no ``snowdesk:downloads-changed`` fires
  // for it; a cheap re-check every couple of seconds catches that.
  var DEFAULT_POLL_MS = 2000;

  /**
   * @typedef {{areaId: string}} QueueItem
   *   One restorable row. The queue reads only ``areaId``; everything else
   *   on the item is the caller's, handed back to ``deps.start``.
   *
   * @typedef {{started: boolean, reason?: string, ok?: boolean}} StartOutcome
   *
   * @typedef {Object} QueueDeps
   * @property {() => boolean} online Whether the app may use the network.
   * @property {() => boolean} isBusy Whether a pinned download holds the
   *   page-wide lock.
   * @property {(listener: () => void) => (() => void)} subscribe Call
   *   ``listener`` whenever what this device holds may have changed;
   *   returns the unsubscribe.
   * @property {() => Promise<Set<string>>} presentIds The area ids on this
   *   device now.
   * @property {(item: any) => Promise<StartOutcome>} start Start one item.
   *
   * @typedef {{timeoutMs?: number, pollMs?: number}} WaitOptions
   *
   * @typedef {Object} QueueResult
   * @property {number} started How many runs the queue started.
   * @property {number} skipped How many items were already present.
   * @property {string|null} stopped ``null`` when the queue ran to the end;
   *   otherwise ``'offline'``, ``'timeout'``, ``'failed'`` or the refusing
   *   start's own reason (``'unknown'`` when it gave none).
   * @property {boolean} silent Whether the stop has already been reported
   *   by the run itself, so the caller should say nothing.
   * @property {any} [item] The item the queue stopped at.
   */

  /**
   * Resolve once the page-wide download lock is free, or the bound passes.
   *
   * @param {QueueDeps} deps
   * @param {WaitOptions} [options]
   * @returns {Promise<boolean>} ``true`` when the lock is free, ``false``
   *   when the bound passed first.
   */
  function waitForIdle(deps, options) {
    var opts = options || {};
    var timeoutMs = typeof opts.timeoutMs === 'number' ? opts.timeoutMs : DEFAULT_TIMEOUT_MS;
    var pollMs = typeof opts.pollMs === 'number' ? opts.pollMs : DEFAULT_POLL_MS;
    if (!deps.isBusy()) return Promise.resolve(true);
    return new Promise(function (resolve) {
      var finished = false;
      /** @type {() => void} */
      var unsubscribe = function () {};
      /** @type {ReturnType<typeof setInterval> | undefined} */
      var poll;
      /** @type {ReturnType<typeof setTimeout> | undefined} */
      var deadline;
      /** @param {boolean} value */
      function done(value) {
        if (finished) return;
        finished = true;
        unsubscribe();
        if (poll !== undefined) clearInterval(poll);
        if (deadline !== undefined) clearTimeout(deadline);
        resolve(value);
      }
      function check() {
        if (!deps.isBusy()) done(true);
      }
      unsubscribe = deps.subscribe(check);
      poll = setInterval(check, pollMs);
      deadline = setTimeout(function () {
        done(false);
      }, timeoutMs);
    });
  }

  /**
   * Run every item through ``deps.start``, one at a time, until the list
   * ends or a stop rule fires (see the module header for the rules).
   *
   * @param {Array<QueueItem>} items In the order they are listed.
   * @param {QueueDeps} deps
   * @param {WaitOptions} [options] The bound and poll for each wait.
   * @returns {Promise<QueueResult>} Never rejects: a throwing dependency
   *   stops the queue as ``'failed'``, not silently.
   */
  async function runQueue(items, deps, options) {
    var list = Array.isArray(items) ? items : [];
    /** @type {QueueResult} */
    var result = { started: 0, skipped: 0, stopped: null, silent: false };
    /** @type {QueueItem | null} */
    var previous = null;

    /**
     * @param {string} reason
     * @param {any} item
     * @param {boolean} [silent]
     * @returns {QueueResult}
     */
    function stop(reason, item, silent) {
      result.stopped = reason;
      result.item = item;
      result.silent = !!silent;
      return result;
    }

    try {
      for (var i = 0; i < list.length; i += 1) {
        var item = list[i];
        if (!item || !item.areaId) continue;
        if (!deps.online()) return stop('offline', item);
        var idle = await waitForIdle(deps, options);
        if (!idle) return stop('timeout', item);
        // The wait can be long; the mode may have changed under it.
        if (!deps.online()) return stop('offline', item);
        var present = await deps.presentIds();
        // The previous run has settled (the lock is free). Its area not
        // being here means it failed or was declined, and it said so.
        if (previous && !present.has(previous.areaId)) return stop('failed', previous, true);
        previous = null;
        if (present.has(item.areaId)) {
          result.skipped += 1;
          continue;
        }
        var outcome = await deps.start(item);
        if (!outcome || !outcome.started) {
          return stop((outcome && outcome.reason) || 'unknown', item);
        }
        result.started += 1;
        // A restore that resolves after settling says whether it recorded
        // the area; a false there is a run that already reported itself.
        if (outcome.ok === false) return stop('failed', item, true);
        previous = item;
      }
    } catch (_err) {
      return stop('failed', null);
    }
    return result;
  }

  window.pwaDownloadsRestoreQueueCore = Object.freeze({
    DEFAULT_TIMEOUT_MS: DEFAULT_TIMEOUT_MS,
    DEFAULT_POLL_MS: DEFAULT_POLL_MS,
    waitForIdle: waitForIdle,
    runQueue: runQueue,
  });
})();
