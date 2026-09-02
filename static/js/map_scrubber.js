/*
 * static/js/map_scrubber.js — the season scrubber — drag to commit a date, and the ?d= URL sync.
 *
 * SNOW-610, step 4: extracted verbatim from map.js. Runs at parse time and
 * binds its own DOM, so it loads AFTER map.js in the position it held
 * inside that file — document order is execution order for deferred
 * classic scripts. Shared state (`MAP`, `FEATURE_BY_REGION_ID`,
 * `COUNTRY_STATE`, …) comes from static/js/map_state.js; shared helpers
 * (`getSeasonRatings`, `repaintRegionsForDate`, the localStorage trio)
 * from static/js/map_shared.js. Both load first.
 */

// SNOW-47: Season-scrubber wires. Drag the thumb → release commits a
// date. The map repaints region colours for that date and the URL gets
// a ``?d=YYYY-MM-DD`` so the page is linkable. Loading ``/map/?d=…``
// on page boot drops the thumb on that date.
//
// SNOW-660: the scrubber no longer commits a date the visitor did not ask
// for. An empty querystring means no day has been chosen, and this module
// leaves it that way — the thumb rests at today's position over an
// uncoloured map until a drag, a playback frame or a ``?d=`` link chooses
// one. Every commit writes ``?d=``, today included, so the URL always says
// which day is showing and a reload comes back to it.
//
// The scrubber owns no data of its own — it consumes the same
// season-ratings payload as the timelapse via getSeasonRatings(), and
// announces date commits via the ``snowdesk:date-changed`` CustomEvent
// so the timelapse IIFE (stop on grab) and the date pill can react
// without seeing each other.
(function seasonScrubberInit() {
  const scrubber = document.getElementById('season-scrubber');
  if (!scrubber) return;
  const track = scrubber.querySelector('.season-scrubber-track');
  const thumb = scrubber.querySelector('.season-scrubber-thumb');
  const todayKey = scrubber.dataset.today;
  const todayPct = parseFloat(scrubber.dataset.todayPct);
  const seasonStartMs = Date.parse(scrubber.dataset.seasonStart);
  const seasonEndMs = Date.parse(scrubber.dataset.seasonEnd);
  const seasonSpanMs = seasonEndMs - seasonStartMs;

  // Convert between a thumb percentage (0..100 along the track) and an
  // ISO date string. Both use the season bounds parsed above and round
  // to the nearest day — the scrubber is intentionally single-day
  // resolution (intraday is a future ticket).
  // SNOW-496: thin delegators — the actual math lives in scrubber_core.js
  // (window.pwaScrubberCore) so it can be unit-tested directly; every
  // closure value below is forwarded unchanged, so behaviour is identical.
  // Inline fallback mirrors the ``self.pwaBasemapCacheCore ||`` idiom in
  // sw.js, so a transient scrubber_core.js load failure can't break the
  // scrubber — the fallback body is byte-identical to the pre-extraction
  // inline code.
  const pctToDateKey = (pct) => {
    if (window.pwaScrubberCore) return window.pwaScrubberCore.pctToDateKey(pct, seasonStartMs, seasonSpanMs);
    const ms = seasonStartMs + (pct / 100) * seasonSpanMs;
    const day = new Date(ms);
    // Snap to UTC midnight to dodge DST edges, then format.
    const y = day.getUTCFullYear();
    const m = String(day.getUTCMonth() + 1).padStart(2, '0');
    const d = String(day.getUTCDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  };
  const dateKeyToPct = (dateKey) => {
    if (window.pwaScrubberCore) {
      return window.pwaScrubberCore.dateKeyToPct(dateKey, seasonStartMs, seasonSpanMs, todayPct);
    }
    const ms = Date.parse(dateKey);
    if (Number.isNaN(ms) || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) {
      return todayPct;
    }
    return Math.max(0, Math.min(100, ((ms - seasonStartMs) / seasonSpanMs) * 100));
  };

  // Cache + sorted-keys, populated lazily by the shared promise below.
  // Used both for repaint and for snap-to-data-day on release. Until the
  // fetch resolves, drag still works — it just won't snap to a real day
  // boundary, which is fine; the next release after the fetch resolves
  // will snap.
  let ratingsCache = null;
  let sortedDates = null;

  // SNOW-236: The effective "today" frame — the latest date in the merged
  // ratings cache that contains at least one visible country. Defaults to
  // todayKey (server-rendered) until the season-ratings fetch resolves and
  // proves that a later or earlier date is actually the last populated one.
  // Mutated by the getSeasonRatings callback and by snowdesk:country-ratings-loaded.
  let effectiveTodayKey = todayKey;

  // SNOW-236: Walk sortedDates from the end and return the latest entry
  // whose payload contains at least one region whose country prefix
  // (region_id.split('-')[0].toLowerCase()) matches an active country in
  // COUNTRY_STATE (the module-scope mirror of the main IIFE's countryState).
  // Falls back to the last entry if nothing matches.
  const deriveEffectiveTodayKey = (dates, cache) => {
    if (window.pwaScrubberCore) {
      return window.pwaScrubberCore.deriveEffectiveTodayKey(dates, cache, COUNTRY_STATE, todayKey);
    }
    if (!dates || dates.length === 0) return todayKey;
    // Dedupe unexpected-prefix warnings per call: a full-season payload
    // can contain hundreds of regions and we don't want one stray prefix
    // (e.g. a future Liechtenstein "LI-…") to spam the console once per
    // region per date.
    const warnedPrefixes = new Set();
    for (let i = dates.length - 1; i >= 0; i--) {
      const dateKey = dates[i];
      const frame = cache[dateKey] || {};
      for (const regionID of Object.keys(frame)) {
        const prefix = regionID.split('-')[0].toLowerCase();
        if (COUNTRY_STATE[prefix] === true) return dateKey;
        // Guard: unexpected prefix — don't silently swallow it.
        if (!(prefix in COUNTRY_STATE) && !warnedPrefixes.has(prefix)) {
          warnedPrefixes.add(prefix);
          console.warn('[map] SNOW-236: unexpected region prefix', prefix, 'in ratings payload');
        }
      }
    }
    // No date matched any active country — return the last date as a safe fallback.
    return dates[dates.length - 1];
  };

  // SNOW-234: transition the scrubber out of the loading state once the
  // season-ratings fetch settles. On success, populate the cache and mark
  // the scrubber ready so the transport controls become visible. On failure,
  // keep the loading placeholder visible (now showing an error message) so
  // the user sees feedback rather than a blank pill.
  getSeasonRatings().then((data) => {
    ratingsCache = data;
    sortedDates = Object.keys(data).sort();
    scrubber.dataset.state = 'ready';

    // SNOW-236: compute the country-aware effective last date — the latest
    // day in the merged cache carrying a visible country.
    //
    // SNOW-660: it is now a SNAP TARGET ONLY, no longer a date this
    // silently commits. Committing it here was literally "the map seeds the
    // last date carrying ratings": on a cold boot with an empty querystring
    // the choropleth painted a day the visitor had not asked for, and
    // nothing on screen named it. It is still what a release-without-a-drag
    // lands on (see `release` below), because that is a user action.
    effectiveTodayKey = deriveEffectiveTodayKey(sortedDates, ratingsCache);
  }).catch(() => {
    scrubber.dataset.state = 'error';
    const loadingEl = scrubber.querySelector('.season-scrubber-loading');
    if (loadingEl) loadingEl.textContent = MAP_STRINGS['season-unavailable'];
  });

  const snapToNearestDataDay = (dateKey) => {
    if (window.pwaScrubberCore) return window.pwaScrubberCore.snapToNearestDataDay(dateKey, sortedDates);
    if (!sortedDates || sortedDates.length === 0) return dateKey;
    let best = sortedDates[0];
    let bestDelta = Math.abs(Date.parse(best) - Date.parse(dateKey));
    for (const d of sortedDates) {
      const delta = Math.abs(Date.parse(d) - Date.parse(dateKey));
      if (delta < bestDelta) { best = d; bestDelta = delta; }
    }
    return best;
  };

  // The single commit point. Updates the thumb, repaints regions, syncs
  // the URL, and notifies the rest of the page. ``opts.silent`` skips
  // the URL write — used by the popstate handler so re-applying a
  // browser-back-restored ``?d=`` doesn't re-write history.
  const commitDate = (dateKey, opts = {}) => {
    const pct = dateKeyToPct(dateKey);
    thumb.style.left = pct + '%';
    scrubber.setAttribute('aria-valuenow', String(Math.round(pct)));
    if (ratingsCache) repaintRegionsForDate(dateKey, ratingsCache);
    // SNOW-660: one URL write, shared with the timelapse — see
    // ``writeUrlDateParam`` in map_shared.js for the replaceState idiom and
    // for why the two surfaces call it at different rates. Every commit
    // writes, today included; ``opts.silent`` still skips it, so re-applying
    // a browser-back-restored ``?d=`` doesn't re-write history.
    if (!opts.silent) writeUrlDateParam(dateKey);
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: dateKey, source: 'scrubber' },
    }));
  };

  // ---- Pointer drag ----
  let dragging = false;
  let pointerId = null;
  let liveDate = null;  // tracked during drag, used by the date-preview event

  // SNOW-614: the date the choropleth is currently painted for. A drag
  // fires pointermove far more often than it crosses into a new day — the
  // thumb moves a pixel at a time across a whole season — and each repaint
  // is a setFeatureState per region. Repainting only on a change turns most
  // frames into a thumb move and an event dispatch.
  let paintedDateKey = null;

  const updateDragVisuals = (clientX) => {
    const rect = track.getBoundingClientRect();
    const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
    thumb.style.left = pct + '%';
    liveDate = pctToDateKey(pct);
    // Dispatch raw liveDate so the date pill follows the thumb exactly.
    document.dispatchEvent(new CustomEvent('snowdesk:date-preview', {
      detail: { date: liveDate, source: 'scrubber' },
    }));
    // Repaint the choropleth live during drag, snapped to the nearest data
    // day so off-data days don't flash everything to no_rating mid-drag.
    if (ratingsCache) {
      const snapped = snapToNearestDataDay(liveDate);
      if (snapped !== paintedDateKey) {
        paintedDateKey = snapped;
        repaintRegionsForDate(snapped, ratingsCache);
      }
    }
  };

  // SNOW-614: pointermove fires faster than the display refreshes, and the
  // only thing a move does is decide what the NEXT frame looks like — so
  // coalescing onto requestAnimationFrame drops the redundant work rather
  // than deferring it. Only the newest position is kept; an older one is
  // already wrong by the time the frame runs.
  let pendingDragX = null;
  let dragFrame = 0;

  const scheduleDragVisuals = (clientX) => {
    pendingDragX = clientX;
    if (dragFrame) return;
    dragFrame = requestAnimationFrame(() => {
      dragFrame = 0;
      if (pendingDragX === null) return;
      const x = pendingDragX;
      pendingDragX = null;
      updateDragVisuals(x);
    });
  };

  track.addEventListener('pointerdown', (e) => {
    dragging = true;
    pointerId = e.pointerId;
    track.classList.add('dragging');
    track.classList.remove('animating');
    // Not coalesced: the press must move the thumb in the same tick it
    // happens, or the control feels unresponsive on the very interaction
    // that starts the drag.
    paintedDateKey = null;
    updateDragVisuals(e.clientX);
    e.preventDefault();
  });

  document.addEventListener('pointermove', (e) => {
    if (!dragging || e.pointerId !== pointerId) return;
    scheduleDragVisuals(e.clientX);
  });

  const release = (e) => {
    if (!dragging || (e && e.pointerId !== pointerId)) return;
    dragging = false;
    pointerId = null;
    // SNOW-614: drop any frame still queued — it holds a position from
    // before the release, and commitDate below is about to paint the
    // authoritative one.
    if (dragFrame) {
      cancelAnimationFrame(dragFrame);
      dragFrame = 0;
    }
    pendingDragX = null;
    paintedDateKey = null;
    track.classList.remove('dragging');
    // SNOW-236: use effectiveTodayKey (country-aware last populated date)
    // as the snap target when the user releases without having dragged.
    const snapped = snapToNearestDataDay(liveDate || effectiveTodayKey);
    commitDate(snapped);
    liveDate = null;
  };
  document.addEventListener('pointerup', release);
  document.addEventListener('pointercancel', release);

  // ---- Boot from URL ----
  // Read ?d= once on init. If parseable and inside the season window,
  // commit it (which positions the thumb + queues the repaint once the
  // ratings cache resolves). Otherwise leave the thumb at today's pct.
  const isInSeason = (dateKey) => {
    if (window.pwaScrubberCore) return window.pwaScrubberCore.isInSeason(dateKey, seasonStartMs, seasonEndMs);
    const ms = Date.parse(dateKey);
    return Number.isFinite(ms) && ms >= seasonStartMs && ms <= seasonEndMs;
  };
  const bootDate = readUrlDateParam();
  if (bootDate && isInSeason(bootDate)) {
    // Defer until both the map style and the ratings cache are ready —
    // commitDate calls repaintRegionsForDate which needs MAP and the
    // regions source up. The thumb position can be set immediately so
    // the boot UI is correct even before paint.
    thumb.style.left = dateKeyToPct(bootDate) + '%';
    Promise.all([MAP_READY_PROMISE, getSeasonRatings().catch(() => null)]).then(() => {
      commitDate(bootDate, { silent: true });
    });
  }

  // ---- Browser back/forward ----
  //
  // SNOW-660: a step back onto a URL with no valid ``?d=`` restores the
  // UNCHOSEN state rather than committing effectiveTodayKey. That fallback
  // made back-nav out of a chosen day land on a day the map picked itself —
  // the same invented-date problem the boot path had, reached by a different
  // route. Every region goes to ``no_rating`` (``repaintRegionsForDate``
  // clears the ones the empty frame omits), the thumb returns to today's
  // position, and the ``date: null`` commit tells the ribbon and the
  // groupings boundary to stand down with it.
  window.addEventListener('popstate', () => {
    const d = readUrlDateParam();
    if (d && isInSeason(d)) {
      commitDate(d, { silent: true });
      return;
    }
    if (ratingsCache) repaintRegionsForDate(null, ratingsCache);
    thumb.style.left = todayPct + '%';
    scrubber.setAttribute('aria-valuenow', String(Math.round(todayPct)));
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: null, source: 'scrubber' },
    }));
  });

  // ---- SNOW-792: jump to a date chosen elsewhere ----
  //
  // The calendar popup (static/js/map_calendar.js) picks a day and
  // dispatches it here rather than committing it, so ``commitDate`` above
  // stays the one place a date is applied — repaint, ``?d=`` write and
  // ``snowdesk:date-changed`` all happen exactly as they do for a drag
  // release.
  //
  // ``snowdesk:scrub-to`` is the name the season ribbon's click-to-scrub
  // contract used until SNOW-615 retired it as dead (its cells had stopped
  // carrying click handlers when they moved into the track, leaving the
  // listener with no dispatcher). It is the right name for this, and
  // reviving it is cheaper than inventing a second one that means the same.
  //
  // NOT guarded on the season window. The picker reaches every day up to
  // today, season or not, because the map carries weather as well as
  // bulletins and September has an answer even though no bulletin covers
  // it. An out-of-season date parks the thumb at whichever end of the track
  // it is past — `dateKeyToPct` clamps to [0, 100] — which is the honest
  // reading of "the season ran out", not a mismatch to hide by refusing the
  // date. The ribbon and the choropleth report the same thing: no ratings.
  document.addEventListener('snowdesk:scrub-to', (e) => {
    const dateKey = e.detail && e.detail.date;
    if (!dateKey) return;
    commitDate(dateKey);
  });

  // ---- SNOW-236: Re-derive effective today on country ratings load ----
  // ensureCountryLoaded dispatches this event after merging a new country's
  // ratings into the shared cache. Re-run the effective-last computation so
  // a release without a drag snaps to the correct date for the newly-active
  // country set.
  //
  // SNOW-660: re-derives, and nothing more. It used to commit the new
  // effective date silently whenever the URL carried no ``?d=`` — so
  // toggling a country on could move the visitor's day for them, and with
  // no day chosen it invented one outright.
  document.addEventListener('snowdesk:country-ratings-loaded', () => {
    if (!sortedDates || !ratingsCache) return;
    effectiveTodayKey = deriveEffectiveTodayKey(sortedDates, ratingsCache);
  });

})();
