// @ts-check
/*
 * static/js/cached_stamp_core.js — when was this cached copy made? (SNOW-1139)
 *
 * Three panels repaint from a cache when their list request fails — the
 * favourites panel (favourites_offline.js), the reports panel (report.js) and
 * the routes panel (routes.js) — and each stamped its cached copy with a
 * private HH:MM formatter. A time alone reads the same whether the copy was
 * made this morning or a week ago, so yesterday's cache looked like today's.
 * The downloads surfaces had the opposite gap: a download records when it
 * fetched its bulletins (``contentAt``) and nothing but the /offline/ report
 * ever said so.
 *
 * This module is the one answer to both. It holds no state and touches no
 * DOM; the consumers call it at paint time, and it is loaded in
 * ``public/base.html``'s ``<head>`` so it precedes every one of them however
 * their own tags move.
 *
 *   - ``formatStamp`` — "HH:MM" when the instant is today, "<day> HH:MM"
 *     otherwise, '' for a missing or unparseable value. The time stays a
 *     zero-padded 24-hour clock, as the three copies it replaces wrote it.
 *   - ``formatDay`` — the day alone ("18 Oct"), in the page's language.
 *   - ``isBeforeToday`` — whether an instant falls on an earlier calendar day
 *     than now. A missing, unparseable or future instant is not "before
 *     today": a surface that cannot tell says nothing rather than raising a
 *     staleness it cannot prove.
 *
 * "Today" is the device's own local day. ``now`` is injectable so tests do
 * not depend on the clock.
 */
(function () {
  'use strict';

  /**
   * Parse an ISO value into a Date, or null when it is missing or invalid.
   *
   * @param {unknown} value
   * @returns {Date | null}
   */
  function toDate(value) {
    if (!value || (typeof value !== 'string' && !(value instanceof Date))) return null;
    const d = new Date(value);
    return Number.isNaN(d.valueOf()) ? null : d;
  }

  /**
   * The local calendar day of a Date, as a comparable number (YYYYMMDD).
   *
   * @param {Date} d
   * @returns {number}
   */
  function dayKey(d) {
    return d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate();
  }

  /**
   * The language to format in: the caller's, else the page's, else the
   * runtime default.
   *
   * @param {string | undefined} locale
   * @returns {string | undefined}
   */
  function resolveLocale(locale) {
    if (locale) return locale;
    const lang =
      typeof document !== 'undefined' && document.documentElement
        ? document.documentElement.lang
        : '';
    return lang || undefined;
  }

  /**
   * Whether ``value`` falls on an earlier local day than ``now``.
   *
   * @param {unknown} value An ISO 8601 instant.
   * @param {Date} [now] Defaults to the current time.
   * @returns {boolean}
   */
  function isBeforeToday(value, now) {
    const d = toDate(value);
    if (!d) return false;
    return dayKey(d) < dayKey(now || new Date());
  }

  /**
   * A short, locale-aware day ("18 Oct") for an instant, in local time.
   *
   * @param {unknown} value An ISO 8601 instant.
   * @param {{locale?: string}} [options]
   * @returns {string} '' when the value cannot be parsed.
   */
  function formatDay(value, options) {
    const d = toDate(value);
    if (!d) return '';
    try {
      return new Intl.DateTimeFormat(resolveLocale(options && options.locale), {
        day: 'numeric',
        month: 'short',
      }).format(d);
    } catch (_err) {
      return d.toDateString();
    }
  }

  /**
   * "HH:MM" today, "<day> HH:MM" on any other day, '' when unparseable.
   *
   * @param {unknown} value An ISO 8601 instant.
   * @param {{now?: Date, locale?: string}} [options]
   * @returns {string}
   */
  function formatStamp(value, options) {
    const d = toDate(value);
    if (!d) return '';
    const opts = options || {};
    /** @param {number} n */
    const pad = (n) => String(n).padStart(2, '0');
    const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (dayKey(d) === dayKey(opts.now || new Date())) return time;
    return `${formatDay(d.toISOString(), { locale: opts.locale })} ${time}`;
  }

  window.pwaCachedStampCore = Object.freeze({
    isBeforeToday: isBeforeToday,
    formatDay: formatDay,
    formatStamp: formatStamp,
  });
})();
