/*
 * static/js/map_season_ribbon.js — the danger ribbon painted into the scrubber track, and its region readout.
 *
 * SNOW-610, step 4: extracted verbatim from map.js. Runs at parse time and
 * binds its own DOM, so it loads AFTER map.js in the position it held
 * inside that file — document order is execution order for deferred
 * classic scripts. Shared state (`MAP`, `FEATURE_BY_REGION_ID`,
 * `COUNTRY_STATE`, …) comes from static/js/map_state.js; shared helpers
 * (`getSeasonRatings`, `repaintRegionsForDate`, the localStorage trio)
 * from static/js/map_shared.js. Both load first.
 */

// SNOW-314: Season ribbon — the season scrubber's track doubles as the danger
// ribbon. When a region is the focus, one decorative coloured cell per season
// day is painted into the track (behind the thumb); a persistent readout names
// that region and shows its danger for the scrubbed day. Both the track fill
// and the readout are driven from the in-memory season-ratings cache (same
// payload as the scrubber / timelapse — no extra fetch), so they update live
// during manual scrub AND playback, decoupling region identity from the
// ephemeral map popup. The homepage pre-selects CH-4115; /map/ starts grey
// (no focus) until the user taps a region.
(function seasonRibbonInit() {
  // #season-ribbon is retained as the data carrier (season bounds + default
  // region) and the caption header; the visible cells live in the scrubber
  // track's .scrubber-ribbon fill, the label in #region-readout.
  const ribbonEl = document.getElementById('season-ribbon');
  if (!ribbonEl) return;

  const fill = document.querySelector('.season-scrubber .scrubber-ribbon');
  const trackEl = document.querySelector(
    '.season-scrubber .season-scrubber-track'
  );
  if (!fill || !trackEl) return;

  const readoutEl = document.getElementById('region-readout');
  // The scrubbed date now lives in its own ribbon beside the scrubber
  // (bottom-left), not in the top region chip — see updateReadout below.
  const dateRibbonEl = document.getElementById('map-date-ribbon');
  const readoutSwatch =
    readoutEl && readoutEl.querySelector('.region-readout-swatch');
  const readoutCrumbs =
    readoutEl && readoutEl.querySelector('.region-readout-crumbs');
  const readoutLeaf =
    readoutEl && readoutEl.querySelector('.region-readout-leaf');
  // The "view bulletin" action is now a separate roundel (sibling of the
  // readout pill), not the pill itself; it carries the bulletin href.
  const readoutAction = document.getElementById('region-readout-action');

  // Convert the int rating from the ratings cache to a key string.
  // SNOW-496: thin delegator — see scrubber_core.js's module header.
  const intToKey = (n) => {
    if (window.pwaScrubberCore) return window.pwaScrubberCore.intToKey(n, INT_TO_RATING);
    if (n == null || n < 0 || n >= INT_TO_RATING.length) return 'no_rating';
    return INT_TO_RATING[n];
  };

  // Focus state. Empty region => no focus (grey track, "No region selected"
  // readout, no map highlight).
  //
  // SNOW-656: the focus starts EMPTY. It used to be seeded from
  // ``data-default-region-id`` (CH-4115, Martigny-Verbier), which meant every
  // visitor arrived at a homepage presenting one arbitrary region as though
  // they had chosen it: the chip named it and carried its danger swatch, the
  // season ribbon was painted for it, the download roundel was armed for it,
  // and — until this ticket's first pass — the map outlined it too. Nothing
  // on the page explained why that region and not another, and its swatch
  // colour read as a statement about the map rather than about one region.
  //
  // The empty state this falls back to is not a gap; SNOW-642 designed it
  // deliberately, with a "No region selected" leaf and both header controls
  // visible-but-disabled rather than hidden. The date is still seeded, since
  // the timeline has a day whether or not a region is focused.
  //
  // ``data-default-region-id`` is still rendered and is still read by
  // ``map_region_download.js``'s own seed — see the note there.
  //
  // SNOW-660: the DATE now starts empty too. It was seeded from
  // ``data-default-date`` (server-rendered today) on the reasoning that the
  // timeline has a day whether or not a region is focused — but the map was
  // painting that day's choropleth to match, so a visitor who had asked for
  // nothing arrived at a coloured map explained by a date they never chose.
  // The one source for "which day" is ``?d=``, and null means none yet.
  let cache = null;
  let regionId = null;
  let regionName = null;
  let regionSlug = null;
  // ``readUrlDateParam`` is map_shared.js's, shared as a bare identifier by
  // the classic-script bundle. The typeof guard is for the unit tests, which
  // load this module on its own against a stubbed scope: map_shared.js owns
  // the ``?d=`` validation and this file must not grow a second copy of it,
  // so standalone it reads "no date" rather than reimplementing the parse.
  let dateKey = typeof readUrlDateParam === 'function' ? readUrlDateParam() : null;
  // SNOW-314 prototype: L2 (sub) + L1 (major) names for the breadcrumb.
  // Populated on every region-selected event, which carries the full
  // hierarchy; empty until the visitor picks a region.
  let regionSubName = '';
  let regionMajorName = '';
  // Which region tiers are visible on the map (l1=Major, l2=Minor); the chip
  // breadcrumb mirrors these. Seeded from the persisted overlay state, updated
  // on snowdesk:overlays-changed. The leaf is the region name; it remains in
  // the breadcrumb regardless of the L4 map-layer visibility because it is a
  // text readout, not the polygon layer.
  const overlayVisible = {
    l1: readBoolStorage('snowdesk.map.overlay.l1', false),
    l2: readBoolStorage('snowdesk.map.overlay.l2', false),
  };

  // Paint one decorative cell per CALENDAR DAY across [seasonStart, seasonEnd]
  // for the focused region into the scrubber track. One cell per day (not per
  // data-date) is essential: the scrubber thumb maps date→position linearly
  // over the same range, so a cell-per-day grid lines up with the thumb. A
  // cell-per-data-date grid would compress gap days and drift every cell off
  // its true date — making the colour under the thumb mismatch the map. Gap
  // days (and any days past the last data point) render as no_rating slivers.
  // Cells are pointer-events:none — the track beneath keeps its drag/click; the
  // thumb marks the active day. With no focus or no region data → grey rail.
  const DAY_MS = 86400000;
  const paintTrack = () => {
    if (!cache || !regionId) {
      fill.replaceChildren();
      trackEl.removeAttribute('data-ribbon');
      return;
    }
    const startMs = Date.parse(ribbonEl.dataset.seasonStart);
    const endMs = Date.parse(ribbonEl.dataset.seasonEnd);
    if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs < startMs) {
      fill.replaceChildren();
      trackEl.removeAttribute('data-ribbon');
      return;
    }
    // UTC-midnight stepping (seasonStart parses as UTC midnight); UTC has no
    // DST, so adding exact days and slicing toISOString gives correct ISO keys.
    let regionHasData = false;
    const cells = [];
    for (let t = startMs; t <= endMs; t += DAY_MS) {
      const d = new Date(t).toISOString().slice(0, 10);
      const ratingInt = cache[d] ? cache[d][regionId] : null;
      if (ratingInt != null) regionHasData = true;
      const cell = document.createElement('span');
      cell.className = 'scrubber-ribbon-cell ribbon-cell--' + intToKey(ratingInt);
      // Month-boundary hairline: mark the first-of-month cells (skip the very
      // first cell so there's no leading mark). A pure CSS overlay — see
      // .scrubber-ribbon-cell--month — so it adds no width and the cells stay
      // aligned with the scrubber thumb.
      if (d.slice(8, 10) === '01' && t !== startMs) {
        cell.classList.add('scrubber-ribbon-cell--month');
      }
      cells.push(cell);
    }
    if (!regionHasData) {
      fill.replaceChildren();
      trackEl.removeAttribute('data-ribbon');
      return;
    }
    fill.replaceChildren(...cells);
    trackEl.setAttribute('data-ribbon', 'on');
  };

  // Persist the focused region's border highlight on the map, independent of
  // the popup (which is destroyed during playback). Driven from the
  // module-scope MAP + FEATURE_BY_REGION_ID. Re-asserted on every date change
  // so playback's popup-clear can't strip the selection outline.
  //
  // SNOW-656: ONLY for a region the user actually chose. ``regionId`` is
  // seeded from ``data-default-region-id`` (CH-4115) so the ribbon has a
  // timeline to draw before anything is picked — but pushing that seed onto
  // the MAP meant the homepage always rendered with Martigny-Verbier
  // selected: a black selection ring and an emphasised fill around one region
  // out of 149, with nothing to explain why. Worse, the emphasis made it read
  // as a different colour from its neighbours, so a default that was supposed
  // to be a convenience looked like a data error.
  //
  // The seed still drives the ribbon and the readout chip; it just no longer
  // claims a selection on the map. The map starts genuinely unselected.
  let highlightedFeatureId = null;
  let userHasChosen = false;
  const setHighlight = () => {
    if (!MAP || !userHasChosen) return;
    const feature = regionId ? FEATURE_BY_REGION_ID[regionId] : null;
    const fid = feature ? feature.id : null;
    if (highlightedFeatureId != null && highlightedFeatureId !== fid) {
      MAP.setFeatureState(
        { source: 'regions', id: highlightedFeatureId }, { selected: false },
      );
    }
    if (fid != null) {
      MAP.setFeatureState({ source: 'regions', id: fid }, { selected: true });
    }
    highlightedFeatureId = fid;
    if (MAP.triggerRepaint) MAP.triggerRepaint();
  };

  // Update the two split readouts. The bottom #map-date-ribbon always shows
  // the scrubbed date (the timeline's own readout, region or no region); the
  // top #region-readout chip names the focused region and shows its danger
  // swatch. Pure in-memory lookup (no fetch), so it is safe to call on every
  // scrub/preview/playback frame.
  //
  // SNOW-642: the chip is no longer hidden until a region is focused. It was,
  // and because both of its sibling controls key off `.has-region`, the whole
  // .ribbon-header emptied out at once — which read as the ribbon vanishing
  // rather than as "nothing is selected". It now persists with a "No region
  // selected" leaf and both controls visible-but-disabled. `.has-region` is
  // still toggled: the CSS uses it for the empty-state text treatment, and it
  // remains the one place the focused/unfocused distinction is expressed.
  const updateReadout = () => {
    // Bottom date ribbon — day-first, title-case date ("18 May 2026") matching
    // the popup card; deliberately not the uppercase scrubber format.
    //
    // SNOW-660: with no day chosen it says "No date selected" rather than
    // hiding. The map is uncoloured in that state, and a blank choropleth
    // beside a ribbon that has silently disappeared reads as a broken page;
    // the prompt names the state and points at the timeline that resolves it.
    if (dateRibbonEl) {
      dateRibbonEl.hidden = false;
      dateRibbonEl.textContent = dateKey
        ? formatDatePopup(dateKey)
        : MAP_STRINGS['map-date-none'];
    }
    if (!readoutEl) return;
    // SNOW-660: region and date are independent. A region picked with no day
    // chosen still names itself — the chip answers "which region", and
    // making that answer wait on a date the visitor has not given would
    // report "No region selected" about a region they just tapped.
    const hasRegion = !!(regionId && regionName);
    // SNOW-642: no `readoutEl.hidden = !hasRegion` any more — see the note
    // above. The template no longer ships `hidden` either, so the chip is
    // visible from first paint rather than appearing on first selection.
    readoutEl.classList.toggle('has-region', hasRegion);
    if (hasRegion) {
      // Breadcrumb: Major (L1) › Minor (L2) › Micro (L4, the leaf), including
      // only the tiers currently visible on the map. The leaf is always shown.
      if (readoutLeaf) readoutLeaf.textContent = regionName;
      if (readoutCrumbs) {
        const crumbs = [];
        if (overlayVisible.l1 && regionMajorName) crumbs.push(regionMajorName);
        if (overlayVisible.l2 && regionSubName) crumbs.push(regionSubName);
        readoutCrumbs.textContent = crumbs.length ? crumbs.join(' › ') + ' › ' : '';
      }
      // SNOW-660: no day chosen ⇒ no rating to show. `cache[null]` is
      // already undefined, so this resolves to 'no_rating' and the swatch
      // below goes transparent — the chip names the region without claiming
      // a danger level for a day nobody asked for.
      const ratingInt = cache && cache[dateKey] ? cache[dateKey][regionId] : null;
      const key = intToKey(ratingInt);
      // The swatch is its own element (a colour block that divides date from
      // name), not a pseudo-element on the date.
      if (readoutSwatch) {
        readoutSwatch.style.background =
          key === 'no_rating' ? 'transparent' : 'var(--color-eaws-' + key.replace(/_/g, '-') + ')';
      }
      // Point the action roundel at the bulletin: /<region_id>/<slug>/<date>/.
      // Region id is lowercased to match the canonical URL form.
      // SNOW-660: a bulletin is one region on one DAY, so with no day chosen
      // there is no URL to build — the roundel sits disabled, exactly as it
      // does for a region with no slug below.
      if (readoutAction) {
        if (regionSlug && dateKey) {
          readoutAction.setAttribute(
            'href',
            '/' + regionId.toLowerCase() + '/' + regionSlug + '/' + dateKey + '/',
          );
          _setActionEnabled(readoutAction, true);
        } else {
          // Focused, but with no slug (or no day) there is still no bulletin
          // URL to build — so the roundel is as dead as it is with nothing
          // selected, and says so the same way.
          readoutAction.removeAttribute('href');
          _setActionEnabled(readoutAction, false);
        }
      }
    } else {
      // SNOW-642: the empty state. The leaf carries the message rather than
      // a new element, so the chip keeps its shape and there is one place
      // that says what region is showing.
      if (readoutLeaf) readoutLeaf.textContent = MAP_STRINGS['no-region'];
      // Clear the crumbs (not the wrapper's textContent, which would detach
      // the cached child spans) — a breadcrumb prefix on "No region
      // selected" would read as a region called that, inside those parents.
      if (readoutCrumbs) readoutCrumbs.textContent = '';
      if (readoutSwatch) readoutSwatch.style.background = 'transparent';
      if (readoutAction) {
        readoutAction.removeAttribute('href');
        _setActionEnabled(readoutAction, false);
      }
    }
  };

  /**
   * Enable or disable the view-bulletin roundel (SNOW-642).
   *
   * It stays on screen with nothing selected, so "no bulletin to open" has
   * to be a state rather than an absence. An <a> with no href is already
   * inert and untabbable, but that is invisible to a screen reader and to
   * the eye — this makes it legible to both, and the CSS keys its dimming
   * off the same aria-disabled a screen reader announces, so the two
   * cannot drift apart.
   *
   * tabindex is set explicitly rather than left to the missing href: the
   * roundel is a real tab stop the moment a region IS selected, and an
   * element that silently enters and leaves the tab order is harder to
   * reason about than one that states where it is.
   *
   * @param {HTMLElement} el
   * @param {boolean} enabled
   */
  function _setActionEnabled(el, enabled) {
    if (enabled) {
      el.removeAttribute('aria-disabled');
      el.removeAttribute('tabindex');
    } else {
      el.setAttribute('aria-disabled', 'true');
      el.setAttribute('tabindex', '-1');
    }
  }

  const refresh = () => {
    paintTrack();
    updateReadout();
    setHighlight();
  };

  // Tap a region → it becomes the focus. This is the ONLY thing that lets
  // setHighlight touch the map (SNOW-656) — it fires for a tap, a resort pin,
  // a search hit and a ``#CH-xxxx`` deep link, i.e. every route by which a
  // user genuinely picks a region, and for nothing else.
  document.addEventListener('snowdesk:region-selected', (e) => {
    userHasChosen = true;
    regionId = (e.detail && e.detail.region_id) || null;
    regionName = (e.detail && e.detail.region_name) || null;
    regionSlug = (e.detail && e.detail.region_slug) || null;
    regionSubName = (e.detail && e.detail.subregion_name) || '';
    regionMajorName = (e.detail && e.detail.major_name) || '';
    refresh();
  });

  // SNOW-314 prototype: a Major/Minor overlay toggle changes which breadcrumb
  // tiers are shown. Re-render the readout in place (the region is unchanged).
  document.addEventListener('snowdesk:overlays-changed', (e) => {
    const key = e.detail && e.detail.key;
    if (key === 'l1' || key === 'l2') {
      overlayVisible[key] = !!(e.detail && e.detail.visible);
      updateReadout();
    }
  });

  // Scrubber commit / live drag preview → update the readout's date, and
  // re-assert the highlight (playback destroys the popup and its outline).
  // SNOW-660: `|| null`, not `|| dateKey`. A commit carrying `date: null`
  // (the scrubber's popstate handler, stepping back to a URL with no `?d=`)
  // means the day has been UNchosen, and keeping the previous one here would
  // leave the ribbon naming a date the map is no longer painted for.
  document.addEventListener('snowdesk:date-changed', (e) => {
    dateKey = (e.detail && e.detail.date) || null;
    updateReadout();
    setHighlight();
  });
  document.addEventListener('snowdesk:date-preview', (e) => {
    dateKey = (e.detail && e.detail.date) || dateKey;
    updateReadout();
  });

  // Apply the initial highlight once the region features are loaded (the
  // default homepage focus, or a deep-linked region).
  document.addEventListener('snowdesk:regions-loaded', setHighlight);

  // Paint the default focus (homepage) once the ratings cache resolves.
  getSeasonRatings().then((c) => {
    cache = c || null;
    refresh();
  });
})();
