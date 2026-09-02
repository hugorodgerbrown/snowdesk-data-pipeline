/*
 * static/js/map_scrubber_reveal.js — the calendar is the scrubber's reveal
 * (SNOW-794).
 *
 * The season scrubber's track IS the avalanche season. A day outside it —
 * July, or anything in the off-season — has no position on that track, and
 * the thumb clamping to whichever end it is past was the defect this ticket
 * opened with: the URL said one day and the thumb said another.
 *
 * So the scrubber is present only while the day the map is showing falls
 * inside the season, and the calendar is what puts you either side of that
 * line. Two controls, each honest about its own job: the calendar is "pick a
 * date, any date"; the scrubber is a season scrubber.
 *
 * WHY THE ELEMENT IS ALWAYS RENDERED. _map_embed.html could have wrapped it
 * in an {% if %} — the server knows the answer at first paint, and does
 * render `data-in-season` from it. But an element that is not in the DOM
 * cannot come back: pick an in-season day from the calendar during the
 * off-season and the scrubber would stay gone until a reload. The server
 * settles the FIRST answer so nothing has to correct itself a frame later;
 * this module keeps it current after that.
 *
 * It owns visibility and nothing else. The date itself belongs to
 * map_scrubber.js, which is also what dispatches the event read here.
 */

(function mapScrubberRevealInit() {
  const scrubber = document.getElementById('season-scrubber');
  if (!scrubber) return;

  const seasonStart = scrubber.dataset.seasonStart || '';
  const seasonEnd = scrubber.dataset.seasonEnd || '';
  const todayKey = scrubber.dataset.today || '';

  /**
   * Whether `dateKey` falls inside the season the track spans.
   *
   * String comparison, not Date.parse: these are all ``YYYY-MM-DD``, a
   * format whose lexical order IS its chronological order, and the calendar
   * grid compares its own bounds the same way (calendar_core.js's
   * ``inSeason``). Two surfaces answering one question must answer it the
   * same way.
   *
   * @param {string} dateKey
   * @returns {boolean} False when the bounds are missing, which is the safe
   *   reading — no season means no season scrubber.
   */
  const inSeason = (dateKey) => {
    if (!dateKey || !seasonStart || !seasonEnd) return false;
    return dateKey >= seasonStart && dateKey <= seasonEnd;
  };

  /**
   * @param {boolean} shown
   * @returns {void}
   */
  const apply = (shown) => {
    scrubber.dataset.inSeason = String(shown);
  };

  // ``date: null`` means the visitor has chosen no day (SNOW-660) — a cold
  // boot, or a step back out of a chosen one. Today is the day the scrubber
  // would rest on in that state, so today decides. In winter that shows the
  // scrubber on an unchosen map; in September it does not, which is correct:
  // the season is over and there is nothing to scrub through.
  document.addEventListener('snowdesk:date-changed', (e) => {
    const dateKey = (e.detail && e.detail.date) || todayKey;
    apply(inSeason(dateKey));
  });
})();
