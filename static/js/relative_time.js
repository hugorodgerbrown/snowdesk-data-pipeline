/**
 * static/js/relative_time.js — keeps a server-rendered relative timestamp
 * honest for as long as the page stays open.
 *
 * A relative age ("2 hours ago") is only true at the instant it is
 * rendered. The map's field-observation rows carry one, and the map is a
 * page that stays open for hours: opened at the car park, put in a pocket,
 * read again on the ridge. By then the server's answer is four hours out of
 * date and nothing on the page has re-rendered it — the list endpoint is
 * network-only (see sw.js's classification), so away from signal there is
 * no re-render to be had. The arithmetic, though, needs no network at all,
 * which is why it happens here.
 *
 * Markup contract — any surface, not just observations:
 *
 *     <time datetime="2026-08-11T05:29:00+00:00" data-relative-time>
 *       2 hours ago
 *     </time>
 *
 * The server renders the age in the element's text (``naturaltime``) and
 * the instant in ``datetime``. This module recomputes the text from
 * ``datetime``; a browser that never runs it keeps the server's answer,
 * which is right at page load and stale only afterwards. The surrounding
 * copy — "Martigny-Verbier · reported …" — stays in the template, so a
 * translator sees a whole sentence and this module owns no prose.
 *
 * That is also what keeps the module out of the i18n trap (docs/i18n.md):
 * the age itself is formatted by ``Intl.RelativeTimeFormat`` against
 * ``<html lang>``, so the words come from the platform's CLDR data rather
 * than from English literals hard-coded here.
 *
 * WHEN IT RECOMPUTES. The interval is the least important of the four:
 *
 *   - a 60s interval, for a panel someone is looking at;
 *   - ``visibilitychange`` and ``pageshow``, which is the case that
 *     actually matters. A backgrounded tab has its timers throttled, and a
 *     phone that slept for four hours may have had the page frozen
 *     outright — no timer fired at all. Both fire on the way back, before
 *     the user can read a stale row;
 *   - ``htmx:afterSwap``, because the rows arrive by HTMX after this
 *     module's first pass and would otherwise wait up to a minute for
 *     their first correction. (They are correct as rendered, so this is
 *     about the swap-then-sleep case, not the swap itself.)
 *
 * Exposes ``window.pwaRelativeTime.refresh(root)`` so a caller that
 * injects rows by some other route can ask for a pass directly.
 */

(function () {
  'use strict';

  /** Elements this module owns. `datetime` is required: without an instant
   * there is nothing to recompute from, and overwriting the server's text
   * would lose the only age the row has.
   * @type {string}
   */
  const SELECTOR = 'time[data-relative-time][datetime]';

  /** How often to recompute while the page is visible. A minute is the
   * coarsest tick that still keeps a sub-hour age (the common case for a
   * report just filed) from reading visibly wrong.
   * @type {number}
   */
  const TICK_MS = 60000;

  /** Unit ladder, smallest first: how many of each unit make up the next
   * one. `Infinity` terminates it — years are the last unit we name.
   * @type {Array<[Intl.RelativeTimeFormatUnit, number]>}
   */
  const UNITS = [
    ['second', 60],
    ['minute', 60],
    ['hour', 24],
    ['day', 7],
    // 30.4 days per month / 7 per week. Approximate on purpose: this is a
    // human-readable age, not a duration anyone measures against.
    ['week', 4.35],
    ['month', 12],
    ['year', Infinity],
  ];

  /** The formatter, or null where `Intl.RelativeTimeFormat` is missing.
   * Null means every element keeps its server-rendered text — the same
   * outcome as this file failing to load, which is the point of rendering
   * the age server-side in the first place.
   * @type {Intl.RelativeTimeFormat | null}
   */
  const FORMATTER = (function () {
    if (typeof Intl === 'undefined' || !Intl.RelativeTimeFormat) return null;
    const lang = document.documentElement.getAttribute('lang');
    try {
      // `numeric: 'always'` — "1 day ago", not "yesterday". The template's
      // sentence is "reported <age>", and only the numeric form completes
      // it in every language the way the server's own naturaltime does.
      return new Intl.RelativeTimeFormat(lang || undefined, { numeric: 'always' });
    } catch (_err) {
      // An unparseable lang tag is not worth losing the feature over.
      return new Intl.RelativeTimeFormat(undefined, { numeric: 'always' });
    }
  })();

  /** Format a signed millisecond offset as a relative age.
   *
   * Negative is the past ("2 hours ago"); positive the future ("in 3
   * minutes"), which a report can legitimately be by up to the server's
   * five-minute clock-skew tolerance.
   *
   * @param {number} deltaMs Instant minus now, in milliseconds.
   * @returns {string} The formatted age.
   */
  function format(deltaMs) {
    let value = deltaMs / 1000;
    for (let i = 0; i < UNITS.length; i += 1) {
      const [unit, perNext] = UNITS[i];
      if (Math.abs(value) < perNext || perNext === Infinity) {
        // Round away from zero, so the first second of a report's life
        // reads "1 second ago" rather than the meaningless "0 seconds ago".
        const rounded = Math.round(value) || (value < 0 ? -1 : 1);
        return FORMATTER.format(rounded, unit);
      }
      value /= perNext;
    }
    // Unreachable: the ladder ends at Infinity. Kept so the function has
    // one return type whatever a future edit does to UNITS.
    return FORMATTER.format(Math.round(value), 'year');
  }

  /** Recompute every relative timestamp under `root`.
   *
   * @param {ParentNode} [root] Subtree to scan. Defaults to the document.
   * @returns {void}
   */
  function refresh(root) {
    if (!FORMATTER) return;
    const scope = root || document;
    const now = Date.now();
    scope.querySelectorAll(SELECTOR).forEach(function (el) {
      const instant = Date.parse(el.getAttribute('datetime'));
      // An unparseable datetime leaves the server's text alone rather than
      // replacing it with "NaN years ago".
      if (Number.isNaN(instant)) return;
      el.textContent = format(instant - now);
    });
  }

  refresh();

  setInterval(refresh, TICK_MS);

  // The page coming back into view — from another tab, from a locked
  // screen, or out of the bfcache after a device slept through the whole
  // interval. `pageshow` covers the restore that fires no visibilitychange.
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) refresh();
  });
  window.addEventListener('pageshow', function () {
    refresh();
  });

  // Rows swapped in by HTMX after this module's first pass.
  document.addEventListener('htmx:afterSwap', function (event) {
    const target = event.detail && event.detail.target;
    refresh(target && target.querySelectorAll ? target : undefined);
  });

  window.pwaRelativeTime = { refresh: refresh, format: format };
})();
