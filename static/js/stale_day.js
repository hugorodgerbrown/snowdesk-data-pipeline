/*
 * static/js/stale_day.js — "Showing 18 Oct · Reload" when the page is a day old (SNOW-920).
 *
 * A navigation the worker serves from Cache Storage after its network budget
 * ran out carries the day it was rendered on (``#season-scrubber[data-today]``),
 * and ``readDisplayDate`` trusts it (SNOW-793). This module asks the server
 * what day it is and, when the answer differs, reveals ``#stale-day-notice``:
 * the page's day and a Reload button that clears the shell caches and fetches
 * the shell from the network.
 *
 * Decisions, and why:
 *
 *   - NEVER AUTOMATIC. Nothing reloads by itself; the visitor may be
 *     mid-gesture or mid-form, and an unprompted reload is worse than a
 *     stale label. The button is the whole remedy.
 *   - NO BROWSER CLOCK. The comparison is page day vs server day, both
 *     strings; ``new Date()`` is never asked what day it is (SNOW-793).
 *   - A FAILED OR EMPTY ANSWER SAYS NOTHING. Offline, unreachable or a server
 *     that predates ``today`` is "cannot confirm", which never raises the
 *     label (stale_day_core.js ``isStale``). Only a clear mismatch does.
 *   - IT CLEARS ITSELF. A later poll that agrees hides the label again, so a
 *     visitor who reloads some other way is not left with a stale warning.
 *   - WHEN IT ASKS: once on load, and when the page comes back to the
 *     foreground (visibilitychange / pageshow), at most once a minute — the
 *     ``/api/version`` edge cache is 60 s, so asking faster is wasted. The
 *     second trigger is what catches a tab or installed app left open across
 *     midnight, which no cold-start check could.
 *   - AN EXPLICIT ``?d=`` IS LEFT ALONE. The visitor asked for that day; the
 *     label is about the page's default one. Choosing a day while the label
 *     is showing hides it at once (snowdesk:date-changed, popstate).
 *
 * Depends on pwa_version_check.js (window.pwaVersionInfo.refresh / .reload),
 * stale_day_core.js and i18n_strings.js, all loaded before it.
 */
(function staleDayInit() {
  'use strict';

  const MIN_POLL_GAP_MS = 60 * 1000;

  const scrubber = document.getElementById('season-scrubber');
  const notice = document.getElementById('stale-day-notice');
  const core = window.pwaStaleDayCore;
  if (!scrubber || !notice || !core) return;

  const label = notice.querySelector('[data-stale-day-label]');
  const reloadBtn = notice.querySelector('[data-stale-day-reload]');
  if (!label || !reloadBtn) return;

  const STRINGS = window.pwaStrings.read('stale-day-strings-template', {
    'stale-day-showing': 'Showing %(day)s',
  });

  let lastPollAt = 0;
  let polling = false;
  // The last day the server named, so a change of URL can re-judge it
  // without another request. Null until a poll has answered.
  let lastServerDay = null;

  /** The URL's ``?d=``, or ``''`` when the visitor chose no day. */
  function explicitDay() {
    try {
      return new URLSearchParams(location.search).get('d') || '';
    } catch (_err) {
      return '';
    }
  }

  /**
   * Show or hide the notice for one server answer.
   *
   * @param {string} serverDay The server's ``today``, possibly empty.
   */
  function apply(serverDay) {
    lastServerDay = serverDay;
    const pageDay = scrubber.dataset.today || '';
    const stale = core.isStale(pageDay, serverDay, explicitDay());
    notice.hidden = !stale;
    if (stale) {
      label.textContent = window.pwaStrings.interpolate(STRINGS['stale-day-showing'], {
        day: core.formatDay(pageDay, document.documentElement.lang),
      });
    }
  }

  /** Ask the server for its day, at most once a minute. */
  async function check() {
    const info = window.pwaVersionInfo;
    if (polling || !info || typeof info.refresh !== 'function') return;
    const now = Date.now(); // a throttle interval, not a calendar day
    if (lastPollAt && now - lastPollAt < MIN_POLL_GAP_MS) return;
    polling = true;
    lastPollAt = now;
    try {
      const verdict = await info.refresh();
      // null = could not confirm: leave whatever is showing as it is.
      if (verdict) apply(verdict.today || '');
    } catch (_err) {
      // Never let this break the page.
    } finally {
      polling = false;
    }
  }

  reloadBtn.addEventListener('click', () => {
    const info = window.pwaVersionInfo;
    const reload = info && typeof info.reload === 'function' ? info.reload : null;
    Promise.resolve(reload ? reload() : window.location.reload()).catch(() =>
      window.location.reload()
    );
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') check();
  });
  window.addEventListener('pageshow', check);
  // A day chosen on the calendar or scrubber writes ``?d=`` and emits
  // ``snowdesk:date-changed`` (the URL first); back/forward moves ``?d=``
  // too. Either makes the day the visitor's own choice, so re-judge at once
  // rather than leave a label naming the page's rendered day beside it.
  const rejudge = () => {
    if (lastServerDay !== null) apply(lastServerDay);
  };
  document.addEventListener('snowdesk:date-changed', rejudge);
  window.addEventListener('popstate', rejudge);
  check();
})();
