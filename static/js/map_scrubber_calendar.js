/*
 * static/js/map_scrubber_calendar.js — the scrubber's month-grid date picker
 * (SNOW-792).
 *
 * The DOM half of the control; the maths is in calendar_core.js
 * (window.pwaCalendarCore), which is where the grid construction, the month
 * clamping and the selectable-day derivation are unit-tested.
 *
 * Loads AFTER map_scrubber.js — document order is execution order for
 * deferred classic scripts — because the ``snowdesk:scrub-to`` listener it
 * dispatches into is bound there. Shared helpers (``getSeasonRatings``,
 * ``readUrlDateParam``) come from static/js/map_shared.js.
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
 * ## Which days are selectable
 *
 * The same season-ratings payload the scrubber snaps releases to. A day the
 * cache has no frame for cannot be painted, so offering it would be
 * offering a click that silently lands somewhere else — which is the
 * imprecision this control exists to fix. Days outside the season window
 * are disabled by the same rule.
 *
 * The toggle carries ``.scrubber-transport-button``, whose existing
 * ``data-state="loading"`` rule hides it along with the transport controls,
 * so the button cannot be pressed before the data behind it has arrived.
 *
 * ## Stacking
 *
 * The popup is a child of #season-scrubber (z-index 2) but has to draw over
 * the bottom-right control stack (z-index 4), which sits exactly where a
 * popup opening upward from this pill lands. A child cannot escape its
 * parent's stacking context, so the OPEN state is mirrored onto the pill as
 * ``data-calendar="open"`` and the CSS lifts the whole pill while it holds.
 */

(function scrubberCalendarInit() {
  const root = document.getElementById('scrubber-calendar');
  const toggle = document.getElementById('scrubber-calendar-toggle');
  if (!root || !toggle) return;

  const core = window.pwaCalendarCore;
  // No fallback body here, unlike map_scrubber.js's inline mirrors of
  // scrubber_core.js: this module is new with its core, so there is no
  // pre-extraction code to fall back to, and a picker that renders a grid
  // from guessed maths would be worse than one that stays shut. The
  // scrubber is untouched and remains the way to change the date.
  if (!core) return;

  const scrubber = document.getElementById('season-scrubber');
  const grid = document.getElementById('scrubber-calendar-grid');
  const monthLabel = document.getElementById('scrubber-calendar-month');
  const prevButton = document.getElementById('scrubber-calendar-prev');
  const nextButton = document.getElementById('scrubber-calendar-next');

  const seasonStart = root.dataset.seasonStart || '';
  const seasonEnd = root.dataset.seasonEnd || '';
  const todayKey = root.dataset.today || '';
  // Localised month names, January first — see the partial for why they
  // travel as one pipe-separated attribute rather than twelve.
  const MONTH_NAMES = (root.dataset.months || '').split('|');

  const OVERLAY_NAME = 'scrubber-calendar';

  const firstMonth = core.monthKeyOf(seasonStart);
  const lastMonth = core.monthKeyOf(seasonEnd);

  // The set of days carrying ratings, once the season fetch resolves.
  // ``null`` until then, which buildMonthGrid reads as "no data set to
  // filter by" — but the toggle is hidden while the scrubber is loading,
  // so the popup cannot actually be opened in that state.
  let available = null;
  let visibleMonth = firstMonth;

  getSeasonRatings()
    .then((data) => {
      available = core.selectableDates(data, seasonStart, seasonEnd);
      if (isOpen()) render();
    })
    .catch(() => {
      // The scrubber shows the failure in its own loading strip and the
      // data-state="error" rule hides this toggle with the rest, so there
      // is nothing for this module to say that would not be a second copy
      // of the same message.
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
    const selected = currentDateKey();
    const cells = core.buildMonthGrid(visibleMonth, {
      available: available,
      seasonStart: seasonStart,
      seasonEnd: seasonEnd,
      selected: selected,
      today: todayKey,
    });

    const monthIndex = parseInt(visibleMonth.slice(5, 7), 10) - 1;
    const monthName = MONTH_NAMES[monthIndex] || '';
    monthLabel.textContent = monthName + ' ' + visibleMonth.slice(0, 4);

    // Both steps are disabled rather than hidden at the season bounds: a
    // control that vanishes at an edge moves the two beside it, and the
    // header would reflow every time the visitor reached November.
    prevButton.disabled = !firstMonth || visibleMonth <= firstMonth;
    nextButton.disabled = !lastMonth || visibleMonth >= lastMonth;

    grid.replaceChildren();
    for (const cell of cells) {
      if (cell.blank) {
        const filler = document.createElement('span');
        filler.className = 'scrubber-calendar-day is-blank';
        grid.appendChild(filler);
        continue;
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'scrubber-calendar-day';
      button.textContent = String(cell.day);
      button.dataset.date = cell.dateKey;
      // The visible text is a bare number, so the accessible name has to
      // carry the month and year or every cell in the grid announces as
      // "16". Composed from the server's own month names — no English
      // literal reaches the DOM (docs/i18n.md).
      button.setAttribute('aria-label', cell.day + ' ' + monthName + ' ' + visibleMonth.slice(0, 4));
      if (!cell.selectable) button.disabled = true;
      if (cell.selected) {
        button.classList.add('is-selected');
        button.setAttribute('aria-current', 'date');
      }
      if (cell.isToday) button.classList.add('is-today');
      grid.appendChild(button);
    }
  }

  /**
   * Reflect the open/closed state onto the popup, the toggle and the pill.
   *
   * @param {boolean} open Whether the popup should be on screen.
   * @returns {void}
   */
  function setOpen(open) {
    root.dataset.state = open ? 'open' : 'closed';
    root.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    // See the stacking note in the header: the pill has to outrank the
    // bottom-right control stack while this is up.
    if (scrubber) {
      if (open) scrubber.dataset.calendar = 'open';
      else delete scrubber.dataset.calendar;
    }
  }

  /**
   * Open the popup on the month holding the current date.
   *
   * With no day chosen it opens on today's month, clamped into the season —
   * out of season that is the last month with data rather than an empty
   * August the visitor would have to page back out of.
   *
   * @returns {void}
   */
  function open() {
    const anchor = currentDateKey() || todayKey;
    visibleMonth = core.clampMonthKey(core.monthKeyOf(anchor) || firstMonth, seasonStart, seasonEnd);
    render();
    // Announced before the popup is revealed, so whatever it replaces is
    // gone by the time this is on screen.
    window.pwaMapOverlays?.opening(OVERLAY_NAME);
    setOpen(true);
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
   * Step the visible month, staying inside the season window.
   *
   * @param {number} delta Months to move; negative goes back.
   * @returns {void}
   */
  function step(delta) {
    const next = core.clampMonthKey(
      core.shiftMonth(visibleMonth, delta),
      seasonStart,
      seasonEnd
    );
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
