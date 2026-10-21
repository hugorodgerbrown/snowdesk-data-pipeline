/*
 * static/js/map_today.js — the map's ways back to today (SNOW-1165).
 *
 * Two controls, one action:
 *
 *   - #map-today-control, the "Today" half of the date control. Always shown;
 *     enabled whenever the map is on a day other than today (today_core.js).
 *   - any ``[data-back-to-today]`` button — the "Back to today" in a map
 *     sheet's date row (includes/_sheet_date_row.html, Hugo, 21 Oct; the
 *     SNOW-1165 strip above the peek row until then), which the sheets
 *     render server-side and re-render on every change of day. One delegated
 *     listener, because the sheets' content is swapped in and out.
 *
 * Decisions, and why:
 *
 *   - THE SCRUBBER COMMITS. A press dispatches ``snowdesk:scrub-to`` with
 *     today, the request the calendar already makes, so the repaint, the
 *     ``?d=`` write and ``snowdesk:date-changed`` happen in the one place they
 *     always do (map_scrubber.js ``commitDate``). That event is what disables
 *     the control again and re-renders the open sheet without its strip.
 *   - TODAY IS THE PAGE'S. ``#season-scrubber[data-today]``, as every other
 *     date decision on the map (SNOW-793). The browser clock is never asked.
 *   - A STRIP ON THE MAP'S OWN TODAY MEANS A STALE SHELL. The sheets judge
 *     "another day" against the server's date, the map against the shell's
 *     ``data-today``. They differ only when a cached shell has outlived its
 *     day (SNOW-920), and then scrubbing to ``data-today`` would change
 *     nothing and leave the strip up. So the strip's button reloads the
 *     shell instead, as the stale-day notice's Reload does, which brings a
 *     ``data-today`` that agrees with the server.
 *   - ENABLED DURING PLAYBACK. A timelapse is still another day, and pressing
 *     "Today" mid-play is a reasonable way to stop it: the scrubber's commit
 *     stops the timelapse, as any scrub does.
 *
 * Depends on today_core.js, loaded before it, and on pwa_version_check.js
 * (``window.pwaVersionInfo.reload``) for the stale-shell case, falling back
 * to a plain reload without it.
 */
(function mapTodayInit() {
  'use strict';

  const control = document.getElementById('map-today-control');
  const scrubber = document.getElementById('season-scrubber');
  const core = window.pwaTodayCore;
  if (!scrubber || !core) return;

  const today = scrubber.dataset.today || '';

  // The URL's ``?d=`` is the day until the scrubber's first commit says
  // otherwise; that commit waits on the ratings fetch, and the control
  // should not wait with it.
  let date = today || null;
  try {
    date = new URLSearchParams(location.search).get('d') || date;
  } catch (_err) {
    // Keep today.
  }

  /** Enable or disable "Today" for the current day. */
  function apply() {
    if (control) control.disabled = !core.canGoToToday(date, today);
  }

  /** Move the map to today. */
  function goToToday() {
    if (!today) return;
    document.dispatchEvent(
      new CustomEvent('snowdesk:scrub-to', {
        detail: { date: today, source: 'today' },
      })
    );
  }

  /** Fetch a fresh shell, whose ``data-today`` is the server's day. */
  function reloadShell() {
    const info = window.pwaVersionInfo;
    const reload = info && typeof info.reload === 'function' ? info.reload : null;
    Promise.resolve(reload ? reload() : window.location.reload()).catch(() =>
      window.location.reload()
    );
  }

  document.addEventListener('snowdesk:date-changed', (e) => {
    date = (e.detail && e.detail.date) || null;
    apply();
  });

  if (control) control.addEventListener('click', goToToday);

  document.addEventListener('click', (e) => {
    const target = e.target instanceof Element ? e.target.closest('[data-back-to-today]') : null;
    if (!target) return;
    e.preventDefault();
    if (today && date === today) {
      reloadShell();
      return;
    }
    goToToday();
  });

  apply();
})();
