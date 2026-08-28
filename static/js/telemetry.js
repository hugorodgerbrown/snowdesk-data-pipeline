/*
 * static/js/telemetry.js — First-party client telemetry buffer (SNOW-385).
 *
 * Spec §16, non-negotiable §12.11. Client observability owned end-to-end
 * (no third-party posthog-js) so a broken SW / evicted cache / dead push
 * subscription can't leave the app unable to phone home. Writes events
 * to the ``queue:events`` IndexedDB store (SNOW-375) and flushes to the
 * server-side receiver at ``POST /api/telemetry`` (SNOW-381).
 *
 * Public API — attached to ``window.pwaTelemetry``:
 *
 *   emit(event, properties?)   Enqueue an event. Respects sample rates
 *                              and the opt-in preference. Critical
 *                              events also fire ``sendBeacon`` immediately.
 *   flush()                    Manually drain the buffer. Returns a
 *                              Promise; the periodic timer and lifecycle
 *                              triggers call this too.
 *   setOptIn(bool)             Persist the toggle to ``meta:app``.
 *                              Critical events keep firing regardless
 *                              (spec §16.6) but with ``user_id`` and
 *                              ``session_id`` stripped when opt-out.
 *   isOptIn()                  Read the persisted preference. Returns
 *                              a Promise (the value lives in IndexedDB).
 *
 * All ``pwaTelemetry.emit`` call sites in other scripts use optional
 * chaining (``window.pwaTelemetry?.emit(...)``) so the file is safe to
 * omit on pages that shouldn't emit (admin, staff-only debug).
 *
 * Deliberately no posthog-js
 * --------------------------
 * Spec §16.3. The whole point is a first-party pipeline that survives
 * every failure mode client-side. posthog-js is ~40 KB and would still
 * need our own fallback path for critical events. This file is ~350
 * lines including copy-pasted comments and produces byte-for-byte
 * identical PostHog events on the server side of the pipeline.
 */

(function () {
  'use strict';

  const ENDPOINT = '/api/telemetry';

  // Batch drain: read up to BATCH_SIZE rows from queue:events per flush.
  // Chosen well below the server's 32 KiB payload cap — a 50-event batch
  // with generous properties is comfortably under.
  const BATCH_SIZE = 50;

  // Flush cadence: every 30 s while the tab is visible.
  const FLUSH_INTERVAL_MS = 30 * 1000;

  // Immediate-flush threshold: any single write that pushes the queue
  // past this depth triggers a flush without waiting for the timer.
  const FLUSH_QUEUE_THRESHOLD = 50;

  // Meta:app key that persists the opt-in preference.
  const OPT_IN_KEY = 'telemetry.opt_in';

  // Critical event names (spec §16 explicit list). These fire an
  // immediate ``navigator.sendBeacon`` even when the buffer flush is a
  // long way off, and they always fire regardless of the opt-in state
  // (with ids stripped when opt-out).
  const CRITICAL_EVENTS = Object.freeze(
    new Set([
      'pwa.kill_switch.activated',
      'pwa.forced_update.triggered',
      'pwa.reset.user_initiated',
      'pwa.reset.forced',
      'pwa.sw.fetch_undefined',
      'pwa.mutation.failed_permanent',
      'pwa.push.subscription_lost',
      'pwa.sw.activation_failed',
    ]),
  );

  // Sample rates per spec §16.3. Any event not listed defaults to 1.0
  // (always sampled). ``!!isFirstLaunch`` handles the "100% on first
  // launch" branch for the SW lifecycle events.
  const SAMPLE_RATES = Object.freeze({
    // 10% — high-volume signals used only for distribution shape.
    'pwa.freshness.fresh': 0.1,
    'pwa.freshness.stale': 0.1,
    'pwa.storage.evicted_probable': 0.1,
    // 25% for repeat launches; 100% for first launch (see _sampleRate).
    'pwa.sw.installed': 0.25,
    'pwa.sw.activated': 0.25,
    'pwa.sw.update_available': 0.25,
    'pwa.sw.update_applied': 0.25,
  });

  // EU-language prefixes used to default opt-in to FALSE for new
  // installs in the EU (spec §16.6). Language is a proxy for locale, not
  // a legal jurisdiction lookup — the user can flip the toggle on the
  // manage page. English speakers in the EU default to opt-in; that is
  // consistent with the settings-toggle-per-user story.
  const EU_LANG_PREFIXES = Object.freeze([
    'de', 'fr', 'it', 'es', 'nl', 'pl', 'pt', 'sv', 'da', 'fi',
    'el', 'hu', 'cs', 'sk', 'ro', 'bg', 'hr', 'sl', 'et', 'lv',
    'lt', 'mt', 'ga',
  ]);

  // In-memory opt-in cache — populated on first ``_optIn`` call and
  // written through on ``setOptIn``. Nullable while unresolved.
  let _optInCache = null;

  // In-memory cache for the telemetry master switch (the
  // ``pwa-telemetry-enabled`` meta tag baked in by
  // ``public.context_processors.pwa_telemetry``). Resolved once on first
  // read. Nullable while unresolved.
  let _enabledCache = null;

  /**
   * Return whether the telemetry pipeline is enabled for this page load.
   *
   * The server renders ``<meta name="pwa-telemetry-enabled" content="0">``
   * when ``settings.PWA_TELEMETRY_ENABLED`` is false — an operator-level
   * off switch (distinct from the per-user opt-in) that silences the
   * WHOLE pipeline, critical events included. Any value other than the
   * literal ``"0"`` — including a missing tag — is treated as enabled so
   * a page that omits the tag keeps the historical behaviour.
   *
   * @returns {boolean}
   */
  function _enabled() {
    if (_enabledCache !== null) return _enabledCache;
    let value = true;
    try {
      const el = document.querySelector('meta[name="pwa-telemetry-enabled"]');
      if (el && el.getAttribute('content') === '0') value = false;
    } catch (_e) {
      // No DOM / querySelector unavailable — default to enabled.
    }
    _enabledCache = value;
    return value;
  }

  /**
   * SNOW-748: true when the app is actually using the network — the interface
   * is up AND no offline mode is in force.
   *
   * ``navigator.onLine`` answers only the first half, so a mode the user
   * forced from the header toggle left this module beaconing and flushing
   * over a connection the user had just asked the app not to spend.
   * ``pwa_offline.js`` owns the answer; ``navigator.onLine`` stays the
   * fallback for a page where that module has not run.
   *
   * Nothing is dropped when this is false — events still enqueue, and the
   * ``online`` lifecycle trigger drains them when the network comes back.
   *
   * @returns {boolean}
   */
  function _networkInUse() {
    const connectivity = window.pwaConnectivity;
    return connectivity ? connectivity.isOnline() : navigator.onLine !== false;
  }

  // Flush-in-progress guard so overlapping triggers don't send the same
  // rows twice.
  let _flushInFlight = null;

  /**
   * Read the persisted opt-in value; on first call, compute the default
   * from ``navigator.language`` and persist it so a language change
   * doesn't retroactively flip the default.
   *
   * @returns {Promise<boolean>}
   */
  async function _optIn() {
    if (_optInCache !== null) return _optInCache;
    try {
      const row = await window.pwaDb.get('meta:app', OPT_IN_KEY);
      if (row && typeof row.value === 'boolean') {
        _optInCache = row.value;
        return row.value;
      }
    } catch (_e) {
      // DB unavailable — treat as opt-out (fail closed). The value is
      // NOT cached so a later successful DB call can read it fresh.
      return false;
    }
    // First launch — compute the default from navigator.language.
    const lang = (
      (navigator.languages && navigator.languages[0]) ||
      navigator.language ||
      ''
    ).toLowerCase();
    const isEu = EU_LANG_PREFIXES.some((p) => lang.startsWith(p));
    const value = !isEu;
    try {
      await window.pwaDb.put('meta:app', { key: OPT_IN_KEY, value });
    } catch (_e) {
      // Persistence failure — keep the in-memory value so this session
      // is consistent; a later ``setOptIn`` call will retry.
    }
    _optInCache = value;
    return value;
  }

  async function setOptIn(value) {
    const bool = !!value;
    _optInCache = bool;
    try {
      await window.pwaDb.put('meta:app', { key: OPT_IN_KEY, value: bool });
    } catch (_e) {
      // Non-fatal — the in-memory cache still reflects the caller's
      // intent for this session.
    }
    return bool;
  }

  function isOptIn() {
    return _optIn();
  }

  /**
   * Return the sample rate for ``event`` (1.0 default). SW lifecycle
   * events are always 100% on the first launch after install, per spec
   * — a fresh session_id in sessionStorage is the "first launch" tell.
   *
   * @param {string} event
   * @returns {number}
   */
  function _sampleRate(event) {
    const rate = SAMPLE_RATES[event];
    if (rate === undefined) return 1.0;
    // Cheap first-launch bump for the four SW lifecycle events: if this
    // page load was the first to allocate the session_id key, treat as
    // first launch and sample at 100%.
    if (event.indexOf('pwa.sw.') === 0) {
      try {
        // On the very first ``pwaDb.context()`` call in a page load, the
        // session_id was just allocated by sessionStorage. We can spot
        // "first launch after install" by checking for the install
        // timestamp — absent until we set it below.
        if (!sessionStorage.getItem('pwa.telemetry.first_launch_seen')) {
          sessionStorage.setItem('pwa.telemetry.first_launch_seen', '1');
          return 1.0;
        }
      } catch (_e) {
        // sessionStorage blocked — fall through to configured rate.
      }
    }
    return rate;
  }

  /**
   * Build the eight-field envelope for one event. When ``optIn`` is
   * false, strip user_id and session_id so the operational-safety
   * signals still fire but don't identify the user (spec §16.6).
   *
   * @param {string} event
   * @param {object} properties
   * @param {boolean} optIn
   * @returns {object}
   */
  function _buildEnvelope(event, properties, optIn) {
    const ctx = window.pwaDb.context();
    return {
      event,
      timestamp: new Date().toISOString(),
      client_version: ctx.client_version || '',
      session_id: optIn ? ctx.session_id : null,
      user_id: optIn ? ctx.user_id : null,
      platform: ctx.platform,
      install_state: ctx.install_state,
      sw_state: ctx.sw_state,
      connection: ctx.connection,
      properties: properties || {},
    };
  }

  /**
   * Fire ``navigator.sendBeacon`` for one envelope. The receiver accepts
   * a single-envelope shape as the sendBeacon fast path (see
   * ``analytics.schema.parse_payload``). Returns true on successful
   * queueing, false when the browser refused (queue full / body too
   * large) so the caller can fall back to the buffer if it wants.
   */
  function _sendBeacon(envelope) {
    try {
      if (typeof navigator === 'undefined' || !navigator.sendBeacon) {
        return false;
      }
      // Offline-integrity: no network sends while offline. The envelope has
      // already been enqueued by the caller (critical events always enqueue),
      // so skipping the beacon just defers delivery to the next flush — which
      // the ``online`` lifecycle trigger runs on reconnect — instead of
      // firing a doomed request that surfaces as a console network error.
      // SNOW-748: "offline" here means the effective connectivity, so a
      // user-forced offline mode holds the beacon back too.
      if (!_networkInUse()) {
        return false;
      }
      const blob = new Blob([JSON.stringify(envelope)], {
        type: 'application/json',
      });
      return navigator.sendBeacon(ENDPOINT, blob);
    } catch (_e) {
      return false;
    }
  }

  /**
   * Enqueue one event onto the buffer store. Rejects silently on IDB
   * failure so a broken DB never breaks the caller (analytics never
   * breaks the app).
   */
  async function _enqueue(envelope) {
    try {
      await window.pwaDb.put('queue:events', envelope);
      const depth = await window.pwaDb.count('queue:events');
      if (depth >= FLUSH_QUEUE_THRESHOLD) {
        // Fire-and-forget — we don't want emit() callers to await this.
        flush().catch(() => {});
      }
    } catch (_e) {
      // Non-fatal.
    }
  }

  /**
   * Enqueue an event, applying opt-in gate + sample rate + critical
   * sendBeacon. Returns a Promise for tests; production callers do not
   * need to await it.
   *
   * @param {string} event
   * @param {object} [properties]
   */
  async function emit(event, properties) {
    if (typeof event !== 'string' || !event) return;
    // Operator-level kill switch — silences the whole pipeline,
    // critical events included. Checked before anything touches the DB.
    if (!_enabled()) return;
    if (
      typeof window.pwaDb !== 'object' ||
      window.pwaDb.isResetRequired()
    ) {
      return;
    }
    let optIn = false;
    try {
      optIn = await _optIn();
    } catch (_e) {
      optIn = false;
    }

    const isCritical = CRITICAL_EVENTS.has(event);

    // Sample-rate gate — critical events bypass sampling entirely.
    if (!isCritical) {
      const rate = _sampleRate(event);
      if (rate < 1.0 && Math.random() >= rate) return;
    }

    const envelope = _buildEnvelope(event, properties, optIn);

    if (isCritical) {
      // Fire immediately AND enqueue for the batch flush so the receiver
      // sees the event even if the beacon queueing failed (browser
      // discretion — no delivery guarantee from sendBeacon).
      _sendBeacon(envelope);
    }

    // Standard events only reach the buffer when opt-in. Critical events
    // always go through so the flush loop retries anything sendBeacon
    // dropped.
    if (optIn || isCritical) {
      await _enqueue(envelope);
    }
  }

  /**
   * Drain the buffer to the server. Serialised — a call while one is
   * in flight returns the same promise so overlapping triggers can't
   * double-POST the same rows.
   *
   * @param {object} [opts]
   * @param {boolean} [opts.keepalive]  Set to true only on the
   *   ``pagehide`` trigger. Playwright's ``page.route`` interceptor
   *   cannot see ``keepalive`` fetches — the browser sends them
   *   outside the normal fetch pipeline — so leaving keepalive on by
   *   default would silently bypass tests. The pagehide path needs it
   *   because a normal fetch would be cancelled when the tab tears
   *   down; every other trigger runs while the tab is still alive.
   */
  function flush(opts) {
    // Nothing is ever enqueued while disabled, but guard here too so a
    // manual ``flush()`` call is a no-op rather than a stray fetch.
    if (!_enabled()) return Promise.resolve();
    // Offline-integrity: don't drain to the network while offline — the POST
    // can only fail (and litters the console with a network error, as seen
    // on the offline map). Rows stay queued; the ``online`` lifecycle
    // trigger drains them on reconnect. The pagehide keepalive path is
    // included: a keepalive fetch offline can't deliver either.
    // SNOW-748: a user-forced offline mode counts as offline here — the
    // POST would succeed, and spending the connection is exactly what the
    // user asked the app not to do.
    if (!_networkInUse()) {
      return Promise.resolve();
    }
    if (_flushInFlight) return _flushInFlight;
    const keepalive = !!(opts && opts.keepalive);
    _flushInFlight = (async () => {
      try {
        if (
          typeof window.pwaDb !== 'object' ||
          window.pwaDb.isResetRequired()
        ) {
          return;
        }
        const rows = await window.pwaDb.getAll('queue:events', BATCH_SIZE);
        if (!rows || rows.length === 0) return;
        const events = rows.map((r) => {
          // Strip the autoIncrement id — the server envelope doesn't
          // want it.
          const { id: _id, ...envelope } = r;
          return envelope;
        });
        let response;
        try {
          const init = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ events }),
          };
          if (keepalive) {
            init.keepalive = true;
          }
          response = await fetch(ENDPOINT, init);
        } catch (_networkErr) {
          // Network failure — leave rows in place for the next trigger.
          return;
        }
        if (response.status === 204 || (response.status >= 200 && response.status < 300)) {
          // Success — remove exactly the rows we just POSTed.
          await Promise.all(
            rows.map((r) => window.pwaDb.delete('queue:events', r.id).catch(() => {})),
          );
        } else if (response.status >= 400 && response.status < 500) {
          // Server rejected — drop the rows; retrying identical bad
          // payloads only wastes the rate limit.
          await Promise.all(
            rows.map((r) => window.pwaDb.delete('queue:events', r.id).catch(() => {})),
          );
        }
        // 5xx → leave rows in place, they'll retry on the next trigger.
      } finally {
        _flushInFlight = null;
      }
    })();
    return _flushInFlight;
  }

  // -------------------------------------------------------------------
  // Lifecycle triggers — attached once at module load.
  // -------------------------------------------------------------------

  function _wireLifecycle() {
    // Don't attach the flush triggers (online / visibilitychange /
    // interval / pagehide) at all when telemetry is disabled — there is
    // nothing to drain and no reason to hold a timer.
    if (!_enabled()) return;
    try {
      window.addEventListener('online', () => {
        flush().catch(() => {});
      });
    } catch (_e) {
      // Non-fatal.
    }
    try {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
          flush().catch(() => {});
        }
      });
    } catch (_e) {
      // Non-fatal.
    }
    try {
      // Periodic drain — only while the tab is active (browsers throttle
      // background timers hard). ``setInterval`` returns an id we don't
      // need to clear; the tab close tears down its own timer.
      setInterval(() => {
        if (document.visibilityState === 'visible') {
          flush().catch(() => {});
        }
      }, FLUSH_INTERVAL_MS);
    } catch (_e) {
      // Non-fatal.
    }
    try {
      // Best-effort drain on ``pagehide`` — the ONLY trigger that
      // needs ``keepalive: true`` because a normal fetch would be
      // cancelled as the tab tears down. This is also the only place
      // we drain regardless of visibility.
      window.addEventListener('pagehide', () => {
        flush({ keepalive: true }).catch(() => {});
      });
    } catch (_e) {
      // Non-fatal.
    }
  }

  Object.defineProperty(window, 'pwaTelemetry', {
    value: Object.freeze({
      emit,
      flush,
      setOptIn,
      isOptIn,
      // Exposed for tests + doc reference.
      CRITICAL_EVENTS,
      SAMPLE_RATES,
    }),
    writable: false,
    configurable: false,
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wireLifecycle);
  } else {
    _wireLifecycle();
  }
})();
