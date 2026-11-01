/**
 * static/js/map_between_seasons.js — the between-seasons note on the map
 * (SNOW-1244).
 *
 * Out of season today's map has no colour, and user testing found nothing
 * on it to say why. #map-between-seasons (rendered only out of season, by
 * public/partials/_map_between_seasons.html) says so along the top of the
 * map and offers a full day to look at instead.
 *
 * Behaviour:
 *   - Waits for the map to settle, as the welcome card does (the page's
 *     load, then the first 'snowdesk:country-ratings-loaded', capped), and
 *     then for the welcome card to go: the two never show together, and
 *     the welcome card speaks first.
 *   - Shown only while the map is on today. Another day has colour, or a
 *     day note of its own; stepping back to today brings the note back.
 *   - Never over a walkthrough: the example or resort guide's bar, pill or
 *     finish card, an object sheet on a phone, or a deep link's surface.
 *   - "Explore a sample bulletin" moves the map to the sample day and
 *     opens the region (data-region-id); the note is done for this visit.
 *   - The "×" records the season (data-season) under STORAGE_KEY, so the
 *     note stays away until the next off-season, which has another label.
 *   - Telemetry: 'map.between_seasons.shown', '.explored', '.dismissed'.
 *   - Placed under the controls along the top (#map-utility-cluster), as
 *     the example guide's bar is on a phone.
 */
(function mapBetweenSeasonsInit() {
  'use strict';

  const note = document.getElementById('map-between-seasons');
  const mapEl = document.getElementById('map');
  if (!note || !mapEl) return;

  const STORAGE_KEY = 'snowdesk.map.between-seasons';
  const RATINGS_WAIT_MS = 6000;
  // After the welcome card's own settle (1.5 s), so it has had its say.
  const SETTLE_MS = 2000;
  const GAP = 8;
  const DEEP_LINK_PARAMS = [
    'route_share', 'trip', 'trip_share', 'favourite', 'resort', 'panel',
    'edit', 'layers', 'region',
  ];
  const BUSY_SURFACES = [
    'map-tour-offer',
    'map-example-guide',
    'map-example-pill',
    'map-example-finish',
    'map-resort-guide-bar',
    'map-resort-guide-pill',
    'map-resort-guide-finish',
    'map-help-overlay',
    'map-help-menu',
  ];

  const season = note.dataset.season || '';
  const sampleDate = note.dataset.sampleDate || '';
  const regionId = note.dataset.regionId || '';
  const scrubber = document.getElementById('season-scrubber');
  const todayKey = scrubber ? scrubber.dataset.today || '' : '';

  // Whether this visit still wants the note: true once the map settles,
  // false for good once it is answered.
  let wanted = false;
  let answered = false;
  let shownOnce = false;
  let date = '';

  /**
   * @param {string} name
   * @param {Object} [props]
   */
  const emit = (name, props) => window.pwaTelemetry?.emit(name, props || {});

  /** @returns {string|null} The season the note was dismissed for. */
  const dismissedFor = () => {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      // Private mode: nothing was stored.
      return null;
    }
  };

  /** @returns {boolean} Whether the map is showing today. */
  const onToday = () => {
    const shown = date || readUrlDateParam() || todayKey;
    return !todayKey || shown === todayKey;
  };

  /** @returns {boolean} Whether a walkthrough or a sheet holds the screen. */
  const screenIsBusy = () => {
    if (document.documentElement.hasAttribute('data-map-sheet')) return true;
    if (mapEl.hasAttribute('data-route-rail-open')) return true;
    return BUSY_SURFACES.some((id) => {
      const el = document.getElementById(id);
      return Boolean(el && !el.hidden);
    });
  };

  /** Put the note under the controls along the top of the map. */
  const place = () => {
    const mapTop = mapEl.getBoundingClientRect().top;
    const cluster = document.getElementById('map-utility-cluster');
    const floor = (cluster ? cluster.getBoundingClientRect().bottom : mapTop) - mapTop + GAP;
    note.style.top = `${Math.round(Math.max(GAP, floor))}px`;
  };

  /** Show or hide the note from the visit's state. */
  const paint = () => {
    const show = wanted && !answered && onToday() && !screenIsBusy();
    note.hidden = !show;
    if (!show) return;
    place();
    if (!shownOnce) {
      shownOnce = true;
      emit('map.between_seasons.shown');
    }
  };

  /** "×": away until the next off-season. */
  const dismiss = () => {
    answered = true;
    note.hidden = true;
    emit('map.between_seasons.dismissed');
    try {
      localStorage.setItem(STORAGE_KEY, season);
    } catch (_) {
      // Private mode: it holds for this page only.
    }
  };

  /** "Explore a sample bulletin": the sample day, and the region on it. */
  const explore = () => {
    answered = true;
    note.hidden = true;
    emit('map.between_seasons.explored');
    if (sampleDate) {
      document.dispatchEvent(new CustomEvent('snowdesk:scrub-to', {
        detail: { date: sampleDate, source: 'between-seasons' },
      }));
    }
    if (regionId) {
      document.dispatchEvent(new CustomEvent('snowdesk:region-open', { detail: { region_id: regionId } }));
    }
  };

  note.querySelector('[data-between-seasons-close]')?.addEventListener('click', dismiss);
  note.querySelector('[data-between-seasons-explore]')?.addEventListener('click', explore);

  document.addEventListener('snowdesk:date-changed', (e) => {
    date = (e.detail && e.detail.date) || '';
    paint();
  });

  // The surfaces that hold the screen open and close without an event of
  // their own, so their `hidden` and the sheet's attribute are watched.
  const watch = new MutationObserver(() => {
    if (wanted && !answered) paint();
  });
  BUSY_SURFACES.forEach((id) => {
    const el = document.getElementById(id);
    if (el) watch.observe(el, { attributes: true, attributeFilter: ['hidden'] });
  });
  watch.observe(document.documentElement, { attributes: true, attributeFilter: ['data-map-sheet'] });
  watch.observe(mapEl, { attributes: true, attributeFilter: ['data-route-rail-open'] });
  window.addEventListener('resize', () => {
    if (!note.hidden) place();
  });

  // ---- When to show ----

  if (season && dismissedFor() === season) return;
  const params = new URLSearchParams(location.search);
  if (DEEP_LINK_PARAMS.some((name) => params.has(name))) return;

  let started = false;
  const start = () => {
    if (started) return;
    started = true;
    setTimeout(() => {
      wanted = true;
      paint();
    }, SETTLE_MS);
  };
  let ratingsLoaded = false;
  let pageLoaded = false;
  // SNOW-957: the boot season may have landed before this deferred script
  // ran, in which case its one-shot event has already gone out.
  // ``SEASON_RATINGS_READY`` is map_shared.js's; the guard keeps the
  // module standalone where that script is not loaded.
  if (typeof SEASON_RATINGS_READY !== 'undefined' && SEASON_RATINGS_READY) {
    ratingsLoaded = true;
  }
  document.addEventListener('snowdesk:country-ratings-loaded', () => {
    ratingsLoaded = true;
    if (pageLoaded) start();
  }, { once: true });
  const afterLoad = () => {
    pageLoaded = true;
    if (ratingsLoaded) start();
    else setTimeout(start, RATINGS_WAIT_MS);
  };
  if (document.readyState === 'complete') afterLoad();
  else window.addEventListener('load', afterLoad, { once: true });
})();
