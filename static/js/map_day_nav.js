/*
 * static/js/map_day_nav.js — a day at a time on the map's date row
 * (SNOW-1195).
 *
 * Three things, all about moving between bulletin days without losing what
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
 *   - THE EMPTY-DAY NOTICE. #map-day-empty says when the day on the map has
 *     no bulletin for any country the visitor has on, and offers the
 *     nearest day that does — the last one issued, or the next when nothing
 *     came before. Without it an empty day read as a map that had failed to
 *     load.
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
 *     carries weather too, and the empty-day notice is what explains a day
 *     without a bulletin.
 *   - NOTHING UNTIL THE RATINGS ARRIVE. The notice cannot judge a day it has
 *     no data for, and a failed fetch is reported by the scrubber's own
 *     strip.
 *
 * Reads ``getSeasonRatings``, ``readUrlDateParam``, ``formatDatePopup``
 * (map_shared.js) and ``COUNTRY_STATE`` (map_state.js), and loads after
 * map_scrubber.js, which owns the ``snowdesk:scrub-to`` listener. Pure
 * maths in day_nav_core.js; strings in ``map-day-nav-strings-template``.
 */
(function mapDayNavInit() {
  'use strict';

  const scrubber = document.getElementById('season-scrubber');
  const core = window.pwaDayNavCore;
  const calendar = window.pwaCalendarCore;
  if (!scrubber || !core) return;

  const prev = document.getElementById('map-day-prev');
  const next = document.getElementById('map-day-next');
  const empty = document.getElementById('map-day-empty');
  const emptyLabel = empty ? empty.querySelector('[data-day-empty-label]') : null;
  const emptyJump = empty ? empty.querySelector('[data-day-empty-jump]') : null;
  const emptyJumpLabel = emptyJump ? emptyJump.querySelector('[data-day-empty-jump-label]') : null;

  const STRINGS = self.pwaStrings.read('map-day-nav-strings-template', {
    'day-empty': 'No bulletins for %(day)s',
    'day-last-issued': 'Last issued %(day)s',
    'day-next-issued': 'Next issued %(day)s',
  });

  const todayKey = scrubber.dataset.today || '';
  const seasonStart = scrubber.dataset.seasonStart || '';

  // The reachable range: the calendar's, before and after the ratings land.
  let minKey = seasonStart;
  let maxKey = todayKey;
  let ratingsCache = null;
  let jumpTarget = '';
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

  /** Show or hide the empty-day notice for the day on the map. */
  function applyEmpty() {
    if (!empty || !emptyLabel || !emptyJump || !emptyJumpLabel) return;
    const day = currentKey();
    if (!ratingsCache || !day || core.dayHasBulletin(ratingsCache[day], COUNTRY_STATE)) {
      empty.hidden = true;
      jumpTarget = '';
      return;
    }
    emptyLabel.textContent = self.pwaStrings.interpolate(STRINGS['day-empty'], {
      day: formatDatePopup(day),
    });
    const nearest = core.nearestIssued(day, ratingsCache, COUNTRY_STATE);
    if (nearest) {
      const key = nearest.direction === 'last' ? 'day-last-issued' : 'day-next-issued';
      emptyJumpLabel.textContent = self.pwaStrings.interpolate(STRINGS[key], {
        day: formatDatePopup(nearest.date),
      });
      emptyJump.hidden = false;
      jumpTarget = nearest.date;
    } else {
      emptyJump.hidden = true;
      jumpTarget = '';
    }
    empty.hidden = false;
  }

  /** Bring every control into line with the day and the data. */
  function apply() {
    applyControls();
    applyEmpty();
  }

  if (prev) prev.addEventListener('click', () => step(-1));
  if (next) next.addEventListener('click', () => step(1));
  if (emptyJump) emptyJump.addEventListener('click', () => goTo(jumpTarget));

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
    apply();
  });
  // Another country's ratings merge into the same cache object and can add
  // later days; turning a country on or off changes which days are empty.
  document.addEventListener('snowdesk:country-ratings-loaded', () => {
    readRange();
    apply();
  });
  document.addEventListener('snowdesk:country-toggle', apply);

  getSeasonRatings()
    .then((data) => {
      ratingsCache = data || null;
      readRange();
      apply();
    })
    .catch(() => {
      // The steps still work over the boot range; the scrubber reports the
      // failed fetch, and the notice has nothing to judge by.
    });

  applyControls();
})();
