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
 * NO QUERY STRING REACHES THE PAYLOAD — AND `pathname` IS NOT ENOUGH
 * -------------------------------------------------------------------
 * The map's own URLs carry `?route_share=<token>` and `?trip_share=<token>`.
 * A share token is a capability, and putting one in an analytics payload
 * would put a grant on somebody's private route into a third-party system.
 * The `map.route.shared` event already follows this rule.
 *
 * Sending `pathname` rather than `href` is only the first half. `message`,
 * `filename` and `stack` are strings the runtime hands us, and each can
 * carry the document URL in full: an error thrown from an inline or
 * dynamically-evaluated script is attributed to the DOCUMENT, so `filename`
 * and every stack frame read `/?route_share=SECRET`; and a rejected fetch
 * commonly puts the request URL in its message. So every diagnostic string
 * goes through `redact()` before it is emitted, not just the path.
 *
 * Raised in review on the SNOW-894 PR. Worth stating plainly: the original
 * version satisfied its own comment and still leaked, because the comment
 * described the field it had thought about rather than the guarantee.
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
   * Faults raised before `window.pwaTelemetry` existed.
   *
   * This module registers ABOVE the content block so its listeners are in
   * place before any page's boot scripts run — which necessarily means
   * before `telemetry.js`, further down the same document. Dropping those
   * faults would drop precisely the ones worth having: a throw during the
   * map's boot IIFE is the motivating case for the whole file.
   *
   * Bounded by the dedupe, which runs before anything reaches here, so this
   * cannot exceed MAX_KEYS * MAX_PER_KEY entries however badly the page is
   * failing.
   *
   * @type {Array<{kind: string, properties: object}>}
   */
  const pending = [];

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
   * Strip query strings and fragments out of anything URL-shaped.
   *
   * Applied to every diagnostic string, not only to the path — see the
   * header. Two passes, because they catch different things:
   *
   *   1. Any `…?…` run inside a URL-ish token loses its query. This is the
   *      general case and covers a filename, a stack frame and a fetch
   *      error message alike.
   *   2. The current page's own `search`, matched literally. Belt and
   *      braces for a runtime that has already reformatted the URL — a
   *      percent-encoded or re-ordered copy would slip past pass 1, and
   *      this is the one string we know for certain is sensitive.
   *
   * Deliberately blunt. Losing a query string that would have been useful
   * for debugging costs a support conversation; keeping one that carries a
   * share token costs someone their private route.
   *
   * @param {unknown} text
   * @param {number} max Truncate to this many characters afterwards.
   * @returns {string} Redacted and clamped, or '' for a non-string.
   */
  function redact(text, max) {
    if (typeof text !== 'string') return '';
    var out = text;
    try {
      // Pass 1 — drop `?query` and `#fragment` from any URL-ish token. The
      // token ends at whitespace, a quote, or a bracket, which is how these
      // strings delimit URLs in practice.
      out = out.replace(/([^\s'"()<>]*?)[?#][^\s'"()<>]*/g, '$1?[redacted]');
      // Pass 2 — the page's own query, verbatim.
      var search = window.location.search;
      if (search && search.length > 1) {
        while (out.indexOf(search) !== -1) out = out.replace(search, '?[redacted]');
      }
    } catch (_err) {
      // A redaction that throws must not become an unredacted payload.
      return '[redaction failed]';
    }
    return clamp(out, max);
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
  /**
   * Hand anything buffered to telemetry, if it has arrived.
   *
   * Called on every new fault and from both document lifecycle events, so a
   * page whose only fault happened before telemetry loaded still reports it.
   */
  function flushPending() {
    const telemetry = window.pwaTelemetry;
    if (!telemetry || typeof telemetry.emit !== 'function') return;
    while (pending.length) {
      const queued = pending.shift();
      try {
        telemetry.emit('js.error', queued.properties);
      } catch (_err) {
        // Nowhere left to report to; dropping one queued fault is better
        // than looping on it.
      }
    }
  }

  function report(kind, detail) {
    const pathname = safePath();
    const key = `${detail.message}|${detail.filename}|${detail.lineno}`;
    if (isSuppressed(key)) return;

    // isOptIn() reads IndexedDB, so it is a promise. Resolving it before
    // emitting is what lets the payload differ; a rejection (a broken or
    // reset DB) is treated as opted OUT, which is the conservative branch.
    // isOptIn() reads IndexedDB, so it is a promise. A missing telemetry
    // module (this fault beat it to the page) and a rejection (a broken or
    // reset DB) both resolve to opted OUT, which is the conservative branch:
    // the buffered report carries the path and nothing else.
    let optIn;
    try {
      const telemetry = window.pwaTelemetry;
      optIn = telemetry && typeof telemetry.isOptIn === 'function'
        ? telemetry.isOptIn()
        : Promise.resolve(false);
    } catch (_err) {
      optIn = Promise.resolve(false);
    }

    Promise.resolve(optIn)
      .catch(() => false)
      .then((allowed) => {
        const telemetry = window.pwaTelemetry;
        const properties = allowed
          ? {
            kind,
            pathname,
            message: redact(detail.message, MAX_MESSAGE_CHARS),
            filename: redact(detail.filename, MAX_MESSAGE_CHARS),
            lineno: Number.isFinite(detail.lineno) ? detail.lineno : 0,
            colno: Number.isFinite(detail.colno) ? detail.colno : 0,
            stack: redact(detail.stack, MAX_STACK_CHARS),
          }
          // Opted out: that a fault happened, and where in the site. No
          // message, no file, no line, no stack.
          : { kind, pathname };
        if (telemetry && typeof telemetry.emit === 'function') {
          flushPending();
          telemetry.emit('js.error', properties);
        } else {
          // telemetry.js has not loaded yet — see `pending`.
          pending.push({ kind, properties });
        }
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
  // Two chances to drain anything buffered on a page whose faults all
  // happened before telemetry.js loaded and which then raises no more.
  window.addEventListener('DOMContentLoaded', flushPending);
  window.addEventListener('load', flushPending);

  window.pwaErrorReporting = Object.freeze({
    _flush: flushPending,
    _reset: () => { seen.clear(); pending.length = 0; },
    MAX_PER_KEY,
    MAX_KEYS,
  });
})();
