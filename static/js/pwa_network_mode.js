// @ts-check
/*
 * static/js/pwa_network_mode.js — the network mode as a VALUE, owned in
 * one place (SNOW-922).
 *
 * ## The bug this exists for
 *
 * ``'offline-forced'`` is the user's standing instruction not to spend
 * their connection, and ``sw.js`` keeps that promise completely:
 * ``_shouldUseNetwork()`` answers false for every read path, HTML
 * navigations included. The mode is persisted to ``meta:app``
 * (``network.mode``) and re-hydrated by the worker on every boot, so it
 * survives the worker being recycled, the tab being closed, and the
 * device being restarted.
 *
 * On a device whose map page is cached that is exactly right. On one
 * whose map page is NOT cached for the account signed in — a fresh
 * install, a sign-in as someone else, the window after a deploy before
 * ``activate`` has re-warmed the shell — every navigation falls through
 * ``_networkFirstFallback`` to ``offline.html``, and the only control
 * that clears the mode is the switch in the network menu
 * (``includes/_connection_panel.html``, moved there from the account
 * dropdown by SNOW-921), which is
 * inside the app that will not open. The exit was behind the door it
 * locks, and a live signal made no difference: the worker refuses the
 * network, not the radio.
 *
 * The two ways out were "Reset local data" — which also destroys every
 * downloaded region, saved place and queued mutation — and clearing site
 * data in browser settings. Neither is a thing to ask of someone in a car
 * park at the bottom of a lift.
 *
 * ## Why a module rather than a second copy of the rule
 *
 * ``pwa_offline.js`` already knew how to change the mode; the recovery
 * page could not use it, because that module binds to nav markup this
 * page does not have and reads ``window.pwaDb``, which it does not load.
 * The answer is not a second implementation of the persist-then-announce
 * rule — the rule is exactly the thing that must not drift, since a page
 * that announces without persisting is re-stranded by the next worker
 * restart, and one that persists without announcing leaves the live
 * worker in the old mode until it is recycled.
 *
 * So the rule lives here, with two callers: ``pwa_offline.js`` (the app's
 * nav switch) and ``static/offline.html`` (the recovery page's). Small
 * enough to precache alongside ``pwa_reset.js``, which it sits beside for
 * the same reason — a control that renders bound to nothing is worse than
 * absent on a page someone reaches when everything else has failed.
 *
 * ## Storage is an adapter, not a choice
 *
 * ``window.pwaDb`` is the app's real database layer: it owns the schema
 * version, the upgrade path and the Reset-Required state
 * (docs/indexeddb-scaffolding.md). Where it is loaded it MUST be the one
 * that writes, or an app-side write would quietly bypass all of that.
 * Where it is not — the recovery page, which loads no ``db.js`` — a
 * direct versionless open of the same store is the only option, and a
 * versionless open never triggers a migration, so it cannot damage a
 * schema it does not know about. ``_store`` picks between them per call
 * rather than at load time, because ``db.js`` is deferred and may not
 * have published itself yet when this module runs.
 *
 * ## Every read is bounded
 *
 * Per docs/decisions/bounded-offline-read-paths.md: a wedged IndexedDB
 * hangs rather than rejecting, and this module is on the path of a page
 * whose whole purpose is to work when things are broken. A read that does
 * not answer resolves to its fallback, and the fallback is always the
 * answer that claims least.
 */

(function () {
  'use strict';

  // The same database ``db.js`` and ``offline_audit.js`` open. Declared
  // here rather than imported because this module has to work on a page
  // that loads neither.
  var DB_NAME = 'snowdesk-pwa-v1';
  var META_STORE = 'meta:app';

  // The ``meta:app`` key the mode is persisted under. ``sw.js``'s
  // ``NETWORK_MODE_KEY`` and ``pwa_offline.js``'s are the same string;
  // all three must agree or a mode written by one is invisible to the
  // others.
  var KEY = 'network.mode';

  /**
   * The three modes, and what each one means.
   *
   *   ``'auto'``           — ordinary bounded network reads.
   *   ``'offline'``        — the worker LATCHED itself after three
   *                          read-path timeouts. Its own inference, and
   *                          it probes to lift it.
   *   ``'offline-forced'`` — the USER asked for it. Never probed; only
   *                          the user ends it.
   *
   * @type {['auto', 'offline', 'offline-forced']}
   */
  var MODES = ['auto', 'offline', 'offline-forced'];

  // Long enough for a cold IndexedDB open on a slow phone, short enough
  // that a wedged store does not leave a switch looking dead. The user is
  // holding a page that has already failed them once; a control that
  // never settles is the same failure again.
  var READ_BUDGET_MS = 2000;

  // The worker has to start up, open a cache and read a stamp to answer
  // ``canOpenOffline``. More generous than a DB read for that reason, and
  // still bounded: a worker that does not answer must not hold a
  // confirmation dialogue open.
  var WORKER_BUDGET_MS = 3000;

  /**
   * Resolve ``promise``, or ``fallback`` if it has not settled inside
   * ``ms``. Never rejects — a caller here always has a safe answer, and
   * making each one write its own catch is how one of them ends up
   * without it.
   *
   * @template T
   * @param {Promise<T>} promise
   * @param {number} ms
   * @param {T} fallback
   * @returns {Promise<T>}
   */
  function bounded(promise, ms, fallback) {
    return new Promise(function (resolve) {
      var settled = false;
      var timer = setTimeout(function () {
        if (settled) return;
        settled = true;
        resolve(fallback);
      }, ms);
      var finish = function (/** @type {T} */ value) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      };
      promise.then(finish, function () {
        finish(fallback);
      });
    });
  }

  /**
   * Narrow an arbitrary value to one of the three modes.
   *
   * Used on every inbound path — the persisted row, the worker's
   * announcement, a caller's argument — because each can carry a value
   * written by a different version of this code than the one reading it.
   * Anything unrecognised becomes ``'auto'``, which is the only mode that
   * claims nothing.
   *
   * @param {*} value
   * @returns {'auto'|'offline'|'offline-forced'}
   */
  function coerce(value) {
    if (value === 'offline' || value === 'offline-forced') return value;
    return 'auto';
  }

  /**
   * True where the mode stops the app reaching the server at all. Both
   * offline modes do; they differ only in who decided and whether the
   * worker will probe its way out.
   *
   * @param {*} value
   * @returns {boolean}
   */
  function blocksNetwork(value) {
    return coerce(value) !== 'auto';
  }

  /**
   * True only for the mode the user chose. The distinction matters to
   * every caller that offers a way out: a latch lifts itself when a probe
   * finds a route, so telling someone to press a switch for it would be
   * advice for a state that may already be gone. A forced mode has no
   * evidence to expire.
   *
   * @param {*} value
   * @returns {boolean}
   */
  function isForced(value) {
    return coerce(value) === 'offline-forced';
  }

  /**
   * A versionless open of the shared database, for the context that has
   * no ``db.js``.
   *
   * Versionless is load-bearing rather than lazy: it joins the connection
   * queue without ever requesting an upgrade, so this module can never
   * migrate a schema it does not know the shape of. The connection is
   * released on ``versionchange`` for the mirror-image reason — holding
   * it would block ``db.js``'s own migration after an app update.
   *
   * @returns {Promise<IDBDatabase|null>}
   */
  function openRaw() {
    return new Promise(function (resolve) {
      if (!self.indexedDB) {
        resolve(null);
        return;
      }
      /** @type {IDBOpenDBRequest} */
      var request;
      try {
        request = self.indexedDB.open(DB_NAME);
      } catch (_err) {
        resolve(null);
        return;
      }
      request.onsuccess = function () {
        var db = request.result;
        if (db) {
          db.onversionchange = function () {
            try {
              db.close();
            } catch (_err2) {
              // Non-fatal.
            }
          };
        }
        resolve(db || null);
      };
      request.onerror = function () {
        resolve(null);
      };
      // Fires only for an upgrade this open is holding up, which a
      // versionless open never is. Kept because the reverse — queued
      // BEHIND someone else's upgrade — fires nothing at all, and that is
      // the case the budget above is really for.
      request.onblocked = function () {
        resolve(null);
      };
    });
  }

  /**
   * One raw ``meta:app`` read. Resolves null for a missing store, a
   * missing row or any failure — all of which mean the same thing to
   * every caller: nothing has been written here.
   *
   * @param {string} key
   * @returns {Promise<*>}
   */
  async function readRaw(key) {
    var opened = await openRaw();
    if (!opened) return null;
    var db = opened;
    try {
      return await new Promise(function (resolve) {
        if (!db.objectStoreNames.contains(META_STORE)) {
          resolve(null);
          return;
        }
        var request = db.transaction(META_STORE, 'readonly').objectStore(META_STORE).get(key);
        request.onsuccess = function () {
          resolve(request.result ? request.result.value : null);
        };
        request.onerror = function () {
          resolve(null);
        };
      });
    } catch (_err) {
      return null;
    } finally {
      try {
        db.close();
      } catch (_err) {
        // Non-fatal.
      }
    }
  }

  /**
   * One raw ``meta:app`` write.
   *
   * Resolves false rather than throwing where the store is missing — a
   * database the worker created holds only ``queue:mutations`` (see
   * ``sw.js``'s ``_openMutationsDb``), and on such a device there is no
   * persisted mode to correct in the first place. The caller still
   * announces to the worker, which is what makes the change take effect
   * now; the write is what makes it survive a restart.
   *
   * @param {string} key
   * @param {*} value
   * @returns {Promise<boolean>}
   */
  async function writeRaw(key, value) {
    var opened = await openRaw();
    if (!opened) return false;
    var db = opened;
    try {
      return await new Promise(function (resolve) {
        if (!db.objectStoreNames.contains(META_STORE)) {
          resolve(false);
          return;
        }
        var tx = db.transaction(META_STORE, 'readwrite');
        var request = tx.objectStore(META_STORE).put({ key: key, value: value });
        request.onsuccess = function () {
          resolve(true);
        };
        request.onerror = function () {
          resolve(false);
        };
        tx.onabort = function () {
          resolve(false);
        };
      });
    } catch (_err) {
      return false;
    } finally {
      try {
        db.close();
      } catch (_err) {
        // Non-fatal.
      }
    }
  }

  /**
   * Read the persisted mode.
   *
   * Prefers ``window.pwaDb`` where the app has loaded it, so an app-side
   * read goes through the same layer every other app-side read does.
   *
   * @returns {Promise<'auto'|'offline'|'offline-forced'>}
   */
  async function read() {
    var db = self.pwaDb;
    var value = null;
    if (db && typeof db.get === 'function') {
      var row = await bounded(
        Promise.resolve()
          .then(function () {
            return db.get(META_STORE, KEY);
          })
          .catch(function () {
            return null;
          }),
        READ_BUDGET_MS,
        null,
      );
      value = row ? row.value : null;
    } else {
      value = await bounded(readRaw(KEY), READ_BUDGET_MS, null);
    }
    return coerce(value);
  }

  /**
   * Persist the mode. Best-effort and never throws: the announcement
   * below is what changes behaviour now, and a device that cannot be
   * written to is one where the user simply has to say so again after a
   * restart — worse than the alternative, and much better than a rejected
   * promise leaving a switch mid-flight.
   *
   * @param {'auto'|'offline'|'offline-forced'} mode
   * @returns {Promise<boolean>}
   */
  async function persist(mode) {
    var db = self.pwaDb;
    if (db && typeof db.put === 'function') {
      return await bounded(
        Promise.resolve()
          .then(function () {
            return db.put(META_STORE, { key: KEY, value: mode });
          })
          .then(function () {
            return true;
          })
          .catch(function () {
            return false;
          }),
        READ_BUDGET_MS,
        false,
      );
    }
    return await bounded(writeRaw(KEY, mode), READ_BUDGET_MS, false);
  }

  /**
   * Tell the worker which mode to be in.
   *
   * Swallows the absence of a controller, which is an ordinary state
   * rather than an error: a page loaded before the worker activated has
   * none. The persisted row is re-asserted on the next boot, so the
   * user's choice is not lost — it takes effect a load later.
   *
   * @param {'auto'|'offline'|'offline-forced'} mode
   */
  function announce(mode) {
    try {
      navigator.serviceWorker?.controller?.postMessage({ type: 'network-mode', mode: mode });
    } catch (_err) {
      // No controller, or messaging unavailable. See above.
    }
  }

  /**
   * Change the mode: persist it, then tell the worker.
   *
   * That ORDER is the whole point of this module existing. Announcing
   * first would leave a window in which the worker is in the new mode and
   * the disk still holds the old one, and a worker recycled inside that
   * window — Chrome kills an idle one after about thirty seconds — comes
   * back in the mode the user just left. For the ``'auto'`` direction
   * that window is the bug this module was written for, re-opened.
   *
   * Resolves once both have happened, so a caller that reloads the page
   * can wait for it and know the reload will not race its own write.
   *
   * @param {'auto'|'offline'|'offline-forced'} mode
   * @returns {Promise<'auto'|'offline'|'offline-forced'>} The mode set.
   */
  async function set(mode) {
    var next = coerce(mode);
    await persist(next);
    announce(next);
    return next;
  }

  /**
   * Will the app open on this device with no network — right now, for
   * whoever is signed in?
   *
   * Asked of the SERVICE WORKER rather than answered here, and that is
   * the point: the worker is the thing that will or will not serve the
   * page, it already holds the shell cache and the principal stamp, and
   * asking it means there is exactly one implementation of "would
   * ``_networkFirstFallback`` find a page for this request". A page-side
   * copy would be a second answer to a question that already has one, and
   * the two would drift.
   *
   * Answers false on any doubt — no worker, no controller, no reply
   * inside the budget, an unrecognised reply. Its callers use it to
   * decide whether to WARN, and a warning shown to someone whose map is
   * in fact saved costs them one extra press; a warning withheld from
   * someone whose map is not costs them the app.
   *
   * @returns {Promise<boolean>}
   */
  function canOpenOffline() {
    var controller = navigator.serviceWorker?.controller;
    if (!controller) return Promise.resolve(false);
    /** @type {MessageChannel} */
    var channel;
    try {
      channel = new MessageChannel();
    } catch (_err) {
      return Promise.resolve(false);
    }
    var answered = new Promise(function (resolve) {
      channel.port1.onmessage = function (/** @type {MessageEvent} */ event) {
        var data = event && event.data;
        resolve(!!(data && data.type === 'can-open-offline' && data.canOpen === true));
      };
    });
    try {
      controller.postMessage({ type: 'can-open-offline' }, [channel.port2]);
    } catch (_err) {
      return Promise.resolve(false);
    }
    return bounded(answered, WORKER_BUDGET_MS, false).then(function (result) {
      try {
        channel.port1.close();
      } catch (_err) {
        // Non-fatal.
      }
      return result;
    });
  }

  // Frozen, like every other ``window.pwa*`` surface
  // (docs/offline-map.md): the publish channel is a contract, not a
  // scratch object. Declared in static/js/globals.d.ts.
  self.pwaNetworkMode = Object.freeze({
    KEY: KEY,
    MODES: MODES,
    coerce: coerce,
    blocksNetwork: blocksNetwork,
    isForced: isForced,
    read: read,
    set: set,
    announce: announce,
    canOpenOffline: canOpenOffline,
  });
})();
