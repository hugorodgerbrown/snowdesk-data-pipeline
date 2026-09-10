/*
 * static/js/error_reporting.js — uncaught JavaScript errors reach the
 * telemetry pipeline (SNOW-894).
 *
 * Until this file, nothing in static/js registered `window.onerror` or an
 * `unhandledrejection` listener. Every uncaught exception and every rejected
 * promise in the browser was invisible in production: nothing logged
 * anywhere we could read, nothing forwarded to PostHog. The 2026-09-10 map
 * review raised it because of the shape of map.js — one 8,000-line IIFE, so
 * a throw anywhere in it silently kills everything below the throw rather
 * than producing a visibly broken widget — but the gap was site-wide.
 *
 * The pipeline itself was already built and already running: telemetry.js
 * buffers to IndexedDB, beacons critical events immediately, and posts to
 * /api/telemetry. Nothing was feeding it errors. This file is only the
 * plumbing between the two.
 *
 * WHAT IS SENT, AND WHEN
 * ----------------------
 * `js.error` is a CRITICAL event (telemetry.js's CRITICAL_EVENTS), which
 * means two things: it beacons immediately rather than waiting for the next
 * flush, and it fires whatever the opt-in preference says. Both matter for
 * this event specifically — the crash most worth seeing is the one that
 * kills the page, and a page that has died does not flush its own buffer.
 *
 * Firing regardless of opt-in is a real cost, so the payload is not the same
 * for both. telemetry.js already strips `user_id` and `session_id` from an
 * opted-out envelope; this module additionally reduces its OWN properties:
 *
 *   opted in   {message, filename, lineno, colno, stack, pathname, kind}
 *   opted out  {pathname, kind}
 *
 * So an opted-out visitor contributes the fact that something broke on this
 * path, and nothing about what. That keeps the signal that would otherwise
 * be lost — "the map is crashing for a whole class of device" is visible
 * from paths alone — without collecting diagnostics from someone who
 * declined to be diagnosed. Decision recorded on SNOW-894.
 *
 * `search` is NEVER included, on either branch, and `pathname` is sent
 * rather than `href` for exactly that reason. The map's own URLs carry
 * `?route_share=<token>` and `?trip_share=<token>`; a share token is a
 * capability, and putting one in an analytics payload would put a grant on
 * somebody's private route into a third-party system. The same rule the
 * `map.route.shared` event already follows.
 *
 * WHY THE DEDUPE IS NOT OPTIONAL
 * ------------------------------
 * A throw inside a MapLibre `moveend` or `sourcedata` handler fires on every
 * frame of a pan — hundreds of times in one gesture. Unbounded, that fills
 * `queue:events` and floods the receiver with one fault repeated. Each
 * distinct `message|filename|lineno` is reported at most MAX_PER_KEY times
 * per page, and at most MAX_KEYS distinct faults are tracked at all, so the
 * bookkeeping itself cannot grow without bound either.
 *
 * NOTHING HERE MAY THROW
 * ----------------------
 * An error reporter that throws inside an error handler turns one fault into
 * a loop. Every path is wrapped, and `window.pwaTelemetry` is reached
 * through optional chaining — the same defensive convention every other
 * consumer of it uses, and the reason load order here is a preference
 * rather than a requirement.
 */

(function () {
  'use strict';

  // Register exactly once. Two <script> tags for this file — a partial
  // included twice, a page that loads it as well as base.html — would
  // otherwise attach two sets of listeners and report every fault twice,
  // which also doubles the dedupe cap. The listeners below read
  // `window.pwaTelemetry` at call time rather than capturing it, so a second
  // evaluation has nothing to contribute in any case.
  if (window.pwaErrorReporting) return;

  /** Longest stack we will send. Enough for the frames that identify a
   *  fault; short enough that a runaway recursion's stack cannot approach
   *  sendBeacon's ~64 KB body cap on its own. */
  const MAX_STACK_CHARS = 2000;

  /** Longest message we will send, for the same reason. */
  const MAX_MESSAGE_CHARS = 500;

  /** Reports per distinct fault, per page load. Three is enough to show a
   *  fault is repeating without the repetition being the payload. */
  const MAX_PER_KEY = 3;

  /** Distinct faults tracked per page load. Past this the map stops
   *  growing and further NEW faults go unreported — an acceptable trade at
   *  twenty, by which point the page is comprehensively broken and the
   *  first twenty say so. */
  const MAX_KEYS = 20;

  /** @type {Map<string, number>} fault key -> times already reported. */
  const seen = new Map();

  /**
   * Has this fault already been reported enough times?
   *
   * @param {string} key
   * @returns {boolean} True when the report should be dropped.
   */
  function isSuppressed(key) {
    const count = seen.get(key) || 0;
    if (count >= MAX_PER_KEY) return true;
    if (count === 0 && seen.size >= MAX_KEYS) return true;
    seen.set(key, count + 1);
    return false;
  }

  /**
   * Trim a string to `max` characters, or return '' for a non-string.
   *
   * @param {unknown} value
   * @param {number} max
   * @returns {string}
   */
  function clamp(value, max) {
    if (typeof value !== 'string') return '';
    return value.length > max ? value.slice(0, max) : value;
  }

  /**
   * The page path, with the query string deliberately dropped.
   *
   * @returns {string}
   */
  function safePath() {
    try {
      return window.location.pathname || '';
    } catch (_err) {
      return '';
    }
  }

  /**
   * Emit one fault, reducing the payload when the visitor has opted out.
   *
   * @param {string} kind          'error' or 'unhandledrejection'.
   * @param {object} detail        The full (opted-in) property set.
   * @param {string} detail.message
   * @param {string} detail.filename
   * @param {number} detail.lineno
   * @param {number} detail.colno
   * @param {string} detail.stack
   */
  function report(kind, detail) {
    const telemetry = window.pwaTelemetry;
    if (!telemetry || typeof telemetry.emit !== 'function') return;

    const pathname = safePath();
    const key = `${detail.message}|${detail.filename}|${detail.lineno}`;
    if (isSuppressed(key)) return;

    // isOptIn() reads IndexedDB, so it is a promise. Resolving it before
    // emitting is what lets the payload differ; a rejection (a broken or
    // reset DB) is treated as opted OUT, which is the conservative branch.
    let optIn;
    try {
      optIn = telemetry.isOptIn();
    } catch (_err) {
      optIn = Promise.resolve(false);
    }

    Promise.resolve(optIn)
      .catch(() => false)
      .then((allowed) => {
        const properties = allowed
          ? {
            kind,
            pathname,
            message: clamp(detail.message, MAX_MESSAGE_CHARS),
            filename: clamp(detail.filename, MAX_MESSAGE_CHARS),
            lineno: Number.isFinite(detail.lineno) ? detail.lineno : 0,
            colno: Number.isFinite(detail.colno) ? detail.colno : 0,
            stack: clamp(detail.stack, MAX_STACK_CHARS),
          }
          // Opted out: that a fault happened, and where in the site. No
          // message, no file, no line, no stack.
          : { kind, pathname };
        telemetry.emit('js.error', properties);
      })
      .catch(() => {
        // An error reporter that throws inside an error handler turns one
        // fault into a loop. There is nowhere left to report to.
      });
  }

  window.addEventListener('error', (event) => {
    try {
      // A failed <img>/<script>/<link> load also fires 'error' on window
      // during capture, but it does NOT set `message`, and it is not an
      // exception. Resource failures are a different signal with a
      // different owner (the SW's cache paths), so they are not this
      // event's business.
      if (!event || typeof event.message !== 'string') return;
      report('error', {
        message: event.message,
        filename: event.filename || '',
        lineno: event.lineno,
        colno: event.colno,
        stack: (event.error && event.error.stack) || '',
      });
    } catch (_err) {
      /* see report()'s final catch */
    }
  });

  window.addEventListener('unhandledrejection', (event) => {
    try {
      if (!event) return;
      const reason = event.reason;
      // A promise can be rejected with anything at all — an Error, a
      // string, a Response, undefined. Only an Error carries a stack and a
      // trustworthy message; everything else is stringified, and a
      // stringification that itself throws (a proxy, a circular toString)
      // is caught below rather than escaping the handler.
      const isError = reason instanceof Error;
      report('unhandledrejection', {
        message: isError ? reason.message : String(reason),
        filename: '',
        lineno: 0,
        colno: 0,
        stack: isError ? reason.stack || '' : '',
      });
    } catch (_err) {
      /* see report()'s final catch */
    }
  });

  // Exposed for tests only — the listeners above are the whole public
  // surface in a browser. `_reset` clears the per-page dedupe state so one
  // suite can assert the cap and the next can start from zero.
  window.pwaErrorReporting = Object.freeze({
    _reset: () => seen.clear(),
    MAX_PER_KEY,
    MAX_KEYS,
  });
})();
