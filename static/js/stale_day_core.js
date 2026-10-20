// @ts-check
/*
 * static/js/stale_day_core.js — is this page's own "today" old? (SNOW-920)
 *
 * The service worker's network-first navigation waits NAVIGATION_FETCH_BUDGET_MS
 * for the shell and then serves the cached copy. That copy carries the day it
 * was RENDERED on as ``#season-scrubber[data-today]``, and SNOW-793 makes that
 * attribute the only authority for "today" (the browser clock is refused, so a
 * device with the wrong date cannot move the map). So a cached shell opened
 * after midnight quietly shows yesterday as today, and nothing compared it
 * with the server's day.
 *
 * ``stale_day.js`` asks ``/api/version`` for the server's day; this file holds
 * the two pure decisions made with the answer, so they are tested without a
 * DOM:
 *
 *   - ``isStale`` — the page is stale only when BOTH days are well-formed and
 *     they differ, and no day was asked for in the URL. A missing or
 *     malformed day on either side is "cannot confirm", and cannot confirm
 *     never raises an alarm. An explicit ``?d=`` is the visitor's own choice
 *     of day and is not what the label is about.
 *   - ``formatDay`` — the label's day, from an ISO string, in the page's
 *     language. It formats a date it was GIVEN; it never reads the clock.
 */
(function () {
  'use strict';

  const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

  /**
   * Whether a string is a calendar day in ``YYYY-MM-DD`` form.
   *
   * @param {unknown} value
   * @returns {value is string}
   */
  function isIsoDay(value) {
    if (typeof value !== 'string' || !ISO_DAY.test(value)) return false;
    const parsed = new Date(value + 'T00:00:00Z');
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }

  /**
   * Whether the page's rendered day is older than the server's.
   *
   * @param {unknown} pageDay The page's ``data-today``.
   * @param {unknown} serverDay ``today`` from ``/api/version``.
   * @param {unknown} [explicitDay] The URL's ``?d=``, when it has one.
   * @returns {boolean}
   */
  function isStale(pageDay, serverDay, explicitDay) {
    if (explicitDay) return false;
    if (!isIsoDay(pageDay) || !isIsoDay(serverDay)) return false;
    return pageDay !== serverDay;
  }

  /**
   * A short, locale-aware day for the label ("18 Oct").
   *
   * Formatted in UTC so the calendar day never shifts with the device's
   * zone; the input is a date, not a moment.
   *
   * @param {string} isoDay ``YYYY-MM-DD``.
   * @param {string} [locale] A BCP 47 tag; the runtime default when empty.
   * @returns {string} The formatted day, or the input when it cannot be
   *   formatted.
   */
  function formatDay(isoDay, locale) {
    if (!isIsoDay(isoDay)) return String(isoDay || '');
    try {
      return new Intl.DateTimeFormat(locale || undefined, {
        day: 'numeric',
        month: 'short',
        timeZone: 'UTC',
      }).format(new Date(isoDay + 'T00:00:00Z'));
    } catch (_err) {
      return isoDay;
    }
  }

  window.pwaStaleDayCore = Object.freeze({
    isIsoDay: isIsoDay,
    isStale: isStale,
    formatDay: formatDay,
  });
})();
