/*
 * static/js/mutation_queue.js — Client mutation-queue surface (SNOW-376).
 *
 * Fills in the real queue behind the frozen ``window.pwaMutationQueue``
 * surface stubbed in SNOW-384 — no call-site changes required for any
 * existing adopter. Writes to the ``queue:mutations`` IndexedDB store
 * (SNOW-375, ``static/js/db.js``) and replays it with exponential backoff,
 * an ``Idempotency-Key`` header identical across every retry (server
 * contract: ``core/idempotency.py``), and Background Sync registration so
 * a queued mutation can still flush after the tab has closed (Android
 * Chromium; feature-detected — a no-op on browsers without
 * ``SyncManager``, e.g. iOS Safari).
 *
 * Ships ONLY the machinery. Scope decision (ticket comment): the existing
 * HTMX (``hx-post``) subscription/push call sites are NOT converted to JS
 * calls in this ticket — the real consumer is future field-observation
 * submission (SNOW-330).
 *
 * Row shape (``queue:mutations``, see docs/mutation-queue.md):
 *
 *   { id, idempotency_key, method, url, headers, body, created_at,
 *     attempts, status, next_attempt_at, principal }
 *
 *   status ∈ 'queued' | 'retry-scheduled' | 'failed'
 *   next_attempt_at — epoch ms; the row is eligible for replay once
 *   ``Date.now() >= next_attempt_at``.
 *   principal — the ``<meta name="pwa-user-id">`` value (or ``null`` for
 *   anonymous) at ENQUEUE time (SNOW-462). Stamped so a mutation queued
 *   by one account can never replay under a different one on the same
 *   browser — see "Account-change partitioning" below.
 *
 * Classification / backoff (static/js/mutation_queue_core.js):
 *
 *   - 2xx                              → success: delete the row.
 *   - {408, 429}, 5xx, network error    → retry: attempts += 1,
 *     next_attempt_at = now + backoffDelayMs(attempts)
 *     (2/4/8/…/256s, then capped at 300s from attempt 9).
 *     attempts >= MAX_ATTEMPTS (20) without a 2xx → 'failed', no
 *     further retry.
 *   - any other 4xx                     → 'failed' on the FIRST attempt,
 *     no further retry — the request itself is wrong, not the connection.
 *
 * A permanently failed row (either branch above) emits the critical
 * ``pwa.mutation.failed_permanent`` event, reveals the full-width
 * ``#mutation-queue-toast`` banner (``templates/includes/_toast_banner.html``),
 * and refreshes the nav sync badge into its error colouring.
 *
 * Drain triggers (mirrors static/js/telemetry.js's ``_wireLifecycle``):
 * ``online``, ``visibilitychange`` → visible, a periodic timer,
 * ``pagehide``, and immediately after ``enqueue`` when ``navigator.onLine``.
 * All of them funnel through one in-flight guard (``_drainInFlight``) so
 * concurrent triggers never double-POST the same row, and through one
 * ``navigator.onLine`` guard in ``drain()`` so an offline trigger cannot
 * spend a row's retry budget on a POST that can only fail.
 *
 * Public API — attached to ``window.pwaMutationQueue``:
 *
 *   enqueue(operation)            Persist one mutation to the queue,
 *                                  mint its Idempotency-Key, stamp the
 *                                  enqueue-time principal, emit
 *                                  ``pwa.mutation.enqueued``, drain
 *                                  immediately if online, and
 *                                  feature-detect a Background Sync
 *                                  registration.
 *   drain()                       Replay every eligible row. Emits
 *                                  ``pwa.mutation.drained`` with the
 *                                  REAL count of rows removed by a 2xx.
 *   markFailed(operation, reason) Caller-driven permanent-failure report
 *                                  (for a mutation attempted outside the
 *                                  queue). Emits
 *                                  ``pwa.mutation.failed_permanent`` and
 *                                  reveals the same toast + badge state
 *                                  as an internally-failed row.
 *   clear()                       Empty the entire ``queue:mutations``
 *                                  store and refresh the nav badge
 *                                  (SNOW-462) — used internally by the
 *                                  account-change reconcile below, and
 *                                  available to any other caller that
 *                                  needs to discard the whole queue.
 *
 * ``operation`` is caller-supplied metadata describing the mutation (e.g.
 * ``{method: 'POST', url: '/account/', headers: {...}, body: '...'}``).
 * Callers must not include any of the PII keys the server-side telemetry
 * receiver blocks (``email``, ``ip``, ``token``, ``credential_id``) — the
 * same rule as every other ``window.pwaTelemetry.emit`` call site.
 *
 * Account-change partitioning (SNOW-462)
 * ---------------------------------------
 * Offline mutations carry no principal binding by default, so a browser
 * shared across a sign-out/sign-in — user A queues offline, signs out,
 * user B signs in before reconnect — could otherwise replay A's mutation
 * attributed to B. Defence in depth, two layers:
 *
 *   1. **Reconcile on load** — ``_reconcilePrincipal()`` runs first thing
 *      in ``_wireLifecycle()`` (fire-and-forget). It compares the current
 *      ``<meta name="pwa-user-id">`` principal against the last-seen one
 *      persisted in ``meta:app`` under the key ``mutations.principal``.
 *      On a mismatch it clears the ENTIRE queue (emitting
 *      ``pwa.mutation.discarded`` — ``reason: 'account_change'`` when a
 *      previous principal was recorded and genuinely differs, or
 *      ``reason: 'principal_uninitialised'`` when no ``mutations.principal``
 *      row existed yet — when at least one row was present) and persists
 *      the new principal.
 *   2. **Drain-guard** — ``_processRow()`` re-checks each row's stamped
 *      ``principal`` against the CURRENT principal immediately before
 *      replay; a mismatch deletes the row without a network request and
 *      emits ``pwa.mutation.discarded`` with ``reason:
 *      'principal_mismatch'``. This is the race backstop for the window
 *      between an account change and the next reconcile (e.g. a
 *      Background-Sync firing from the service worker — see
 *      ``static/js/sw.js``'s best-effort ``storedPrincipal`` guard, which
 *      discards silently and does NOT emit telemetry: it only runs with
 *      no tab open, so ``_postTelemetry``'s ``clients.matchAll`` is a
 *      guaranteed no-op there).
 *
 * Every method is defensive — wrapped in try/catch, optional chaining on
 * ``window.pwaTelemetry?.emit`` and guarded ``window.pwaDb`` access — so a
 * missing IndexedDB / telemetry module (this file loads on admin pages
 * too) or ``pwaDb.isResetRequired()`` can never break the caller's
 * mutation flow; the operation is simply not queued.
 */

(function () {
  'use strict';

  var STORE = 'queue:mutations';

  // Batch cap per drain pass — mirrors telemetry.js's BATCH_SIZE
  // convention so one drain trigger can't process an unbounded backlog in
  // a single synchronous run.
  var BATCH_SIZE = 50;

  // Periodic drain cadence — same 30s cadence as telemetry.js's flush
  // timer, only while the tab is visible (browsers throttle background
  // timers hard, same rationale as telemetry.js).
  var DRAIN_INTERVAL_MS = 30 * 1000;

  // Auto-dismiss the failure toast after 10s (ticket-resolved design
  // decision) — the "×" button and a fresh failure both also dismiss/
  // re-arm it.
  var TOAST_AUTO_DISMISS_MS = 10 * 1000;

  var TOAST_ID = 'mutation-queue-toast';
  var BADGE_SELECTOR = '[data-sync-badge]';

  // Matches _toast_banner.html's `duration-300` Tailwind class. The
  // `transitionend` listener is the primary signal that the slide-up has
  // finished; this is only the fallback for browsers/states where it
  // doesn't fire (e.g. the element was already mid-transition, or
  // `prefers-reduced-motion` short-circuits the animation).
  var TOAST_TRANSITION_MS = 300;

  // Drain-in-progress guard — mirrors telemetry.js's _flushInFlight so
  // overlapping triggers (online + visibilitychange + timer all firing
  // close together) can't double-POST the same row.
  var _drainInFlight = null;

  // Handle for the toast's auto-dismiss timer so a second failure while
  // the first toast is still showing resets the 10s window instead of
  // stacking timers.
  var _toastDismissTimer = null;

  // Handles for the in-progress dismiss transition, so a second dismiss
  // (or a fresh reveal) while one is already playing cancels the stale
  // listener/timer rather than piling up duplicates.
  var _toastHideFallbackTimer = null;
  var _toastHideTransitionEndHandler = null;

  /**
   * Best-effort telemetry emit — never throws.
   * @param {string} event
   * @param {object} [properties]
   */
  function emitTelemetry(event, properties) {
    try {
      window.pwaTelemetry?.emit(event, properties || {});
    } catch (_err) {
      // Ignore — telemetry must never break a mutation call site.
    }
  }

  /**
   * The current principal — the ``<meta name="pwa-user-id">`` value read
   * via ``window.pwaDb.context().user_id``, normalised so an empty
   * string / ``undefined`` reads as ``null`` (anonymous), matching the
   * value a signed-out session stamps on a queued row. Guarded end to
   * end: returns ``null`` on any throw, or when ``window.pwaDb`` isn't
   * available at all (this file loads on admin pages too).
   * @returns {string|null}
   */
  function _currentPrincipal() {
    try {
      if (typeof window.pwaDb !== 'object') return null;
      var userId = window.pwaDb.context().user_id;
      return userId === undefined || userId === '' ? null : userId;
    } catch (_e) {
      return null;
    }
  }

  /**
   * Generate an idempotency key. ``crypto.randomUUID`` is available on
   * every browser this queue targets; the hex fallback mirrors
   * ``db.js``'s ``_randomHex`` for the rare runtime without it.
   * @returns {string}
   */
  function _mintIdempotencyKey() {
    try {
      if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
      }
      var bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      var out = '';
      for (var i = 0; i < bytes.length; i++) {
        out += bytes[i].toString(16).padStart(2, '0');
      }
      return out;
    } catch (_e) {
      // No crypto at all — fall back to a low-entropy but still-unique-
      // enough value so the queue keeps working rather than throwing.
      return 'mut-' + Date.now().toString(16) + '-' + Math.floor(Math.random() * 1e9).toString(16);
    }
  }

  // -------------------------------------------------------------------
  // Toast (full-width, top-of-page — templates/includes/_toast_banner.html)
  // -------------------------------------------------------------------

  function _toastEl() {
    try {
      return document.getElementById(TOAST_ID);
    } catch (_e) {
      return null;
    }
  }

  /**
   * Cancel any in-progress dismiss transition — its `transitionend`
   * listener and fallback timer — without touching the element's classes.
   * Called before both a fresh reveal and a fresh hide so repeated calls
   * never leave stale listeners/timers behind.
   */
  function _cancelToastHideTransition(el) {
    if (_toastHideFallbackTimer) {
      clearTimeout(_toastHideFallbackTimer);
      _toastHideFallbackTimer = null;
    }
    if (_toastHideTransitionEndHandler) {
      try {
        el?.removeEventListener('transitionend', _toastHideTransitionEndHandler);
      } catch (_e) {
        // Non-fatal.
      }
      _toastHideTransitionEndHandler = null;
    }
  }

  /**
   * Reveal the permanent-failure toast and (re)arm its 10s auto-dismiss
   * timer. Uses a Tailwind class toggle (not custom CSS) for the
   * slide-down reveal: remove ``hidden`` first, force a layout flush so
   * the browser doesn't collapse the ``hidden``-removal and the
   * transform/opacity flip into the same frame (which would skip the
   * transition entirely), then flip the transform/opacity classes.
   */
  function _revealToast() {
    try {
      var el = _toastEl();
      if (!el) return;
      // A reveal always wins over an in-progress dismiss.
      _cancelToastHideTransition(el);
      el.classList.remove('hidden');
      el.offsetWidth; // force layout — see comment above.
      el.classList.remove('-translate-y-full', 'opacity-0');
      el.classList.add('translate-y-0', 'opacity-100');
      if (_toastDismissTimer) clearTimeout(_toastDismissTimer);
      _toastDismissTimer = setTimeout(_hideToast, TOAST_AUTO_DISMISS_MS);
    } catch (_e) {
      // Non-fatal — a broken toast must never break the mutation flow.
    }
  }

  /**
   * Dismiss the toast — played from both the "×" button and the 10s
   * auto-dismiss timer. The reveal and dismiss are symmetric: this plays
   * the slide-up/fade-out transition FIRST, and only adds ``hidden``
   * (display:none) once that transition has actually finished — via a
   * ``transitionend`` listener, with a ``setTimeout`` fallback matching
   * the template's ``duration-300`` in case ``transitionend`` never fires
   * (browser quirk, or the element was already mid-transition). Flipping
   * both class changes in the same tick — as an earlier version of this
   * function did — collapses the animation to nothing; ``hidden`` sets
   * ``display: none`` immediately, so the browser never gets a frame in
   * which to render the transform/opacity in flight.
   */
  function _hideToast() {
    try {
      var el = _toastEl();
      if (_toastDismissTimer) {
        clearTimeout(_toastDismissTimer);
        _toastDismissTimer = null;
      }
      if (!el || el.classList.contains('hidden')) return;
      _cancelToastHideTransition(el);

      el.classList.remove('translate-y-0', 'opacity-100');
      el.classList.add('-translate-y-full', 'opacity-0');

      var finish = function () {
        _cancelToastHideTransition(el);
        el.classList.add('hidden');
      };
      _toastHideTransitionEndHandler = finish;
      el.addEventListener('transitionend', finish);
      _toastHideFallbackTimer = setTimeout(finish, TOAST_TRANSITION_MS + 50);
    } catch (_e) {
      // Non-fatal.
    }
  }

  function _wireToast() {
    try {
      var el = _toastEl();
      if (!el) return;
      el.querySelector('[data-action="dismiss"]')?.addEventListener('click', _hideToast);
    } catch (_e) {
      // Non-fatal — markup may be absent on pages that don't include the
      // partial (e.g. admin).
    }
  }

  // -------------------------------------------------------------------
  // Nav sync badge (templates/includes/nav.html [data-sync-badge])
  // -------------------------------------------------------------------

  /**
   * Refresh the nav badge from the live ``queue:mutations`` depth: text
   * shows the non-``failed`` count (singular/plural), hidden entirely
   * when that count is zero, coloured into the status-error tokens when
   * any row in the store has failed.
   * @returns {Promise<void>}
   */
  async function _updateBadge() {
    try {
      var el = document.querySelector(BADGE_SELECTOR);
      if (!el) return;
      if (typeof window.pwaDb !== 'object' || window.pwaDb.isResetRequired()) {
        el.classList.add('hidden');
        el.classList.remove('inline-flex');
        return;
      }
      var rows = (await window.pwaDb.getAll(STORE)) || [];
      var pending = rows.filter(function (r) {
        return r.status !== 'failed';
      });
      var anyFailed = rows.some(function (r) {
        return r.status === 'failed';
      });
      if (pending.length === 0) {
        el.classList.add('hidden');
        el.classList.remove('inline-flex');
        return;
      }
      // i18n: English-only pre-launch (docs/i18n.md) — plain JS string,
      // not wrapped, matching every other dynamically-generated JS
      // string in the PWA layer today.
      el.textContent =
        pending.length === 1 ? '1 change queued' : pending.length + ' changes queued';
      // SNOW-445: swap `hidden` for `inline-flex` so the two display utilities
      // are never both present (which would otherwise leak an empty pill).
      el.classList.remove('hidden');
      el.classList.add('inline-flex');
      if (anyFailed) {
        el.classList.remove('bg-tag', 'text-text-3', 'border-border');
        el.classList.add('bg-status-error-bg', 'text-status-error-text', 'border-status-error-text');
      } else {
        el.classList.remove('bg-status-error-bg', 'text-status-error-text', 'border-status-error-text');
        el.classList.add('bg-tag', 'text-text-3', 'border-border');
      }
    } catch (_e) {
      // Non-fatal — a badge refresh failure must never break the queue.
    }
  }

  // -------------------------------------------------------------------
  // Background Sync registration (feature-detected)
  // -------------------------------------------------------------------

  /**
   * Register the shared Background Sync tag so Android Chromium can
   * replay the queue even after every tab has closed. Wrapped end-to-end
   * in a promise chain that swallows every rejection — browsers without
   * ``SyncManager`` (notably iOS Safari) simply skip this with no error,
   * relying entirely on the page-lifecycle drain triggers instead.
   */
  function _registerBackgroundSync() {
    try {
      if (!('serviceWorker' in navigator)) return;
      var tag = window.pwaMutationQueueCore ? window.pwaMutationQueueCore.SYNC_TAG : 'mutation-queue';
      navigator.serviceWorker.ready
        .then(function (registration) {
          if ('sync' in registration) {
            return registration.sync.register(tag);
          }
          return undefined;
        })
        .catch(function () {
          // Feature not supported, or registration failed transiently —
          // the lifecycle drain triggers are the fallback path either way.
        });
    } catch (_e) {
      // Non-fatal.
    }
  }

  // -------------------------------------------------------------------
  // Row processing
  // -------------------------------------------------------------------

  /**
   * Mark a row (and, separately, any caller-supplied operation metadata)
   * as permanently failed: persist ``status: 'failed'``, emit the
   * critical telemetry event, reveal the toast, and refresh the badge.
   *
   * @param {object} row The stored queue row.
   * @param {string} reason Short machine-identifier for the failure.
   */
  async function _markRowFailed(row, reason) {
    var updated = Object.assign({}, row, { status: 'failed' });
    try {
      await window.pwaDb.put(STORE, updated);
    } catch (_e) {
      // Non-fatal — still surface the failure below even if the write
      // itself couldn't be persisted.
    }
    emitTelemetry('pwa.mutation.failed_permanent', {
      method: row.method,
      url: row.url,
      idempotency_key: row.idempotency_key,
      attempts: updated.attempts,
      reason: reason,
    });
    // Generic DOM signal so a consumer can undo its optimistic UI for the
    // failed mutation (SNOW-479: favourites.js drops the pending pin when a
    // queued create is permanently rejected, e.g. the 409 at the favourites
    // cap). Kept queue-neutral — the payload names the row, consumers filter.
    try {
      document.dispatchEvent(
        new CustomEvent('pwa:mutation-failed-permanent', {
          detail: { method: row.method, url: row.url, reason: reason },
        }),
      );
    } catch (_e) {
      // Non-fatal — the toast + badge below are the primary user signal.
    }
    _revealToast();
    _updateBadge().catch(function () {});
  }

  /**
   * Replay one eligible row. Returns
   * ``'success' | 'failed' | 'retry' | 'discarded'`` describing the
   * outcome so ``drain()`` can count real successes — a ``'discarded'``
   * row never left this device, so it must not count as either.
   *
   * @param {object} row
   * @returns {Promise<'success'|'failed'|'retry'|'discarded'>}
   */
  async function _processRow(row) {
    // SNOW-462 drain-guard: the row's enqueue-time principal must still
    // match the current one — the race backstop for the window between
    // an account change and the next reconcile (see _reconcilePrincipal
    // and the module header's "Account-change partitioning" section).
    if (row.principal !== _currentPrincipal()) {
      try {
        await window.pwaDb.delete(STORE, row.id);
      } catch (_e) {
        // Non-fatal — still report the discard below even if the delete
        // itself couldn't be persisted.
      }
      emitTelemetry('pwa.mutation.discarded', {
        method: row.method,
        url: row.url,
        idempotency_key: row.idempotency_key,
        reason: 'principal_mismatch',
      });
      return 'discarded';
    }

    var core = window.pwaMutationQueueCore;
    if (!core) {
      // The shared helper script didn't load — treat as a transient
      // condition rather than guessing at classification logic here.
      return 'retry';
    }

    var headers = Object.assign({}, row.headers || {}, {
      // Identical value across every replay of this row — the server-side
      // contract (core/idempotency.py) relies on that to deduplicate.
      'Idempotency-Key': row.idempotency_key,
    });

    var outcome;
    try {
      var response = await fetch(row.url, {
        method: row.method,
        headers: headers,
        body: row.body != null ? row.body : undefined,
      });
      outcome = core.classifyStatus(response.status);
    } catch (_networkErr) {
      outcome = 'retry';
    }

    if (outcome === 'success') {
      await window.pwaDb.delete(STORE, row.id);
      return 'success';
    }

    // Both remaining branches represent an attempt that just happened —
    // increment before persisting/reporting so `attempts` (and the
    // telemetry it feeds) reflects the failed attempt rather than reading
    // as "never attempted".
    var attempts = (row.attempts || 0) + 1;

    if (outcome === 'permanent') {
      await _markRowFailed(Object.assign({}, row, { attempts: attempts }), 'permanent_4xx');
      return 'failed';
    }

    // 'retry' — {408, 429}, 5xx, or a network error.
    if (attempts >= core.MAX_ATTEMPTS) {
      await _markRowFailed(Object.assign({}, row, { attempts: attempts }), 'max_attempts');
      return 'failed';
    }
    var updated = Object.assign({}, row, {
      attempts: attempts,
      status: 'retry-scheduled',
      next_attempt_at: Date.now() + core.backoffDelayMs(attempts),
    });
    await window.pwaDb.put(STORE, updated);
    return 'retry';
  }

  // -------------------------------------------------------------------
  // Public surface
  // -------------------------------------------------------------------

  /**
   * Enqueue one mutation. Mints a fresh Idempotency-Key, persists the row,
   * drains immediately when online, and feature-detects a Background
   * Sync registration for the offline/tab-closed case.
   *
   * @param {object} [operation] Caller-supplied mutation descriptor:
   *   ``{method, url, headers, body}``. ``method`` defaults to ``'POST'``.
   * @returns {Promise<void>}
   */
  async function enqueue(operation) {
    var op = operation || {};
    emitTelemetry('pwa.mutation.enqueued', op);
    try {
      if (typeof window.pwaDb !== 'object' || window.pwaDb.isResetRequired()) {
        return;
      }
      var row = {
        idempotency_key: _mintIdempotencyKey(),
        method: (op.method || 'POST').toUpperCase(),
        url: op.url,
        headers: op.headers || {},
        body: op.body !== undefined ? op.body : null,
        created_at: new Date().toISOString(),
        attempts: 0,
        status: 'queued',
        next_attempt_at: Date.now(),
        principal: _currentPrincipal(),
      };
      await window.pwaDb.put(STORE, row);
      await _updateBadge();
      if (navigator.onLine) {
        drain().catch(function () {});
      }
      _registerBackgroundSync();
    } catch (_e) {
      // Non-fatal — enqueue must never throw for the caller.
    }
  }

  /**
   * Drain every eligible row, replaying it with its identical
   * Idempotency-Key. Serialised — a call while one is already in flight
   * returns the same promise so concurrent triggers can't double-POST.
   *
   * @returns {Promise<void>}
   */
  function drain() {
    // Offline-integrity: don't replay to the network while offline — the
    // POST can only fail, and each failure classifies as 'retry', spends one
    // of the row's MAX_ATTEMPTS and lengthens its backoff. Rows stay queued;
    // the ``online`` lifecycle trigger drains them on reconnect. Mirrors
    // telemetry.js's flush() guard and the navigator.onLine check enqueue()
    // already makes before its own drain.
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      return Promise.resolve();
    }
    if (_drainInFlight) return _drainInFlight;
    _drainInFlight = (async () => {
      var drained = 0;
      try {
        if (typeof window.pwaDb !== 'object' || window.pwaDb.isResetRequired()) {
          return;
        }
        var core = window.pwaMutationQueueCore;
        var rows = (await window.pwaDb.getAll(STORE, BATCH_SIZE)) || [];
        var now = Date.now();
        var eligible = rows.filter(function (row) {
          return core ? core.isRowEligible(row, now) : row.status !== 'failed';
        });
        for (const row of eligible) {
          // Sequential, not parallel — keeps replay order stable and
          // avoids concurrent writes to the same store racing each other.
          var result = await _processRow(row);
          if (result === 'success') drained += 1;
        }
      } catch (_e) {
        // Non-fatal — a drain failure must never break the caller.
      } finally {
        emitTelemetry('pwa.mutation.drained', { count: drained });
        // SNOW-479: once ≥1 row synced we are online and server state has
        // moved, so tell the map to refetch its favourites overlay — this is
        // what replaces an offline-created pin's synthetic "pending" marker
        // with the authoritative server pin. Kept queue-neutral (fires for any
        // successful drain, not just favourites); map.js's listener is
        // eligible-gated and a no-op when nothing changed, so a report-only
        // drain harmlessly triggers one cheap favourites refetch.
        if (drained > 0) {
          try {
            document.dispatchEvent(
              new CustomEvent('snowdesk:favourites-changed'),
            );
          } catch (_e2) {
            // Non-fatal — the drain itself already succeeded.
          }
        }
        _drainInFlight = null;
        _updateBadge().catch(function () {});
      }
    })();
    return _drainInFlight;
  }

  /**
   * Caller-driven permanent-failure report for a mutation attempted
   * outside the queue. Emits the critical ``pwa.mutation.failed_permanent``
   * event and surfaces the same toast + badge state as an internally
   * failed row.
   *
   * @param {object} [operation] Caller-supplied mutation descriptor.
   * @param {string} [reason] Short machine-identifier for the failure.
   * @returns {Promise<void>}
   */
  async function markFailed(operation, reason) {
    emitTelemetry('pwa.mutation.failed_permanent', {
      ...(operation || {}),
      reason: reason || 'unknown',
    });
    _revealToast();
    await _updateBadge().catch(function () {});
  }

  /**
   * Empty the entire ``queue:mutations`` store and refresh the nav
   * badge. Guarded/non-fatal — used internally by
   * ``_reconcilePrincipal()`` on an account change, and available to any
   * other caller that needs to discard the whole queue outright.
   *
   * @returns {Promise<void>}
   */
  async function clear() {
    try {
      if (typeof window.pwaDb !== 'object' || window.pwaDb.isResetRequired()) {
        return;
      }
      await window.pwaDb.clear(STORE);
      await _updateBadge();
    } catch (_e) {
      // Non-fatal — clear() must never throw for the caller.
    }
  }

  // -------------------------------------------------------------------
  // Account-change reconcile (SNOW-462) — see the module header's
  // "Account-change partitioning" section.
  // -------------------------------------------------------------------

  /**
   * Compare the current principal against the last-seen one persisted in
   * ``meta:app`` (key ``mutations.principal``). On a mismatch — the
   * browser signed in as someone else since the last page load — clear
   * the whole queue (so nothing queued under the previous principal can
   * ever replay) and persist the new principal. Run first thing in
   * ``_wireLifecycle()``, fire-and-forget: the drain-guard in
   * ``_processRow()`` is the race backstop, so this must not block
   * wiring the rest of the lifecycle.
   *
   * The ``pwa.mutation.discarded`` ``reason`` distinguishes two mismatch
   * causes so a PostHog query can tell them apart: ``'account_change'``
   * when a ``mutations.principal`` row already existed and genuinely
   * differs from the current principal, versus
   * ``'principal_uninitialised'`` when no such row existed yet (first
   * load after this ticket shipped, or a fresh DB) — any pre-existing,
   * principal-less row is untrustworthy and cleared on general
   * principle, not because an actual account change was observed.
   *
   * Entirely guarded/non-fatal — a failure here leaves the drain-guard
   * as the sole (still airtight, if less proactive) protection.
   *
   * @returns {Promise<void>}
   */
  async function _reconcilePrincipal() {
    try {
      if (typeof window.pwaDb !== 'object' || window.pwaDb.isResetRequired()) {
        return;
      }
      var current = _currentPrincipal();
      var stored = await window.pwaDb.get('meta:app', 'mutations.principal');
      var wasUninitialised = stored === undefined;
      var storedValue = wasUninitialised ? undefined : stored.value;
      if (!wasUninitialised && storedValue === current) {
        return;
      }
      var rows = (await window.pwaDb.getAll(STORE)) || [];
      if (rows.length > 0) {
        await clear();
        emitTelemetry('pwa.mutation.discarded', {
          count: rows.length,
          reason: wasUninitialised ? 'principal_uninitialised' : 'account_change',
        });
      }
      await window.pwaDb.put('meta:app', { key: 'mutations.principal', value: current });
    } catch (_e) {
      // Non-fatal — see the docstring above.
    }
  }

  // -------------------------------------------------------------------
  // Lifecycle triggers — mirrors telemetry.js's _wireLifecycle.
  // -------------------------------------------------------------------

  function _wireLifecycle() {
    // SNOW-462: fire-and-forget — must not block wiring the rest of the
    // lifecycle. The drain-guard in _processRow is the race backstop.
    _reconcilePrincipal().catch(() => {});
    try {
      window.addEventListener('online', () => {
        drain().catch(() => {});
      });
    } catch (_e) {
      // Non-fatal.
    }
    try {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
          drain().catch(() => {});
        }
      });
    } catch (_e) {
      // Non-fatal.
    }
    try {
      setInterval(() => {
        if (document.visibilityState === 'visible') {
          drain().catch(() => {});
        }
      }, DRAIN_INTERVAL_MS);
    } catch (_e) {
      // Non-fatal.
    }
    try {
      // Best-effort drain as the tab tears down, and the only trigger that
      // runs regardless of visibility. Unlike telemetry.js's mirror of this
      // trigger there is no ``keepalive`` counterpart: a replay carries a
      // body and an Idempotency-Key, so a fetch cut short by the teardown
      // leaves the row queued for the next load or for the Background Sync
      // registration made at enqueue time, and the server deduplicates a
      // replay that did reach it.
      window.addEventListener('pagehide', () => {
        drain().catch(() => {});
      });
    } catch (_e) {
      // Non-fatal.
    }
    _wireToast();
    // Reflect any rows left over from a previous session/tab close.
    _updateBadge().catch(() => {});
  }

  Object.defineProperty(window, 'pwaMutationQueue', {
    value: Object.freeze({ enqueue, drain, markFailed, clear }),
    writable: false,
    configurable: false,
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wireLifecycle);
  } else {
    _wireLifecycle();
  }
})();
