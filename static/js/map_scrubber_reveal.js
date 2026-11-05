/*
 * static/js/map_scrubber_reveal.js — the calendar is the scrubber's reveal
 * (SNOW-794).
 *
 * The scrubber's track is the latest bulletin run through today (SNOW-1431,
 * docs/decisions/date-windows-follow-the-data.md). Today is always on it,
 * so the scrubber is on screen on an unchosen map in every month. A day the
 * calendar offers before the run started has no position on that track, and
 * the thumb clamping to the start was the defect SNOW-794 opened with: the
 * URL said one day and the thumb said another.
 *
 * So this module is a generic guard: the scrubber is present only while the
 * day the map is showing falls on its track. Two controls, each honest about
 * its own job: the calendar is "pick a date, any date"; the scrubber steps
 * through the days it spans.
 *
 * WHY THE ELEMENT IS ALWAYS RENDERED. _map_embed.html could have wrapped it
 * in an {% if %} — the server knows the answer at first paint, and does
 * render `data-on-track` from it. But an element that is not in the DOM
 * cannot come back: pick a day on the track from the calendar after an
 * older one and the scrubber would stay gone until a reload. The server
 * settles the FIRST answer so nothing has to correct itself a frame later;
 * this module keeps it current after that.
 *
 * It owns visibility and nothing else. The date itself belongs to
 * map_scrubber.js, which is also what dispatches the event read here.
 */

(function mapScrubberRevealInit() {
  const scrubber = document.getElementById('season-scrubber');
  if (!scrubber) return;

  const trackStart = scrubber.dataset.seasonStart || '';
  const trackEnd = scrubber.dataset.seasonEnd || '';
  const todayKey = scrubber.dataset.today || '';

  /**
   * Whether `dateKey` falls on the scrubber's track.
   *
   * String comparison, not Date.parse: these are all ``YYYY-MM-DD``, a
   * format whose lexical order IS its chronological order.
   *
   * @param {string} dateKey
   * @returns {boolean} False when the bounds are missing, which is the safe
   *   reading — no track means nowhere for the thumb to sit.
   */
  const onTrack = (dateKey) => {
    if (!dateKey || !trackStart || !trackEnd) return false;
    return dateKey >= trackStart && dateKey <= trackEnd;
  };

  /**
   * @param {boolean} shown
   * @returns {void}
   */
  const apply = (shown) => {
    scrubber.dataset.onTrack = String(shown);
  };

  // ``date: null`` means the visitor has chosen no day (SNOW-660) — a cold
  // boot, or a step back out of a chosen one. Today is the day the scrubber
  // would rest on in that state, so today decides, and today is always on
  // the track.
  document.addEventListener('snowdesk:date-changed', (e) => {
    const dateKey = (e.detail && e.detail.date) || todayKey;
    apply(onTrack(dateKey));
  });
})();
