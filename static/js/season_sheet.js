/*
 * static/js/season_sheet.js — the bulletin page's season-heatmap sheet
 * (SNOW-117, extracted from bulletin.html by SNOW-621).
 *
 * `apps/public/templates/public/bulletin.html` carried 232 lines of inline
 * JavaScript across four blocks, the bulk of it this one. Inline script
 * cannot be reached by the Vitest harness, so the two pieces here with
 * exact numeric behaviour — the touch threshold and the first-open latch —
 * had no unit coverage and could not get any while they lived in the
 * template. Finding M14.
 *
 * Only this block moved. The other three are short enough that extracting
 * them would trade one problem for another.
 *
 * What it owns
 * ------------
 * The sheet is a fixed-position shell whose open/closed state lives in
 * `data-season-sheet` on the shell itself; the trigger mirrors it in
 * `aria-expanded`. The grid inside is deferred — a `#season-grid`
 * placeholder fires one HTMX GET on first open via the custom
 * `snowdesk:load` event (SNOW-170) — so opening does two different things
 * depending on whether that fetch has happened yet. Once it lands, the
 * page's own day is selected — ringed, and the readout filled from it — and
 * a click on any rated day selects that day instead of navigating
 * (SNOW-1158).
 *
 * Loaded with `defer` from `bulletin.html`, inside the same
 * `{% if season_calendar %}` guard that renders the markup, so the
 * elements exist by the time it runs. It also returns early when they are
 * absent, which is what makes it safe to unit-test against a partial
 * fixture.
 */

(function () {
  'use strict';

  // Squared pixel distance a touch may travel and still count as a tap
  // rather than a scroll — 8px, squared to avoid a square root per
  // touchend.
  //
  // The backdrop dismisses the sheet, and the grid inside it scrolls
  // horizontally. Without a threshold, a scroll gesture that starts on the
  // backdrop lifts into a synthetic click and closes the sheet the user was
  // trying to read. 8px is the usual slop allowance for a finger that meant
  // to stay still.
  var TAP_SLOP_SQUARED = 64;

  // A ringed day of the calendar — never the key's ringed swatch.
  var DAY_RING = '.season-day-ring:not(.season-key-swatch)';

  var sheet = document.querySelector('[data-season-sheet]');
  var trigger = document.querySelector('[data-season-trigger]');
  if (!sheet || !trigger) return;

  /**
   * Centre the scroller on the selected day, or on today.
   *
   * @returns {void}
   */
  function scrollToSelected() {
    var scroller = sheet.querySelector('[data-season-scroll]');
    var target = sheet.querySelector(DAY_RING);
    if (scroller && target) {
      scroller.scrollLeft = Math.max(
        0,
        target.offsetLeft - scroller.clientWidth / 2 + target.clientWidth / 2,
      );
    }
  }

  /**
   * Open or close the sheet.
   *
   * The first open and every later one differ: the grid is fetched lazily,
   * so the first open can only start the request and must leave the scroll
   * to the `htmx:afterSwap` handler once the grid is actually in the DOM.
   * A later open has the grid already and scrolls immediately. The
   * `data-loaded` latch is what tells them apart, and it is set BEFORE the
   * trigger fires so a double-tap cannot start two fetches.
   *
   * @param {'open'|'closed'} state
   * @returns {void}
   */
  function setState(state) {
    sheet.dataset.seasonSheet = state;
    trigger.setAttribute('aria-expanded', state === 'open' ? 'true' : 'false');
    if (state !== 'open') return;

    var grid = document.getElementById('season-grid');
    if (grid && grid.dataset.loaded !== 'true') {
      grid.dataset.loaded = 'true';
      if (window.htmx) {
        window.htmx.trigger(grid, 'snowdesk:load');
      }
    } else {
      scrollToSelected();
    }
  }

  /**
   * Select a day: ring it and fill the readout from it (SNOW-1158).
   *
   * Everything the readout says is server-rendered: every level's tile
   * digit and name sit hidden in the readout, and each cell carries its own
   * dates as `data-day-long` / `data-day-short`, formatted in the page's
   * language. This only chooses among them, so no user-facing string is
   * built here. The cell's `data-level` is the day's peak (the
   * compressed-views rule), so a two-level day reads as its higher level.
   *
   * The link points at the cell's own bulletin. It is hidden on the page's
   * own day — it would link to the page it is on — and on a day with no
   * bulletin, which has none to read.
   *
   * @param {Element} grid  the #season-grid container
   * @param {Element} cell  the `.season-day` to select
   * @returns {void}
   */
  function selectDay(grid, cell) {
    // The key's "Selected day" swatch wears the ring too; leave it on.
    grid.querySelectorAll(DAY_RING).forEach(function (el) {
      el.classList.remove('season-day-ring');
    });
    cell.classList.add('season-day-ring');

    var readout = sheet.querySelector('[data-season-readout]');
    if (!readout) return;
    var level = cell.getAttribute('data-level') || 'no_rating';
    var tile = readout.querySelector('[data-readout-tile]');
    if (tile) tile.setAttribute('data-level', level);
    readout.querySelectorAll('[data-readout-level]').forEach(function (el) {
      el.hidden = el.getAttribute('data-readout-level') !== level;
    });
    var longDate = cell.getAttribute('data-day-long');
    var dateEl = readout.querySelector('[data-readout-date]');
    if (dateEl && longDate) dateEl.textContent = longDate;
    var shortDate = cell.getAttribute('data-day-short');
    var dayEl = readout.querySelector('[data-readout-day]');
    if (dayEl && shortDate) dayEl.textContent = shortDate;
    var link = readout.querySelector('[data-readout-link]');
    if (link) {
      var href = cell.getAttribute('href');
      if (href) link.setAttribute('href', href);
      var isPageDay = cell.getAttribute('data-date') === grid.dataset.selectedDate;
      link.hidden = isPageDay || !href || level === 'no_rating';
    }
    readout.hidden = false;
  }

  // After the HTMX swap completes: select the page's own day, then scroll.
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (!e.detail || !e.detail.target || e.detail.target.id !== 'season-grid') return;
    var grid = e.detail.target;
    // The selected-day ring is applied client-side from data-selected-date
    // rather than server-rendered, so the cached grid partial is the same
    // bytes for every day of the season.
    var selectedDate = grid.dataset.selectedDate;
    if (selectedDate) {
      var cell = grid.querySelector('.season-day[data-date="' + selectedDate + '"]');
      if (cell) selectDay(grid, cell);
    }
    scrollToSelected();
  });

  // With JS a click (Enter on a focused link is a click) on a rated day
  // selects it instead of following its link; the readout's link is how the
  // reader then goes to that day. Without JS the cells are plain links.
  // Delegated, because the grid is swapped in after this runs.
  var gridEl = document.getElementById('season-grid');
  if (gridEl) {
    gridEl.addEventListener('click', function (e) {
      var target = /** @type {Element|null} */ (e.target);
      var cell = target && target.closest ? target.closest('a.season-day') : null;
      if (!cell || !gridEl.contains(cell)) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      selectDay(gridEl, cell);
    });
  }

  trigger.addEventListener('click', function () {
    setState(sheet.dataset.seasonSheet === 'open' ? 'closed' : 'open');
  });

  sheet.querySelectorAll('[data-season-dismiss]').forEach(function (el) {
    if (el.tagName === 'BUTTON') {
      // The ✕ close button — a plain click, with no scroll to conflict with.
      el.addEventListener(
        'click',
        function () {
          setState('closed');
        },
        false,
      );
      return;
    }

    // The backdrop. Touch-aware: dismiss only when the finger did not
    // travel far enough to have been a scroll.
    var startX = 0;
    var startY = 0;
    el.addEventListener(
      'touchstart',
      function (e) {
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        el.classList.add('is-pressing');
      },
      { passive: true },
    );
    el.addEventListener('touchend', function (e) {
      el.classList.remove('is-pressing');
      // Always suppress the synthetic click — the click listener below is a
      // desktop fallback only. Without this a scroll gesture leaks a ghost
      // click and the sheet closes as the user lifts their finger.
      e.preventDefault();
      var dx = e.changedTouches[0].clientX - startX;
      var dy = e.changedTouches[0].clientY - startY;
      if (dx * dx + dy * dy < TAP_SLOP_SQUARED) {
        setState('closed');
      }
    });
    el.addEventListener('touchcancel', function () {
      el.classList.remove('is-pressing');
    });
    el.addEventListener('click', function () {
      setState('closed');
    });
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && sheet.dataset.seasonSheet === 'open') {
      setState('closed');
    }
  });
}());
