/*
 * static/js/map_calendar.js — the map's date picker (SNOW-792).
 *
 * The DOM half of the control; the maths is in calendar_core.js
 * (window.pwaCalendarCore), which is where the grid construction, the month
 * clamping and the range derivation are unit-tested.
 *
 * Loads AFTER map_scrubber.js — document order is execution order for
 * deferred classic scripts — because the ``snowdesk:scrub-to`` listener it
 * dispatches into is bound there. Shared helpers (``getSeasonRatings``,
 * ``readUrlDateParam``) come from static/js/map_shared.js.
 *
 * ## The button and the panel live in different places, on purpose
 *
 * The tap target is a sixth control in the season-scrubber row, with the
 * other date affordances — that is where a visitor already is when they
 * want a different day.
 *
 * The panel is not next to it. It opens in the bottom-right overlay slot,
 * with the same geometry as the favourites, observations and routes
 * sheets, so every panel over the map appears in one place whichever
 * control opened it. That means it is ``position: fixed`` and rendered at
 * top level rather than inside the scrubber — a fixed element is still
 * confined by any ancestor that creates a stacking context — and it is
 * handed to ``window.pwaOverlayBounds.positionSheet`` on open, the same
 * call ``map_sheet.js`` and ``map_downloads_manager.js`` make, which
 * measures the room the scrubber and the roundel column leave. Below the
 * ``sm`` breakpoint that call deliberately writes nothing and the panel
 * docks to the bottom edge, exactly as the sheets do.
 *
 * ## It has no commit path of its own
 *
 * Picking a day dispatches ``snowdesk:scrub-to`` and stops. map_scrubber.js
 * owns the single commit point (``commitDate``) that repaints the
 * choropleth, writes ``?d=`` and announces ``snowdesk:date-changed``; a
 * second surface reimplementing any part of that is a second place for the
 * URL and the paint to drift apart.
 *
 * That event name is not new. It was the documented click-to-scrub contract
 * on the season ribbon until SNOW-615 removed it — the ribbon cells had
 * stopped carrying click handlers when they moved into the scrubber track,
 * so the listener had no dispatcher left. This gives it one.
 *
 * ## What it offers, and what it refuses
 *
 * Every day from the earliest the site holds data for up to TODAY. The
 * future is the only hard stop: nothing on the map can answer for a day
 * that has not happened.
 *
 * The avalanche season is a highlight inside that range, not a fence around
 * it. An earlier draft refused every day outside it, which was wrong the
 * moment the map grew a weather layer — weather has an answer for a day in
 * September, and the picker could not reach it.
 *
 * That highlight is the ONLY per-day mark. A dot on the days actually
 * carrying a bulletin came and went: the thing it distinguished — a gap in
 * the archive inside the season — is an operator's concern, and a visitor
 * who lands on such a day already sees an uncoloured map. Two marks for one
 * answer is one more than the grid can spend.
 */

(function mapCalendarInit() {
  const root = document.getElementById('map-calendar');
  const toggle = document.getElementById('map-calendar-toggle');
  if (!root || !toggle) return;

  const core = window.pwaCalendarCore;
  // No fallback body here, unlike map_scrubber.js's inline mirrors of
  // scrubber_core.js: this module is new with its core, so there is no
  // pre-extraction code to fall back to, and a picker that renders a grid
  // from guessed maths would be worse than one that stays shut. The
  // scrubber is untouched and remains the way to change the date.
  if (!core) return;

  const grid = document.getElementById('map-calendar-grid');
  const monthLabel = document.getElementById('map-calendar-month');
  const prevButton = document.getElementById('map-calendar-prev');
  const nextButton = document.getElementById('map-calendar-next');

  const seasonStart = root.dataset.seasonStart || '';
  const seasonEnd = root.dataset.seasonEnd || '';
  const todayKey = root.dataset.today || '';
  // Localised month names, January first — see the partial for why they
  // travel as one pipe-separated attribute rather than twelve.
  const MONTH_NAMES = (root.dataset.months || '').split('|');

  const OVERLAY_NAME = 'map-calendar';

  // The reachable range. The ceiling is fixed at today and never moves; the
  // floor starts at the season and drops to the archive's first day once
  // the ratings fetch resolves, so paging back is bounded by real data
  // rather than by an arbitrary constant.
  const maxKey = todayKey;
  let minKey = seasonStart;

  let visibleMonth = core.clampMonthKey(core.monthKeyOf(todayKey), minKey, maxKey);

  // The cache is read for one thing: how far back the arrows may page. The
  // fetch is the scrubber's, memoised and already in flight, so this costs
  // nothing beyond the callback.
  getSeasonRatings()
    .then((data) => {
      minKey = core.earliestKnownDate(data, seasonStart);
      if (isOpen()) render();
    })
    .catch(() => {
      // The picker still works without it: every day up to today stays
      // selectable and the season highlight is server-rendered data, so all
      // that is lost is a floor tighter than the season start. The scrubber
      // reports the fetch failure in its own loading strip, so saying it
      // again here would be a second copy of one message.
    });

  /** @returns {boolean} Whether the popup is on screen. */
  function isOpen() {
    return root.dataset.state === 'open';
  }

  /**
   * The date the map is currently showing, or ``''`` when no day has been
   * chosen.
   *
   * Read from the URL rather than tracked in a local: ``?d=`` is where the
   * chosen day actually lives (SNOW-660), and it is written by the
   * scrubber, the timelapse and the back button alike. A local would be a
   * fourth opinion.
   *
   * @returns {string} A ``YYYY-MM-DD`` date key, or ``''``.
   */
  function currentDateKey() {
    return readUrlDateParam() || '';
  }

  /**
   * Write the grid and the header for ``visibleMonth``.
   *
   * @returns {void}
   */
  function render() {
    const cells = core.buildMonthGrid(visibleMonth, {
      min: minKey,
      max: maxKey,
      seasonStart: seasonStart,
      seasonEnd: seasonEnd,
      selected: currentDateKey(),
      today: todayKey,
    });

    const monthIndex = parseInt(visibleMonth.slice(5, 7), 10) - 1;
    const monthName = MONTH_NAMES[monthIndex] || '';
    const year = visibleMonth.slice(0, 4);
    monthLabel.textContent = monthName + ' ' + year;

    // Both steps are disabled rather than hidden at the range bounds: a
    // control that vanishes at an edge moves the two beside it, and the
    // header would reflow every time the visitor reached the current month.
    const firstMonth = core.monthKeyOf(minKey);
    const lastMonth = core.monthKeyOf(maxKey);
    prevButton.disabled = !firstMonth || visibleMonth <= firstMonth;
    nextButton.disabled = !lastMonth || visibleMonth >= lastMonth;

    grid.replaceChildren();
    for (const cell of cells) {
      if (cell.blank) {
        const filler = document.createElement('span');
        filler.className = 'map-calendar-day is-blank';
        grid.appendChild(filler);
        continue;
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'map-calendar-day';
      button.textContent = String(cell.day);
      button.dataset.date = cell.dateKey;
      // The visible text is a bare number, so the accessible name has to
      // carry the month and year or every cell in the grid announces as
      // "16". Composed from the server's own month names — no English
      // literal reaches the DOM (docs/i18n.md).
      button.setAttribute('aria-label', cell.day + ' ' + monthName + ' ' + year);
      if (!cell.selectable) button.disabled = true;
      // The season band — "bulletins run here". It does not gate the click.
      if (cell.inSeason) button.classList.add('in-season');
      if (cell.selected) {
        button.classList.add('is-selected');
        button.setAttribute('aria-current', 'date');
      }
      if (cell.isToday) button.classList.add('is-today');
      grid.appendChild(button);
    }
  }

  /**
   * Reflect the open/closed state onto the popup and the roundel.
   *
   * @param {boolean} open Whether the popup should be on screen.
   * @returns {void}
   */
  function setOpen(open) {
    root.dataset.state = open ? 'open' : 'closed';
    root.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  /**
   * Open the popup on the month holding the current date.
   *
   * With no day chosen it opens on today's month — the top of the range,
   * and the month a visitor is most likely to want.
   *
   * @returns {void}
   */
  function open() {
    const anchor = currentDateKey() || todayKey;
    visibleMonth = core.clampMonthKey(core.monthKeyOf(anchor), minKey, maxKey);
    render();
    // Announced before the popup is revealed, so whatever it replaces is
    // gone by the time this is on screen.
    window.pwaMapOverlays?.opening(OVERLAY_NAME);
    setOpen(true);
    // AFTER the reveal: positionSheet measures the element, and a `hidden`
    // element measures zero. Same ordering as map_sheet.js's open().
    window.pwaOverlayBounds?.positionSheet(root);
  }

  /** @returns {void} */
  function close() {
    setOpen(false);
  }

  toggle.addEventListener('click', (e) => {
    // Without this the opening click reaches the document handler below
    // and reads as a click away, so the popup would close in the same tick
    // it opened.
    e.stopPropagation();
    if (isOpen()) close();
    else open();
  });

  /**
   * Step the visible month, staying inside the reachable range.
   *
   * @param {number} delta Months to move; negative goes back.
   * @returns {void}
   */
  function step(delta) {
    const next = core.clampMonthKey(core.shiftMonth(visibleMonth, delta), minKey, maxKey);
    if (!next || next === visibleMonth) return;
    visibleMonth = next;
    render();
  }

  prevButton.addEventListener('click', (e) => {
    e.stopPropagation();
    step(-1);
  });
  nextButton.addEventListener('click', (e) => {
    e.stopPropagation();
    step(1);
  });

  // One delegated listener rather than one per cell: the grid is rebuilt on
  // every month step, and re-binding ~35 handlers each time is work with
  // nothing to show for it.
  grid.addEventListener('click', (e) => {
    e.stopPropagation();
    const button = e.target.closest('button[data-date]');
    if (!button || button.disabled) return;
    // The scrubber owns the commit — see the header.
    document.dispatchEvent(
      new CustomEvent('snowdesk:scrub-to', {
        detail: { date: button.dataset.date, source: 'calendar' },
      })
    );
    close();
  });

  // Keep the selected-day marker honest when the date moves under us — a
  // scrub, a timelapse frame or a back button, all of which announce
  // themselves this way. Only while open: rebuilding a hidden grid is work
  // for nobody, and playback fires this once a frame.
  document.addEventListener('snowdesk:date-changed', () => {
    if (isOpen()) render();
  });

  document.addEventListener('click', (e) => {
    if (!isOpen()) return;
    if (root.contains(e.target)) return;
    close();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen()) {
      close();
      toggle.focus();
    }
  });

  // One overlay open over the map at a time (SNOW-658). The outside-tap
  // dismiss above does not cover this in either direction: a surface opened
  // without a document click never reaches it, and this toggle's own
  // stopPropagation means no other surface's identical handler sees this
  // one open.
  window.pwaMapOverlays?.register(OVERLAY_NAME, { isOpen: isOpen, close: close });
})();
