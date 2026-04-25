/*
 * static/js/map.js — MapLibre client for the /map/ page.
 *
 * Extracted from DO_NOT_ADD/snowdesk_map_preview.html so the same script
 * can later be embedded on the homepage. Endpoint URLs are read from
 * data-* attributes on the #map element — Django renders them through
 * {% url %}, keeping route names as the single source of truth.
 *
 * Data flow at load time:
 *   1. Read endpoint URLs from the #map element's data-* attributes.
 *   2. Fetch regions GeoJSON, today's summaries, and resorts in parallel.
 *   3. Merge the three into per-feature rating state so the fill layer
 *      can colour each region via a MapLibre ``match`` expression.
 *   4. Wire up click + drag-sheet interactions.
 */

// Module-scope handles shared between this file's IIFEs (main init,
// season scrubber, timelapse). Populated by the main IIFE; sibling
// IIFEs read MAP / FEATURE_BY_REGION_ID once the user triggers them.
let MAP = null;
const FEATURE_BY_ID = {};
const FEATURE_BY_REGION_ID = {};

// Resolved by the main IIFE once the MapLibre style has loaded and the
// regions source has been added. Sibling IIFEs that need to call
// setFeatureState during boot (e.g. the scrubber on /map/?d=...) await
// this before painting; user-triggered IIFEs (timelapse) don't need to,
// since the user can't click before the map is up.
let resolveMapReady = null;
const MAP_READY_PROMISE = new Promise((r) => { resolveMapReady = r; });

// Wire-format int → rating string. Inverse of public/api.py::_RATING_TO_INT.
// Hoisted so the timelapse and the scrubber share one definition.
const INT_TO_RATING = ['no_rating', 'low', 'moderate', 'considerable', 'high', 'very_high'];

// Lazily-fetched, cached payload from /api/season-ratings/. Shape:
// { date_iso: { region_id: rating_int } }. Both timelapse (SNOW-46) and
// the scrubber (SNOW-47) consume the same dataset; sharing one fetch
// keeps the payload off the wire twice.
let SEASON_RATINGS_URL = null;
let SEASON_RATINGS_PROMISE = null;

const getSeasonRatings = () => {
  if (SEASON_RATINGS_PROMISE !== null) return SEASON_RATINGS_PROMISE;
  if (!SEASON_RATINGS_URL) {
    return Promise.reject(new Error('season-ratings URL not set'));
  }
  SEASON_RATINGS_PROMISE = fetch(SEASON_RATINGS_URL).then((resp) => {
    if (!resp.ok) throw new Error('season-ratings fetch failed');
    return resp.json();
  });
  return SEASON_RATINGS_PROMISE;
};

// Repaint every known region's choropleth fill via MapLibre feature-state
// for the supplied date. Missing regions in the frame fall back to
// no_rating so colours from a previous frame don't leak through.
const repaintRegionsForDate = (dateKey, cache) => {
  if (!MAP) return;
  const frame = (cache && cache[dateKey]) || {};
  for (const [regionID, feature] of Object.entries(FEATURE_BY_REGION_ID)) {
    const ratingInt = frame[regionID];
    const rating = ratingInt == null ? 'no_rating' : INT_TO_RATING[ratingInt];
    MAP.setFeatureState({ source: 'regions', id: feature.id }, { rating });
  }
};

// Clear per-feature rating state, reverting the choropleth to the
// property-based ``rating`` written at page load (today's bulletins).
const clearRegionRepaint = () => {
  if (!MAP) return;
  for (const feature of Object.values(FEATURE_BY_REGION_ID)) {
    MAP.removeFeatureState({ source: 'regions', id: feature.id }, 'rating');
  }
};

(function () {
  'use strict';

  // Debug mode. Activate with ?debug=1 in the URL, or press 'd' while
  // the page is focused. Exposes region IDs in the drawer and on the map.
  let DEBUG = new URLSearchParams(location.search).has('debug');

  const mapEl = document.getElementById('map');
  const REGIONS_URL   = mapEl.dataset.regionsUrl;
  const SUMMARIES_URL = mapEl.dataset.summariesUrl;
  const RESORTS_URL   = mapEl.dataset.resortsUrl;
  // The summary URL carries the literal placeholder __REGION__ which is
  // substituted with the tapped region's region_id at fetch time. Server
  // renders this via {% url 'api:region_summary' '__REGION__' %} so the
  // route name stays the single source of truth.
  const REGION_SUMMARY_URL_TEMPLATE = mapEl.dataset.regionSummaryUrl;
  const BASEMAP_STYLE = mapEl.dataset.basemapStyle;
  // Hand the season-ratings URL to module scope so the timelapse and
  // scrubber IIFEs (defined further down in this file) can share one
  // fetch via getSeasonRatings().
  SEASON_RATINGS_URL = mapEl.dataset.seasonRatingsUrl;

  const BULLETIN_SUMMARIES = {};
  const RESORTS_BY_REGION  = {};
  const RATINGS            = {};

  const RATING_COLOURS = {
    low:          '#ccff66',
    moderate:     '#ffff00',
    considerable: '#ff9900',
    high:         '#ff0000',
    very_high:    '#a500a5',
    no_rating:    '#e0e0e0',
  };

  // Basemap style JSON URL is rendered server-side from
  // ``settings.BASEMAP_STYLE_URL`` onto the #map element. Default is
  // OpenFreeMap; swapping candidates (Swisstopo winter / light, MapTiler,
  // Mapbox, etc.) is a one-line settings change with no code edit.
  //
  // Initial view is framed via `bounds` around Switzerland rather than a
  // hand-tuned center/zoom pair — `bounds` adapts to viewport aspect
  // ratio automatically, which matters now that SNOW-35 made the map
  // full-bleed (previously the frame was a fixed 390px phone mock).
  const map = new maplibregl.Map({
    container: 'map',
    style: BASEMAP_STYLE,
    bounds: [[5.9, 45.8], [10.5, 47.9]],
    fitBoundsOptions: { padding: 20 },
    minZoom: 5,
    maxZoom: 12,
    maxBounds: [[3.5, 43.5], [13.0, 49.5]],
    attributionControl: { compact: true },
  });
  // Expose for sibling IIFEs (timelapse, season scrubber). FEATURE_BY_ID
  // and FEATURE_BY_REGION_ID are at module scope and get populated below.
  MAP = map;

  // In-memory lookup from numeric feature id -> region properties.
  // Numeric because setFeatureState requires a numeric (or numeric-coerceable) id.
  const REGION_LOOKUP = {};

  map.on('load', async () => {
    // Fetch everything in parallel. The three requests are independent —
    // geometry, bulletin summaries, resort lists — so they can all fly at once.
    const [geojson, summaries, resorts] = await Promise.all([
      fetch(REGIONS_URL).then(r => r.json()),
      fetch(SUMMARIES_URL).then(r => r.json()),
      fetch(RESORTS_URL).then(r => r.json()),
    ]);
    Object.assign(BULLETIN_SUMMARIES, summaries);
    Object.assign(RESORTS_BY_REGION, resorts);
    // Derive RATINGS from summaries — single source of truth for the choropleth.
    for (const [id, s] of Object.entries(summaries)) RATINGS[id] = s.rating;

    // Apply initial debug state (for ?debug=1 URL param).
    if (DEBUG) document.getElementById('debug-pill').style.display = 'block';

    // Assign a numeric id to every feature and build the lookup.
    // MapLibre's feature-state API requires numeric ids; regionID is a string
    // ("CH-4115") so we can't use it directly.
    geojson.features.forEach((f, i) => {
      f.id = i;
      // The API emits the region identifier as properties.id. Normalise to
      // properties.regionID so the rest of the code has a stable name.
      const regionID = f.properties.id;
      f.properties.regionID = regionID;
      f.properties.rating = RATINGS[regionID] || 'no_rating';
      REGION_LOOKUP[i] = f.properties;
      FEATURE_BY_ID[i] = f;
      FEATURE_BY_REGION_ID[regionID] = f;
    });

    map.addSource('regions', { type: 'geojson', data: geojson });

    // Fill layer — the choropleth.
    //
    // Colour resolution prefers a feature-state ``rating`` if one is set
    // (used by the SNOW-46 timelapse to recolour regions per frame
    // without re-uploading the source) and falls back to the
    // property-based ``rating`` written at load time. Removing the
    // feature-state on stop reverts to the property colour, i.e. today's
    // bulletins.
    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions',
      paint: {
        'fill-color': [
          'match',
          ['coalesce', ['feature-state', 'rating'], ['get', 'rating']],
          'low',          RATING_COLOURS.low,
          'moderate',     RATING_COLOURS.moderate,
          'considerable', RATING_COLOURS.considerable,
          'high',         RATING_COLOURS.high,
          'very_high',    RATING_COLOURS.very_high,
          RATING_COLOURS.no_rating,
        ],
        'fill-opacity': 0.55,
      },
    });

    // Outline — thin, darker on the selected region.
    map.addLayer({
      id: 'regions-line',
      type: 'line',
      source: 'regions',
      paint: {
        'line-color': [
          'case',
          ['boolean', ['feature-state', 'selected'], false], '#0c447c',
          'rgba(0,0,0,0.25)',
        ],
        'line-width': [
          'case',
          ['boolean', ['feature-state', 'selected'], false], 2,
          0.6,
        ],
      },
    });

    // Labels — only from zoom 8.5 up, to avoid clutter at country view.
    map.addLayer({
      id: 'regions-label',
      type: 'symbol',
      source: 'regions',
      minzoom: 8.5,
      layout: {
        'text-field': ['get', 'name'],
        'text-font': ['Noto Sans Regular'],
        'text-size': 11,
        'text-allow-overlap': false,
      },
      paint: {
        'text-color': '#2a2a2a',
        'text-halo-color': 'rgba(255,255,255,0.85)',
        'text-halo-width': 1.2,
      },
    });

    // Interaction
    let selectedId = null;
    // Tracks the most recent inflight summary fetch so a slow tap-A
    // followed by a fast tap-B never lets A's response overwrite B's.
    let summarySeq = 0;

    const sheet = document.getElementById('sheet');
    const sheetPeek = document.getElementById('sheet-peek');
    const sheetExpanded = document.getElementById('sheet-expanded');

    // ---- Snap-state helpers (SNOW-43) ----
    //
    // The sheet has three states managed via ``data-snap`` on #sheet:
    //   no attribute  → dismissed
    //   "peek"        → peek partial + a small tease of the expanded body
    //   "expanded"    → full body visible
    //
    // ``--sheet-expanded-height`` is read from CSS once at init and is
    // assumed to be in ``vh`` units. ``--sheet-peek-height`` is set
    // dynamically per region (after the peek partial is measured) so the
    // peek snap matches the actual rendered content height plus a small
    // tease — a slice of the first hazard block deliberately bleeds into
    // the visible area so users see there's content to swipe up to.

    const _rootStyle = getComputedStyle(document.documentElement);
    const SHEET_EXPANDED_VH = parseFloat(
      _rootStyle.getPropertyValue('--sheet-expanded-height'),
    ) || 80;

    // Pixels of expanded-body content allowed to show through the peek
    // visible area — the "peek at more below" affordance. Tweakable.
    const PEEK_TEASE_PX = 56;

    const expandedHeightPx = () => SHEET_EXPANDED_VH * window.innerHeight / 100;

    // Read the *current* peek visible height from CSS, in pixels. The
    // peek-height variable is overridden inline on #sheet by setSnap
    // once content is measured; before that it falls back to the CSS
    // root default.
    const peekHeightPx = () => {
      const cs = getComputedStyle(sheet);
      const raw = cs.getPropertyValue('--sheet-peek-height').trim();
      if (raw.endsWith('px')) return parseFloat(raw);
      if (raw.endsWith('vh')) return parseFloat(raw) * window.innerHeight / 100;
      return parseFloat(raw) || expandedHeightPx() * 0.3;
    };

    // translateY pixel value for each snap state — used as the start
    // baseline for drags and the target for release animations.
    const snapY = (state) => {
      if (state === 'expanded') return 0;
      if (state === 'peek') return expandedHeightPx() - peekHeightPx();
      return expandedHeightPx();
    };

    // Apply a snap state to the sheet: clears any inline transform so
    // CSS takes back control, and writes the ``data-snap`` attribute
    // that drives the CSS transform selector. When entering peek state,
    // the peek-height custom property is updated to fit the actual
    // rendered peek content + a tease of the expanded body so the
    // visible cutoff lands inside real content rather than dead white.
    const setSnap = (state) => {
      if (state === 'peek') {
        const peekContentH = sheetPeek.offsetHeight;
        // sheet-body-wrap has its own padding; include it so the peek
        // partial isn't visually flush against the cutoff line.
        const bodyWrapPad = parseFloat(
          getComputedStyle(bodyWrap).paddingTop,
        ) || 0;
        const total = bodyWrapPad + peekContentH + PEEK_TEASE_PX;
        sheet.style.setProperty('--sheet-peek-height', `${total}px`);
      }
      sheet.style.transform = '';
      sheet.dataset.snap = state;
    };

    // Canonical SLF region-ID shape (e.g. "CH-4115"). Anything else is rejected
    // before it reaches the CTA href to prevent a malformed GeoJSON payload
    // turning into an open-redirect / javascript: URL on the client.
    const REGION_ID_RE = /^[A-Za-z]{2}-[A-Za-z0-9]+$/;

    // Fetch + inject the server-rendered peek + expanded HTML for a region.
    // Returns true on success, false on 404 / network error. The summarySeq
    // guard discards stale responses if the user has tapped a different
    // region while this fetch was in flight.
    // ``dateKey`` (optional, ``YYYY-MM-DD``) selects the bulletin for a
    // specific past or future date — passed through as ``?d=`` to the
    // region-summary API and used by the season scrubber to refresh the
    // open sheet when the displayed date changes (SNOW-47). Omit to get
    // today's bulletin, the default.
    const loadRegionSummary = async (regionID, dateKey) => {
      if (!REGION_ID_RE.test(regionID)) return false;
      let url = REGION_SUMMARY_URL_TEMPLATE.replace(
        '__REGION__', encodeURIComponent(regionID),
      );
      if (dateKey) {
        url += (url.includes('?') ? '&' : '?') + 'd=' + encodeURIComponent(dateKey);
      }
      const seq = ++summarySeq;
      try {
        const resp = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (seq !== summarySeq) return false;  // a newer tap won the race
        if (!resp.ok) return false;
        const data = await resp.json();
        if (seq !== summarySeq) return false;
        // Server-trusted HTML: rendered by the same Django templates that
        // render the rest of the site, with all user-supplied values escaped
        // by Django's autoescape — safe to assign as innerHTML.
        sheetPeek.innerHTML = data.peek || '';
        sheetExpanded.innerHTML = data.expanded || '';
        return true;
      } catch (_err) {
        return false;
      }
    };

    const clearSelection = () => {
      if (selectedId !== null) {
        map.setFeatureState({ source: 'regions', id: selectedId }, { selected: false });
        selectedId = null;
      }
      summarySeq++;  // invalidate any inflight fetch so it can't reopen the sheet
      sheet.style.transform = '';
      sheet.style.removeProperty('--sheet-peek-height');
      delete sheet.dataset.snap;
      sheetPeek.replaceChildren();
      sheetExpanded.replaceChildren();
    };

    // Compute the lng/lat bounding box of a GeoJSON Polygon or MultiPolygon.
    // MapLibre's fitBounds takes [[west, south], [east, north]].
    const featureBBox = (feature) => {
      const coords = feature.geometry.type === 'Polygon'
        ? feature.geometry.coordinates
        : feature.geometry.coordinates.flat();  // MultiPolygon → concat rings
      let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
      for (const ring of coords) {
        for (const [lng, lat] of ring) {
          if (lng < w) w = lng;
          if (lng > e) e = lng;
          if (lat < s) s = lat;
          if (lat > n) n = lat;
        }
      }
      return [[w, s], [e, n]];
    };

    // Pan/zoom so the region sits in the visible portion of the map.
    // The sheet is always rendered at full ``--sheet-expanded-height``
    // but only ``peekHeightPx`` is actually visible after a tap, so we
    // reserve the visible portion (not the full sheet height) as bottom
    // padding for fitBounds.
    const panToRegionAboveSheet = (feature) => {
      const bbox = featureBBox(feature);
      const visible = sheet.dataset.snap === 'expanded'
        ? expandedHeightPx()
        : sheet.dataset.snap === 'peek'
          ? peekHeightPx()
          : 0;
      map.fitBounds(bbox, {
        padding: { top: 60, right: 40, bottom: visible + 40, left: 40 },
        maxZoom: 10,   // don't zoom in past neighbourhood detail even for tiny regions
        duration: 400,
      });
    };

    // Re-usable selection logic. Both the map click handler and the search
    // dropdown route through this so "make this region the active one" has
    // a single definition. ``toggle`` mirrors the map-click UX where a
    // second click on the already-selected region dismisses the sheet;
    // search callers pass ``toggle: false`` so selecting a result always
    // opens it, never toggles it off.
    const selectFeature = async (numericId, { toggle = true } = {}) => {
      if (numericId === selectedId) {
        if (toggle) clearSelection();
        return;
      }
      if (selectedId !== null) {
        map.setFeatureState({ source: 'regions', id: selectedId }, { selected: false });
      }
      selectedId = numericId;
      map.setFeatureState({ source: 'regions', id: selectedId }, { selected: true });

      const props = REGION_LOOKUP[numericId];
      const ok = await loadRegionSummary(props.regionID);
      // If the user dismissed the sheet (or selected a different region)
      // while the fetch was in flight, selectedId may no longer match.
      // loadRegionSummary already discards stale data; here we just bail
      // out without snapping the sheet open.
      if (selectedId !== numericId) return;
      if (!ok) {
        // No bulletin (404) or network failure — leave sheet closed.
        clearSelection();
        return;
      }
      setSnap('peek');

      // One frame after setSnap so getBoundingClientRect reflects the
      // new visible height (the CSS transform hasn't finished animating
      // but the snap state is set, so panToRegionAboveSheet uses the
      // correct visible-height value).
      const feature = FEATURE_BY_ID[numericId];
      requestAnimationFrame(() => panToRegionAboveSheet(feature));
    };

    map.on('click', 'regions-fill', (e) => {
      if (!e.features.length) return;
      selectFeature(e.features[0].id);
    });

    // Dismiss sheet on map tap outside a region
    map.on('click', (e) => {
      const hits = map.queryRenderedFeatures(e.point, { layers: ['regions-fill'] });
      if (!hits.length) clearSelection();
    });

    map.on('mouseenter', 'regions-fill', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'regions-fill', () => { map.getCanvas().style.cursor = ''; });

    // Sheet dismissal: close button, Esc key, and a drag gesture that can
    // start anywhere on the sheet (handle OR body).

    const bodyWrap = document.querySelector('.sheet-body-wrap');
    const sheetGrab = document.getElementById('sheet-grab');
    const closeBtn = document.getElementById('sheet-close');

    closeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      clearSelection();
    });

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && sheet.dataset.snap) clearSelection();
      // Toggle debug mode; ignore when typing in an input/textarea.
      if (e.key === 'd' && !e.target.matches('input, textarea')) {
        DEBUG = !DEBUG;
        sheet.classList.toggle('debug', DEBUG);
        const pill = document.getElementById('debug-pill');
        if (pill) pill.style.display = DEBUG ? 'block' : 'none';
      }
    });

    // --- Drag controller (three-position snap machine, SNOW-43) ---
    //
    // The sheet has three resting positions: dismissed, peek, expanded.
    // Each gesture starts from the current snap state's translateY
    // baseline; on release we pick the nearest of the three states based
    // on drag distance + flick velocity.
    //
    // Design goals:
    //   1. Drag can begin anywhere on the sheet, including over body text.
    //   2. Gesture is "claimed" only after enough vertical movement — small taps
    //      on buttons/links inside the sheet never accidentally start a drag.
    //   3. Respects inner scroll: if the body is scrolled down, downward drag
    //      scrolls the body instead of dragging the sheet. Only when scrollTop
    //      is 0 AND the gesture is downward do we take over.
    //   4. Upward drags past the expanded state get rubber-band resistance.
    //   5. Release animation is driven by JS, not CSS, so speed matches the
    //      flick velocity — a hard flick completes fast, a gentle let-go settles
    //      smoothly.
    //   6. A non-claimed pointerup on the grab zone counts as a tap and
    //      expands a peek-state sheet (does nothing when already expanded).

    const GESTURE_CLAIM_PX = 6;            // pixels of vertical movement before we claim the gesture
    const PEEK_DISMISS_VELOCITY_PX_MS = 0.6;  // downward flick from peek that always dismisses
    const EXPAND_VELOCITY_PX_MS = 0.6;     // upward flick from peek that always expands
    const COLLAPSE_VELOCITY_PX_MS = 0.4;   // gentle downward flick from expanded → peek
    const RUBBER_BAND_DIVISOR = 4;         // upward drag resistance past expanded (higher = stiffer)
    const VELOCITY_SAMPLE_WINDOW_MS = 60;  // only the last N ms of samples count for release velocity
    const MIN_ANIM_DURATION_MS = 120;      // animation clamp — below this feels twitchy
    const MAX_ANIM_DURATION_MS = 400;      // above this feels sluggish

    let drag = null;
    let animFrame = null;

    const pointerDown = (e) => {
      if (!sheet.dataset.snap) return;
      // Don't start a drag on the close button — let its click handler fire.
      if (e.target.closest('.sheet-close')) return;
      // Don't start a drag on the CTA link — taps on it should navigate, not drag.
      if (e.target.closest('.sheet-cta')) return;

      // Cancel any in-flight release animation so the user can grab mid-animation.
      if (animFrame !== null) {
        cancelAnimationFrame(animFrame);
        animFrame = null;
        sheet.classList.remove('animating');
      }

      drag = {
        startY: e.clientY,
        currentY: e.clientY,
        offset: 0,
        baselineY: snapY(sheet.dataset.snap),
        startSnap: sheet.dataset.snap,
        startedInBody: bodyWrap.contains(e.target),
        startedInGrab: sheetGrab.contains(e.target),
        bodyScrollAtStart: bodyWrap.scrollTop,
        samples: [{ t: performance.now(), y: e.clientY }],
        pointerId: e.pointerId,
        claimed: false,  // becomes true once we commit to a vertical drag
      };
    };

    const pointerMove = (e) => {
      if (!drag || e.pointerId !== drag.pointerId) return;

      drag.currentY = e.clientY;
      let delta = drag.currentY - drag.startY;

      // Phase 1: gesture not yet claimed. Decide whether to take over.
      if (!drag.claimed) {
        if (Math.abs(delta) < GESTURE_CLAIM_PX) return;  // not enough movement yet

        // Body-scroll deference (only relevant in the expanded state, where the
        // body actually has scrollable content): if the gesture started inside
        // the scrollable body AND the body was scrolled down AND the gesture is
        // downward, let the body scroll instead of dragging the sheet.
        if (drag.startSnap === 'expanded'
            && drag.startedInBody
            && drag.bodyScrollAtStart > 0
            && delta > 0) {
          drag = null;
          return;
        }
        // Likewise, an upward gesture in a scrollable body in the expanded
        // state means scroll content, not lift the sheet (it's already up).
        if (drag.startSnap === 'expanded' && drag.startedInBody && delta < 0) {
          drag = null;
          return;
        }

        // Commit: we're dragging the sheet.
        drag.claimed = true;
        sheet.classList.add('dragging');
        sheet.setPointerCapture(e.pointerId);
      }

      // Phase 2: claimed. The transform value is baseline + delta, with
      // rubber-band resistance applied if the user is dragging *above* the
      // expanded state (currentY < 0). The peek-state baseline is positive,
      // so dragging upward from peek toward expanded is unrestricted — the
      // rubber-band only kicks in past the expanded snap.
      const intendedY = drag.baselineY + delta;
      let renderedY = intendedY;
      if (intendedY < 0) {
        renderedY = intendedY / RUBBER_BAND_DIVISOR;
      }
      drag.offset = delta;
      sheet.style.transform = `translateY(${renderedY}px)`;

      // Velocity samples — pruned to the recent window.
      const now = performance.now();
      drag.samples.push({ t: now, y: e.clientY });
      while (drag.samples.length > 1 && now - drag.samples[0].t > VELOCITY_SAMPLE_WINDOW_MS) {
        drag.samples.shift();
      }

      // Prevent the browser from also scrolling while we drag.
      e.preventDefault();
    };

    const pointerUp = (e) => {
      if (!drag || e.pointerId !== drag.pointerId) return;

      // Tap (not a drag) on the grab zone → expand a peek-state sheet.
      // Tap when expanded is intentionally a no-op (see SNOW-43 plan
      // open question; easy to flip later if it feels wrong).
      if (!drag.claimed) {
        const wasGrabTap = drag.startedInGrab
          && !e.target.closest('.sheet-close');
        const fromState = drag.startSnap;
        drag = null;
        if (wasGrabTap && fromState === 'peek') {
          animateToSnap(snapY('peek'), 'expanded', 0);
        }
        return;
      }

      const offset = drag.offset;
      const fromState = drag.startSnap;
      const fromY = drag.baselineY + offset;

      const first = drag.samples[0];
      const last = drag.samples[drag.samples.length - 1];
      const dt = last.t - first.t;
      const velocity = dt > 0 ? (last.y - first.y) / dt : 0;  // px per ms, + = downward

      if (sheet.hasPointerCapture(drag.pointerId)) {
        sheet.releasePointerCapture(drag.pointerId);
      }
      sheet.classList.remove('dragging');
      drag = null;

      const peekH = peekHeightPx();
      const expandedH = expandedHeightPx();

      let target;
      if (fromState === 'peek') {
        if (offset > peekH * 0.5 || velocity > PEEK_DISMISS_VELOCITY_PX_MS) {
          target = 'dismissed';
        } else if (offset < -peekH * 0.5 || velocity < -EXPAND_VELOCITY_PX_MS) {
          target = 'expanded';
        } else {
          target = 'peek';
        }
      } else {  // expanded
        // Halfway between expanded and dismissed (i.e. dragged past the peek
        // threshold) OR a hard downward flick → dismiss outright.
        if (offset > (expandedH - peekH) + peekH * 0.5
            || velocity > PEEK_DISMISS_VELOCITY_PX_MS) {
          target = 'dismissed';
        } else if (offset > peekH * 0.5 || velocity > COLLAPSE_VELOCITY_PX_MS) {
          target = 'peek';
        } else {
          target = 'expanded';
        }
      }

      animateToSnap(fromY, target, velocity);
    };

    // Animate from the current pixel transform value to the target snap
    // state. Reuses ``animateTransform`` for the actual rAF loop; this
    // wrapper just picks the duration and the settle callback.
    const animateToSnap = (fromY, targetState, velocity) => {
      const toY = snapY(targetState);
      const distance = Math.abs(toY - fromY);
      // If the release had momentum, use it; otherwise fall back to a median speed.
      const effectiveVelocity = Math.max(Math.abs(velocity), 0.5);  // px/ms floor
      const rawDuration = distance / effectiveVelocity;
      const duration = Math.max(
        MIN_ANIM_DURATION_MS,
        Math.min(MAX_ANIM_DURATION_MS, rawDuration),
      );

      animateTransform(fromY, toY, duration, () => {
        sheet.classList.remove('animating');
        if (targetState === 'dismissed') {
          clearSelection();
        } else {
          setSnap(targetState);
        }
      });
    };

    // Generic transform animator. Uses a cubic ease-out so motion decelerates
    // as it approaches the target — matches iOS bottom-sheet feel.
    const animateTransform = (fromPx, toPx, durationMs, onDone) => {
      sheet.classList.add('animating');
      const start = performance.now();
      const delta = toPx - fromPx;

      const step = (now) => {
        const t = Math.min(1, (now - start) / durationMs);
        // easeOutCubic
        const eased = 1 - Math.pow(1 - t, 3);
        const value = fromPx + delta * eased;
        sheet.style.transform = `translateY(${value}px)`;

        if (t < 1) {
          animFrame = requestAnimationFrame(step);
        } else {
          animFrame = null;
          onDone();
        }
      };
      animFrame = requestAnimationFrame(step);
    };

    // Attach listeners to the whole sheet, not just the grab zone.
    sheet.addEventListener('pointerdown', pointerDown);
    sheet.addEventListener('pointermove', pointerMove);
    sheet.addEventListener('pointerup', pointerUp);
    sheet.addEventListener('pointercancel', pointerUp);

    // --- Search ---
    //
    // In-memory autocomplete over region names + resort names. All data
    // is already resident after the initial load, so search is purely
    // local — no server round-trip per keystroke, no indexing cost worth
    // worrying about (a few hundred entries total).

    const MAX_RESULTS = 8;

    // NFD-decompose and strip combining marks so "Évolène" matches "evolene",
    // "Graubünden" matches "graubunden", etc.
    const normalise = (s) => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');

    const SEARCH_INDEX = [];

    // One entry per region (matchable by display name or SLF region_id).
    for (const props of Object.values(REGION_LOOKUP)) {
      const regionID = props.regionID;
      const name = props.name || regionID;
      SEARCH_INDEX.push({
        type: 'region',
        primary: name,
        secondary: regionID,
        regionID,
        searchable: normalise(`${name} ${regionID}`),
      });
    }
    // One entry per resort, pointing back to its parent region. The
    // secondary label carries the region name so users see context when
    // several resorts share a first word.
    for (const [regionID, resorts] of Object.entries(RESORTS_BY_REGION)) {
      const feature = FEATURE_BY_REGION_ID[regionID];
      if (!feature) continue;
      const regionName = feature.properties.name || regionID;
      for (const resort of resorts) {
        SEARCH_INDEX.push({
          type: 'resort',
          primary: resort,
          secondary: regionName,
          regionID,
          searchable: normalise(`${resort} ${regionName}`),
        });
      }
    }

    // Ranking: prefix matches on the primary label sort above substring
    // matches; ties break alphabetically. Cap at MAX_RESULTS so the
    // dropdown stays usable on narrow viewports.
    const runSearch = (query) => {
      const q = normalise(query).trim();
      if (!q) return [];
      const hits = [];
      for (const item of SEARCH_INDEX) {
        const idx = item.searchable.indexOf(q);
        if (idx === -1) continue;
        const primaryIdx = normalise(item.primary).indexOf(q);
        const score = primaryIdx === 0 ? 0 : primaryIdx > 0 ? 1 : 2;
        hits.push({ item, score, pos: idx });
      }
      hits.sort((a, b) =>
        a.score - b.score ||
        a.pos - b.pos ||
        a.item.primary.localeCompare(b.item.primary),
      );
      return hits.slice(0, MAX_RESULTS).map(h => h.item);
    };

    const inputEl = document.getElementById('search-input');
    const resultsEl = document.getElementById('search-results');
    const pillEl = document.getElementById('search-pill');
    const toggleEl = document.getElementById('search-toggle');
    let currentResults = [];
    let activeIdx = -1;

    // Pill expansion — the collapsed default shows only the icon toggle.
    // Tapping it switches the pill into the expanded state, which reveals
    // the input (CSS transition) and moves focus. The pill stays expanded
    // as long as the user is interacting with it; Escape or an outside
    // pointerdown collapses it back (see handlers below).
    const openSearch = () => {
      pillEl.setAttribute('data-state', 'expanded');
      toggleEl.setAttribute('aria-expanded', 'true');
      // Defer focus one frame so the width transition starts before the
      // caret appears — avoids a flash of the input at width 0.
      window.requestAnimationFrame(() => inputEl.focus());
      if (inputEl.value) renderResults(runSearch(inputEl.value));
    };

    const collapseSearch = () => {
      pillEl.setAttribute('data-state', 'collapsed');
      toggleEl.setAttribute('aria-expanded', 'false');
      closeResults();
      inputEl.blur();
    };

    toggleEl.addEventListener('click', (e) => {
      // When already expanded, the toggle is just the leading icon of a
      // live search input — swallow the click so users don't accidentally
      // collapse mid-query. Escape or an outside pointerdown is the
      // deliberate collapse path.
      if (pillEl.getAttribute('data-state') === 'expanded') return;
      e.preventDefault();
      openSearch();
    });

    const closeResults = () => {
      resultsEl.hidden = true;
      inputEl.setAttribute('aria-expanded', 'false');
      inputEl.removeAttribute('aria-activedescendant');
      activeIdx = -1;
    };

    const setActive = (idx) => {
      const items = resultsEl.children;
      if (activeIdx >= 0 && items[activeIdx]) items[activeIdx].classList.remove('active');
      activeIdx = idx;
      if (idx >= 0 && items[idx]) {
        items[idx].classList.add('active');
        inputEl.setAttribute('aria-activedescendant', items[idx].id);
        items[idx].scrollIntoView({ block: 'nearest' });
      } else {
        inputEl.removeAttribute('aria-activedescendant');
      }
    };

    const renderResults = (results) => {
      resultsEl.replaceChildren();
      currentResults = results;
      activeIdx = -1;
      if (results.length === 0) {
        closeResults();
        return;
      }
      results.forEach((r, i) => {
        const li = document.createElement('li');
        li.className = 'search-result';
        li.setAttribute('role', 'option');
        li.id = `search-result-${i}`;

        // Text column (primary/secondary) and a type badge side by side.
        // The badge disambiguates region hits from resort hits, which
        // otherwise render identically when a resort shares its name
        // with its region (e.g. "Davos" in the Davos region).
        const text = document.createElement('div');
        text.className = 'search-result-text';
        const primary = document.createElement('div');
        primary.className = 'search-result-primary';
        primary.textContent = r.primary;
        const secondary = document.createElement('div');
        secondary.className = 'search-result-secondary';
        secondary.textContent = r.secondary;
        text.append(primary, secondary);

        const badge = document.createElement('span');
        badge.className = `search-result-badge search-result-badge--${r.type}`;
        // i18n: translatable — search result type badges
        badge.textContent = r.type === 'region' ? 'Region' : 'Resort';

        li.append(text, badge);
        // Use pointerdown rather than click so we act before the input's
        // blur handler closes the dropdown. pointerdown covers both mouse
        // and touch — mousedown alone is unreliable on iOS Safari, where
        // the synthesised mousedown after touchend can be skipped.
        li.addEventListener('pointerdown', (e) => {
          e.preventDefault();
          chooseResult(r);
        });
        resultsEl.append(li);
      });
      resultsEl.hidden = false;
      inputEl.setAttribute('aria-expanded', 'true');
    };

    const chooseResult = (item) => {
      const feature = FEATURE_BY_REGION_ID[item.regionID];
      if (!feature) return;
      inputEl.value = item.primary;
      collapseSearch();
      // Force a fresh open even if the region is already the selected one —
      // the user clearly wants to see it, not toggle it off.
      selectFeature(feature.id, { toggle: false });
    };

    inputEl.addEventListener('input', () => {
      renderResults(runSearch(inputEl.value));
    });

    inputEl.addEventListener('focus', () => {
      if (inputEl.value) renderResults(runSearch(inputEl.value));
    });

    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') {
        if (!currentResults.length) return;
        e.preventDefault();
        setActive(Math.min(activeIdx + 1, currentResults.length - 1));
      } else if (e.key === 'ArrowUp') {
        if (!currentResults.length) return;
        e.preventDefault();
        // Allow ArrowUp past index 0 back to -1, returning focus to the
        // free-typed state (ARIA APG combobox pattern — keyboard users
        // must not get trapped inside the list).
        setActive(Math.max(activeIdx - 1, -1));
      } else if (e.key === 'Enter') {
        const pick = activeIdx >= 0 ? currentResults[activeIdx] : currentResults[0];
        if (pick) {
          e.preventDefault();
          chooseResult(pick);
        }
      } else if (e.key === 'Escape') {
        if (inputEl.value) {
          inputEl.value = '';
          closeResults();
        } else {
          collapseSearch();
        }
      }
    });

    // Collapse the pill and close the dropdown on outside pointer
    // interaction. Use pointerdown (before focus changes) and ignore
    // clicks inside the pill itself or the results list so li and
    // toggle handlers still fire.
    document.addEventListener('pointerdown', (e) => {
      if (pillEl.contains(e.target)) return;
      if (resultsEl.contains(e.target)) return;
      collapseSearch();
    });

    // SNOW-47: when the season scrubber (or any other consumer) commits
    // a new displayed date, refresh the open sheet so the user sees that
    // date's bulletin for the currently-selected region. ``selectedId``
    // and ``REGION_LOOKUP`` are closures over the main IIFE; the
    // scrubber doesn't see them, so the bridge is an event.
    document.addEventListener('snowdesk:date-changed', (e) => {
      if (selectedId === null) return;
      const props = REGION_LOOKUP[selectedId];
      if (!props) return;
      loadRegionSummary(props.regionID, e.detail && e.detail.date);
    });

    // Signal to sibling IIFEs (scrubber) that the map style + regions
    // source are ready and setFeatureState calls will now stick. The
    // scrubber awaits this before painting the boot-time ?d= state.
    if (resolveMapReady) resolveMapReady();
  });
})();

// SNOW-47: Season-scrubber wires. Drag the thumb → release commits a
// date. The map repaints region colours for that date, the open sheet
// (if any) refreshes via the region-summary API, and the URL gets a
// ``?d=YYYY-MM-DD`` so the page is linkable. Loading ``/map/?d=…`` on
// page boot drops the thumb on that date.
//
// The scrubber owns no data of its own — it consumes the same
// season-ratings payload as the timelapse via getSeasonRatings(), and
// announces date commits via the ``snowdesk:date-changed`` CustomEvent
// so the main IIFE (sheet) and the timelapse IIFE (stop on grab) can
// react without seeing each other.
(function seasonScrubberInit() {
  const scrubber = document.getElementById('season-scrubber');
  if (!scrubber) return;
  const track = scrubber.querySelector('.season-scrubber-track');
  const thumb = scrubber.querySelector('.season-scrubber-thumb');
  const datePill = scrubber.querySelector('.season-scrubber-date-pill');
  const selectedBound = scrubber.querySelector('.season-scrubber-bound-selected');
  const todayKey = scrubber.dataset.today;
  const todayPct = parseFloat(scrubber.dataset.todayPct);
  const seasonStartMs = Date.parse(scrubber.dataset.seasonStart);
  const seasonEndMs = Date.parse(scrubber.dataset.seasonEnd);
  const seasonSpanMs = seasonEndMs - seasonStartMs;

  // Format an ISO date as "Apr 24 2026" — month-name month-number is
  // locale-friendly and unambiguous (avoids the 04/05 day-vs-month
  // confusion of all-numeric formats).
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const formatDateLong = (dateKey) => {
    const [y, m, d] = dateKey.split('-');
    return `${MONTHS[parseInt(m, 10) - 1]} ${parseInt(d, 10)} ${y}`;
  };

  // Convert between a thumb percentage (0..100 along the track) and an
  // ISO date string. Both use the season bounds parsed above and round
  // to the nearest day — the scrubber is intentionally single-day
  // resolution (intraday is a future ticket).
  const pctToDateKey = (pct) => {
    const ms = seasonStartMs + (pct / 100) * seasonSpanMs;
    const day = new Date(ms);
    // Snap to UTC midnight to dodge DST edges, then format.
    const y = day.getUTCFullYear();
    const m = String(day.getUTCMonth() + 1).padStart(2, '0');
    const d = String(day.getUTCDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  };
  const dateKeyToPct = (dateKey) => {
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
  getSeasonRatings().then((data) => {
    ratingsCache = data;
    sortedDates = Object.keys(data).sort();
  }).catch(() => { /* network fail → leave snap disabled, drag still works */ });

  const snapToNearestDataDay = (dateKey) => {
    if (!sortedDates || sortedDates.length === 0) return dateKey;
    let best = sortedDates[0];
    let bestDelta = Math.abs(Date.parse(best) - Date.parse(dateKey));
    for (const d of sortedDates) {
      const delta = Math.abs(Date.parse(d) - Date.parse(dateKey));
      if (delta < bestDelta) { best = d; bestDelta = delta; }
    }
    return best;
  };

  // Tracks the date the user has committed to — drives URL state, the
  // selected-bound label, and event payloads. ``null`` means "showing
  // today, no time-travel"; this stays distinct from ``todayKey`` so the
  // selected-bound chrome only appears when the user has explicitly
  // scrubbed off today.
  let currentDate = null;

  const renderSelectedBound = () => {
    if (!selectedBound) return;
    if (currentDate && currentDate !== todayKey) {
      selectedBound.textContent = formatDateLong(currentDate);
      scrubber.dataset.hasSelection = 'true';
    } else {
      selectedBound.textContent = '';
      delete scrubber.dataset.hasSelection;
    }
  };

  // The single commit point. Updates the thumb, repaints regions, syncs
  // the URL, and notifies the rest of the page. ``opts.silent`` skips
  // the URL write — used by the popstate handler so re-applying a
  // browser-back-restored ``?d=`` doesn't re-write history.
  const commitDate = (dateKey, opts = {}) => {
    const isToday = dateKey === todayKey;
    currentDate = isToday ? null : dateKey;
    const pct = dateKeyToPct(dateKey);
    thumb.style.left = pct + '%';
    scrubber.setAttribute('aria-valuenow', String(Math.round(pct)));
    if (ratingsCache) repaintRegionsForDate(dateKey, ratingsCache);
    renderSelectedBound();
    if (!opts.silent) {
      // ``replaceState`` (never push) so a long scrub doesn't bury the
      // back button under dozens of intermediate dates. Today clears the
      // ``?d=`` param entirely, matching the canonical /map/ URL.
      const search = isToday ? '' : '?d=' + dateKey;
      history.replaceState(null, '', '/map/' + search + location.hash);
    }
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: dateKey, source: 'scrubber' },
    }));
  };

  // ---- Pointer drag ----
  let dragging = false;
  let pointerId = null;
  let liveDate = null;  // tracked during drag, used by the pill overlay

  const updateDragVisuals = (clientX) => {
    const rect = track.getBoundingClientRect();
    const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
    thumb.style.left = pct + '%';
    liveDate = pctToDateKey(pct);
    if (datePill) datePill.textContent = formatDateLong(liveDate);
  };

  thumb.addEventListener('pointerdown', (e) => {
    dragging = true;
    pointerId = e.pointerId;
    scrubber.classList.add('dragging');
    track.classList.add('dragging');
    track.classList.remove('animating');
    updateDragVisuals(e.clientX);
    e.preventDefault();
  });

  document.addEventListener('pointermove', (e) => {
    if (!dragging || e.pointerId !== pointerId) return;
    updateDragVisuals(e.clientX);
  });

  const release = (e) => {
    if (!dragging || (e && e.pointerId !== pointerId)) return;
    dragging = false;
    pointerId = null;
    scrubber.classList.remove('dragging');
    track.classList.remove('dragging');
    const snapped = snapToNearestDataDay(liveDate || todayKey);
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
    const ms = Date.parse(dateKey);
    return Number.isFinite(ms) && ms >= seasonStartMs && ms <= seasonEndMs;
  };
  const bootDate = new URL(location.href).searchParams.get('d');
  if (bootDate && /^\d{4}-\d{2}-\d{2}$/.test(bootDate) && isInSeason(bootDate)) {
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
  window.addEventListener('popstate', () => {
    const d = new URL(location.href).searchParams.get('d');
    const target = d && /^\d{4}-\d{2}-\d{2}$/.test(d) && isInSeason(d) ? d : todayKey;
    commitDate(target, { silent: true });
  });
})();

// SNOW-38: Collapsible danger-scale legend. State persists in localStorage
// under `snowdesk.map.legend` (namespaced — distinct from the legacy flat
// `offline-map-saved` key, which is intentionally left as-is).
(function legendInit() {
  const root = document.getElementById('map-legend');
  if (!root) return;
  const toggle = document.getElementById('map-legend-toggle');
  const STORAGE_KEY = 'snowdesk.map.legend';

  function applyState(state) {
    const next = state === 'expanded' ? 'expanded' : 'collapsed';
    root.dataset.state = next;
    toggle.setAttribute('aria-expanded', next === 'expanded' ? 'true' : 'false');
  }

  let initial = 'collapsed';
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'expanded') initial = 'expanded';
  } catch (_) { /* private mode / disabled storage — fall through */ }
  applyState(initial);

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const next = root.dataset.state === 'expanded' ? 'collapsed' : 'expanded';
    applyState(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch (_) {}
  });

  // Outside-tap dismiss: any click outside the legend container collapses
  // it. Inside-card clicks bubble harmlessly; the toggle stops propagation
  // above so its own click is not treated as "outside".
  document.addEventListener('click', (e) => {
    if (root.dataset.state !== 'expanded') return;
    if (root.contains(e.target)) return;
    applyState('collapsed');
    try { localStorage.setItem(STORAGE_KEY, 'collapsed'); } catch (_) {}
  });
})();

// SNOW-46: Timelapse debug button. Rendered inside #debug-pill (which is
// itself hidden until the user presses 'd'); a click iterates through
// every date in the dataset, repainting region colours via feature-state
// at ~10 fps with a top-centre date overlay. A second click stops and
// reverts to today's bulletins by clearing the per-region feature-state.
(function timelapseInit() {
  const button = document.getElementById('timelapse-toggle');
  if (!button) return;

  const mapEl = document.getElementById('map');
  const overlay = document.getElementById('timelapse-date');
  const FRAME_MS = 100;  // 10 fps

  // Season-scrubber sync — drive the existing thumb so the operator
  // sees the playback position on the same control they will eventually
  // use to drag-scrub. The bounds and today-snap come from the same
  // data-* attributes the season-scrubber IIFE reads.
  const scrubber = document.getElementById('season-scrubber');
  const scrubberThumb = scrubber ? scrubber.querySelector('.season-scrubber-thumb') : null;
  const seasonStartMs = scrubber ? Date.parse(scrubber.dataset.seasonStart) : NaN;
  const seasonEndMs = scrubber ? Date.parse(scrubber.dataset.seasonEnd) : NaN;
  const todayPct = scrubber ? parseFloat(scrubber.dataset.todayPct) : NaN;
  const seasonSpanMs = seasonEndMs - seasonStartMs;

  const moveScrubber = (dateKey) => {
    if (!scrubberThumb || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) return;
    const dateMs = Date.parse(dateKey);
    if (Number.isNaN(dateMs)) return;
    const pct = Math.max(0, Math.min(100, ((dateMs - seasonStartMs) / seasonSpanMs) * 100));
    scrubberThumb.style.left = pct + '%';
  };

  let cache = null;        // {date_iso: {region_id: int}}
  let sortedDates = null;  // ascending list of date keys
  let frameIdx = 0;
  let timer = null;

  const setOverlay = (text) => { overlay.textContent = text; };

  const applyFrame = (dateKey) => {
    repaintRegionsForDate(dateKey, cache);
    setOverlay(dateKey);
    moveScrubber(dateKey);
  };

  const stop = () => {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
    mapEl.classList.remove('playing');
    button.textContent = 'Play timelapse';
    // Revert to today's colours — clearing feature-state lets the
    // ``coalesce`` fall through to the property-based ``rating`` that
    // was written at page load.
    if (cache) clearRegionRepaint();
    setOverlay('');
    if (scrubberThumb && Number.isFinite(todayPct)) {
      scrubberThumb.style.left = todayPct + '%';
    }
  };

  const start = async () => {
    if (!MAP || !MAP.isStyleLoaded()) return;
    if (cache === null) {
      try {
        cache = await getSeasonRatings();
        sortedDates = Object.keys(cache).sort();
      } catch (_err) {
        return;
      }
    }
    if (sortedDates.length === 0) return;
    frameIdx = 0;
    mapEl.classList.add('playing');
    button.textContent = 'Stop timelapse';
    applyFrame(sortedDates[frameIdx]);
    timer = setInterval(() => {
      frameIdx += 1;
      if (frameIdx >= sortedDates.length) {
        // Last frame already painted on the previous tick — stop here so
        // the date overlay leaves the final value visible just long
        // enough to register before the regions snap back to today.
        stop();
        return;
      }
      applyFrame(sortedDates[frameIdx]);
    }, FRAME_MS);
  };

  // SNOW-47: when the scrubber commits a new date, the timelapse must
  // surrender control — both consumers paint via feature-state on the
  // same source, so a running timer would fight any user scrub.
  document.addEventListener('snowdesk:date-changed', (e) => {
    if (timer !== null && (!e.detail || e.detail.source !== 'timelapse')) {
      stop();
    }
  });

  button.addEventListener('click', () => {
    if (timer !== null) stop();
    else start();
  });
})();
