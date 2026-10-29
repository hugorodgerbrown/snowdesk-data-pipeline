/*
 * static/js/map_today.js — the map's way back to today (SNOW-1165).
 *
 * #map-today-control, the "Today" half of the date control. Always shown;
 * enabled whenever the map is on a day other than today (today_core.js).
 *
 * The map sheets carried a second way back — "Back to today" in their
 * "Data from" row (includes/_sheet_date_row.html, Hugo, 21 Oct). That row
 * went on 29 Oct 2026 (Hugo: "Date is everywhere on the page"): a sheet
 * names its day in its dated section headers now
 * (includes/_dated_eyebrow.html), and this control is the way back.
 *
 * Decisions, and why:
 *
 *   - THE SCRUBBER COMMITS. A press dispatches ``snowdesk:scrub-to`` with
 *     today, the request the calendar already makes, so the repaint, the
 *     ``?d=`` write and ``snowdesk:date-changed`` happen in the one place they
 *     always do (map_scrubber.js ``commitDate``). That event is what disables
 *     the control again.
 *   - TODAY IS THE PAGE'S. ``#season-scrubber[data-today]``, as every other
 *     date decision on the map (SNOW-793). The browser clock is never asked.
 *   - ENABLED DURING PLAYBACK. A timelapse is still another day, and pressing
 *     "Today" mid-play is a reasonable way to stop it: the scrubber's commit
 *     stops the timelapse, as any scrub does.
 *
 * Depends on today_core.js, loaded before it.
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

  document.addEventListener('snowdesk:date-changed', (e) => {
    date = (e.detail && e.detail.date) || null;
    apply();
  });

  if (control) control.addEventListener('click', goToToday);

  apply();
})();
