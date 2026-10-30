/*
 * static/js/map_day_nav.js — a day at a time on the map's date row
 * (SNOW-1195).
 *
 * Two things, both about moving between bulletin days without losing what
 * is open:
 *
 *   - THE STEP BUTTONS. #map-day-prev and #map-day-next sit either side of
 *     the date control, so a day before or after is one press rather than a
 *     calendar open, a cell and a close. On a phone the calendar was the only
 *     date control there was, and it used to shut the region sheet on the
 *     way (map_calendar.js, SNOW-1195).
 *   - THE SCRUBBER'S KEYBOARD. #season-scrubber has said role="slider"
 *     since SNOW-234 and answered no key. Arrows move a day, Page Up / Page
 *     Down a week, Home / End the ends of the reachable range — the
 *     WAI-ARIA slider pattern. aria-valuetext names the day, because
 *     aria-valuenow is a percentage of the season and reads as one.
 *
 * SNOW-1195 also drew an empty-day note, which SNOW-1206 moved behind the
 * date row's bell. SNOW-1333 gave the bell to bulletin alerts and dropped
 * the note (Hugo, 2026-10-30).
 *
 * Decisions, and why:
 *
 *   - THE SCRUBBER COMMITS. Every move dispatches ``snowdesk:scrub-to``, as
 *     the calendar and "Today" do, so the repaint, the ``?d=`` write and
 *     ``snowdesk:date-changed`` happen in map_scrubber.js ``commitDate`` and
 *     nowhere else. Open sheets reload on that event.
 *   - THE RANGE IS THE CALENDAR'S. [earliestKnownDate, latestKnownDate] from
 *     ``pwaCalendarCore`` over the same ratings cache, so the arrows stop
 *     where the picker's grid stops. Re-read when another country's ratings
 *     merge in, which can add later days.
 *   - EVERY DAY, NOT ONLY RATED ONES. A step lands on the next calendar day
 *     whether or not it has a bulletin, as the calendar does: the map
 *     carries weather too.
 *
 * Reads ``getSeasonRatings``, ``readUrlDateParam`` and ``formatDatePopup``
 * (map_shared.js), and loads after map_scrubber.js, which owns the
 * ``snowdesk:scrub-to`` listener. Pure maths in day_nav_core.js.
 */
(function mapDayNavInit() {
  'use strict';

  const scrubber = document.getElementById('season-scrubber');
  const core = window.pwaDayNavCore;
  const calendar = window.pwaCalendarCore;
  if (!scrubber || !core) return;

  const prev = document.getElementById('map-day-prev');
  const next = document.getElementById('map-day-next');

  const todayKey = scrubber.dataset.today || '';
  const seasonStart = scrubber.dataset.seasonStart || '';

  // The reachable range: the calendar's, before and after the ratings land.
  let minKey = seasonStart;
  let maxKey = todayKey;
  let ratingsCache = null;
  // The day the last ``snowdesk:date-changed`` announced. A timelapse
  // announces every frame but writes ``?d=`` only when it stops, so the URL
  // alone would describe the day playback started on.
  let shownKey = '';

  /** @returns {string} The day the map is showing. */
  function currentKey() {
    return shownKey || readUrlDateParam() || todayKey;
  }

  /** Re-read the range from the ratings cache. */
  function readRange() {
    if (!ratingsCache || !calendar) return;
    minKey = calendar.earliestKnownDate(ratingsCache, seasonStart);
    maxKey = calendar.latestKnownDate(ratingsCache, todayKey);
  }

  /**
   * Move the map to a day, through the scrubber's one commit point.
   *
   * @param {string} dateKey
   */
  function goTo(dateKey) {
    if (!dateKey || dateKey === currentKey()) return;
    document.dispatchEvent(
      new CustomEvent('snowdesk:scrub-to', {
        detail: { date: dateKey, source: 'step' },
      })
    );
  }

  /**
   * Move the map by a number of days, held inside the range.
   *
   * @param {number} delta
   */
  function step(delta) {
    const target = core.clampDay(core.shiftDay(currentKey(), delta), minKey, maxKey);
    goTo(target);
  }

  /** Disable a step button at its end of the range; name the day. */
  function applyControls() {
    const day = currentKey();
    if (prev) prev.disabled = !!minKey && day <= minKey;
    if (next) next.disabled = !!maxKey && day >= maxKey;
    if (day) scrubber.setAttribute('aria-valuetext', formatDatePopup(day));
  }


  if (prev) prev.addEventListener('click', () => step(-1));
  if (next) next.addEventListener('click', () => step(1));

  scrubber.addEventListener('keydown', (e) => {
    const move = core.keyStep(e.key);
    if (!move) return;
    e.preventDefault();
    if ('edge' in move) {
      goTo(move.edge === 'min' ? minKey : maxKey);
    } else {
      step(move.delta);
    }
  });

  document.addEventListener('snowdesk:date-changed', (e) => {
    shownKey = (e.detail && e.detail.date) || '';
    applyControls();
  });
  // Another country's ratings merge into the same cache object and can add
  // later days.
  document.addEventListener('snowdesk:country-ratings-loaded', () => {
    readRange();
    applyControls();
  });

  getSeasonRatings()
    .then((data) => {
      ratingsCache = data || null;
      readRange();
      applyControls();
    })
    .catch(() => {
      // The steps still work over the boot range; the scrubber reports the
      // failed fetch.
    });

  applyControls();
})();
