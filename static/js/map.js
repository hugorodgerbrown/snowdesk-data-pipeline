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

(function () {
  'use strict';

  // Debug mode. Activate with ?debug=1 in the URL, or press 'd' while
  // the page is focused. Exposes region IDs in the drawer and on the map.
  let DEBUG = new URLSearchParams(location.search).has('debug');

  const mapEl = document.getElementById('map');
  const REGIONS_URL   = mapEl.dataset.regionsUrl;
  const SUMMARIES_URL = mapEl.dataset.summariesUrl;
  const RESORTS_URL   = mapEl.dataset.resortsUrl;

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

  // OpenFreeMap gives us a free, attribution-compliant basemap with no
  // API key. Swap for MapTiler / Mapbox later if a custom style is wanted.
  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://tiles.openfreemap.org/styles/liberty',
    center: [8.23, 46.5],
    zoom: 6.4,
    minZoom: 5,
    maxZoom: 12,
    maxBounds: [[3.5, 43.5], [13.0, 49.5]],
    attributionControl: { compact: true },
  });

  // In-memory lookup from numeric feature id -> region properties.
  // Numeric because setFeatureState requires a numeric (or numeric-coerceable) id.
  const REGION_LOOKUP = {};

  // Parallel lookups used by the search box — FEATURE_BY_ID keeps the full
  // (un-clipped) feature for panning, FEATURE_BY_REGION_ID lets the search
  // resolve a user-typed match back to the feature it points at.
  const FEATURE_BY_ID = {};
  const FEATURE_BY_REGION_ID = {};

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
    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions',
      paint: {
        'fill-color': [
          'match', ['get', 'rating'],
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

    const ratingLabel = (r, sub) => {
      const names = {
        low: 'Low (1)', moderate: 'Moderate (2)', considerable: 'Considerable (3)',
        high: 'High (4)', very_high: 'Very high (5)', no_rating: 'No rating',
      };
      const base = names[r] || r;
      return sub === 'plus' ? base.replace(/\)$/, '+)')
           : sub === 'minus' ? base.replace(/\)$/, '\u2212)')
           : base;
    };

    // Canonical SLF region-ID shape (e.g. "CH-4115"). Anything else is rejected
    // before it reaches the CTA href to prevent a malformed GeoJSON payload
    // turning into an open-redirect / javascript: URL on the client.
    const REGION_ID_RE = /^[A-Za-z]{2}-[A-Za-z0-9]+$/;

    const el = (tag, attrs, text) => {
      const node = document.createElement(tag);
      if (attrs && attrs.style) node.style.cssText = attrs.style;
      if (text !== undefined) node.textContent = text;
      return node;
    };

    const renderSheet = (props) => {
      const regionID = props.regionID;
      const summary = BULLETIN_SUMMARIES[regionID];
      // Region name — prefer GeoJSON, fall back to summary, then to the ID.
      const name = props.name || props.name_en || props.NAME
                   || (summary && summary.name)
                   || regionID;

      const $ = (id) => document.getElementById(id);
      $('sheet-title').textContent = name;
      $('sheet-cta').href = REGION_ID_RE.test(regionID) ? `/${regionID}/` : '#';

      // Debug region-ID readout — visible only in debug mode.
      const debugEl = $('sheet-debug-id');
      debugEl.textContent = regionID;
      debugEl.style.display = DEBUG ? 'block' : 'none';

      // Rebuild the sheet body. Every text node is set via textContent or
      // the ``el()`` helper so that region names / resort names from the
      // API are never interpreted as HTML — preventing XSS on any value
      // containing <, >, &, or quotes.
      const body = $('sheet-body');
      body.replaceChildren();

      if (!summary) {
        body.append(el(
          'div',
          { style: 'padding: 8px 0; color: #8a8880; font-size: 12px;' },
          'No bulletin data available for this region today.',
        ));
      } else {
        const colour = RATING_COLOURS[summary.rating] || RATING_COLOURS.no_rating;
        const textCol = summary.rating === 'high' || summary.rating === 'very_high' ? '#fff' : '#2a1f00';

        const ratingBox = el('div', {
          style: `margin-top: 10px; padding: 8px 10px; border-radius: 4px; background: ${colour}; color: ${textCol};`,
        });
        ratingBox.append(el(
          'div',
          { style: 'font-size: 12px; font-weight: 500;' },
          ratingLabel(summary.rating, summary.subdivision),
        ));
        body.append(ratingBox);

        const resorts = RESORTS_BY_REGION[regionID] || [];
        if (resorts.length) {
          const wrap = el('div', { style: 'margin-top: 10px;' });
          const line = el('div', { style: 'font-size: 12px;' });
          line.append(el(
            'span', { style: 'color: #8a8880;' }, 'Resorts',
          ));
          line.append(document.createTextNode(' \u00b7 '));
          line.append(el('span', null, resorts.join(', ')));
          const note = el(
            'div',
            { style: 'font-size: 11px; color: #8a8880; margin-top: 4px; font-style: italic;' },
            'Resorts & ski areas may span multiple regions.',
          );
          wrap.append(line, note);
          body.append(wrap);
        }
      }
      $('sheet').classList.add('open');
    };

    const clearSelection = () => {
      if (selectedId !== null) {
        map.setFeatureState({ source: 'regions', id: selectedId }, { selected: false });
        selectedId = null;
      }
      document.getElementById('sheet').classList.remove('open');
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

    // Pan/zoom so the region is centred in the visible portion of the map —
    // i.e. the slice above the bottom sheet. We pass the sheet's height as
    // bottom padding so fitBounds treats the drawer area as off-limits.
    const panToRegionAboveSheet = (feature) => {
      const bbox = featureBBox(feature);
      const sheet = document.getElementById('sheet');
      const sheetHeight = sheet.offsetHeight || 0;
      map.fitBounds(bbox, {
        padding: { top: 60, right: 40, bottom: sheetHeight + 40, left: 40 },
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
    const selectFeature = (numericId, { toggle = true } = {}) => {
      if (numericId === selectedId) {
        if (toggle) clearSelection();
        return;
      }
      if (selectedId !== null) {
        map.setFeatureState({ source: 'regions', id: selectedId }, { selected: false });
      }
      selectedId = numericId;
      map.setFeatureState({ source: 'regions', id: selectedId }, { selected: true });

      renderSheet(REGION_LOOKUP[numericId]);

      // Wait for the sheet to measure itself (it just became .open). One frame
      // is enough — the CSS transition hasn't finished but offsetHeight is already
      // the final height because the transform, not height, is animating.
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

    const sheet = document.getElementById('sheet');
    const bodyWrap = document.querySelector('.sheet-body-wrap');
    const closeBtn = document.getElementById('sheet-close');

    closeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      clearSelection();
    });

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && sheet.classList.contains('open')) clearSelection();
      // Toggle debug mode; ignore when typing in an input/textarea.
      if (e.key === 'd' && !e.target.matches('input, textarea')) {
        DEBUG = !DEBUG;
        const debugEl = document.getElementById('sheet-debug-id');
        if (debugEl) debugEl.style.display = (DEBUG && sheet.classList.contains('open')) ? 'block' : 'none';
        const pill = document.getElementById('debug-pill');
        if (pill) pill.style.display = DEBUG ? 'block' : 'none';
      }
    });

    // --- Drag controller ---
    //
    // Design goals:
    //   1. Drag can begin anywhere on the sheet, including over body text.
    //   2. Gesture is "claimed" only after enough vertical movement — small taps
    //      on buttons/links inside the sheet never accidentally start a drag.
    //   3. Respects inner scroll: if the body is scrolled down, downward drag
    //      scrolls the body instead of dragging the sheet. Only when scrollTop
    //      is 0 AND the gesture is downward do we take over.
    //   4. Upward drags at rest get rubber-band resistance.
    //   5. Release animation is driven by JS, not CSS, so speed matches the
    //      flick velocity — a hard flick dismisses fast, a gentle let-go settles
    //      smoothly.

    const GESTURE_CLAIM_PX = 6;            // pixels of vertical movement before we claim the gesture
    const DISMISS_DISTANCE_RATIO = 0.33;   // drag past 33% of sheet height → dismiss on release
    const DISMISS_VELOCITY_PX_MS = 0.6;    // flick faster than this → dismiss regardless of distance
    const RUBBER_BAND_DIVISOR = 4;         // upward drag resistance (higher = stiffer)
    const VELOCITY_SAMPLE_WINDOW_MS = 60;  // only the last N ms of samples count for release velocity
    const MIN_ANIM_DURATION_MS = 120;      // animation clamp — below this feels twitchy
    const MAX_ANIM_DURATION_MS = 400;      // above this feels sluggish

    let drag = null;
    let animFrame = null;

    const pointerDown = (e) => {
      if (!sheet.classList.contains('open')) return;
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
        sheetHeight: sheet.offsetHeight,
        startedInBody: bodyWrap.contains(e.target),
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

        // If the drag started inside the scrollable body AND the body was
        // scrolled down AND the gesture is downward (delta > 0), let the body
        // scroll instead of dragging the sheet. The user is scrolling content,
        // not trying to dismiss.
        if (drag.startedInBody && drag.bodyScrollAtStart > 0 && delta > 0) {
          drag = null;
          return;
        }
        // Likewise, if it's an upward gesture that started in a scrollable body
        // that has room to scroll down further, let it scroll normally. (Our
        // sheet doesn't grow upward, so upward-in-body always means scroll.)
        if (drag.startedInBody && delta < 0) {
          drag = null;
          return;
        }

        // Commit: we're dragging the sheet.
        drag.claimed = true;
        sheet.classList.add('dragging');
        sheet.setPointerCapture(e.pointerId);
      }

      // Phase 2: claimed. Apply rubber-band on upward movement and set transform.
      if (delta < 0) delta = delta / RUBBER_BAND_DIVISOR;
      drag.offset = delta;
      sheet.style.transform = `translateY(${delta}px)`;

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
      if (!drag.claimed) { drag = null; return; }

      const offset = drag.offset;
      const sheetHeight = drag.sheetHeight;

      const first = drag.samples[0];
      const last = drag.samples[drag.samples.length - 1];
      const dt = last.t - first.t;
      const velocity = dt > 0 ? (last.y - first.y) / dt : 0;  // px per ms, + = downward

      if (sheet.hasPointerCapture(drag.pointerId)) {
        sheet.releasePointerCapture(drag.pointerId);
      }
      sheet.classList.remove('dragging');
      drag = null;

      const draggedFarEnough = offset > sheetHeight * DISMISS_DISTANCE_RATIO;
      const flickedDownFast  = velocity > DISMISS_VELOCITY_PX_MS;

      if (draggedFarEnough || flickedDownFast) {
        animateDismiss(offset, sheetHeight, velocity);
      } else {
        animateSnapBack(offset, velocity);
      }
    };

    // Velocity-matched dismiss: target is translateY(sheetHeight), duration is
    // derived from the distance remaining and the release velocity, so a hard
    // flick completes fast and a slow drag completes at a natural speed.
    const animateDismiss = (fromOffset, sheetHeight, velocity) => {
      const distance = sheetHeight - fromOffset;
      // If the release had momentum, use it; otherwise fall back to a median speed.
      const effectiveVelocity = Math.max(velocity, 0.5);  // px/ms floor
      const rawDuration = distance / effectiveVelocity;
      const duration = Math.max(MIN_ANIM_DURATION_MS, Math.min(MAX_ANIM_DURATION_MS, rawDuration));

      animateTransform(fromOffset, sheetHeight, duration, () => {
        // Settle: return control to CSS by clearing inline transform and the .open class.
        sheet.classList.remove('animating');
        sheet.style.transform = '';
        clearSelection();  // removes .open class; resets selection state
      });
    };

    // Velocity-aware snap-back: if the user was still moving down when they let
    // go but didn't flick hard enough to dismiss, honour that residual motion
    // briefly before the snap. Otherwise, a simple ease back to 0.
    const animateSnapBack = (fromOffset, _velocity) => {
      const distance = Math.abs(fromOffset);
      // Snap-back is always a similar time regardless of distance — that's what
      // makes it feel springy rather than tired.
      const duration = Math.max(180, Math.min(280, distance * 1.2));

      animateTransform(fromOffset, 0, duration, () => {
        sheet.classList.remove('animating');
        sheet.style.transform = '';  // .open keeps it at translateY(0)
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
    let currentResults = [];
    let activeIdx = -1;

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
      closeResults();
      inputEl.blur();
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
          inputEl.blur();
        }
      }
    });

    // Close the dropdown on outside pointer interaction. Use pointerdown
    // (before focus changes) and ignore clicks inside the results list
    // so ``li`` mousedown handlers still fire.
    document.addEventListener('pointerdown', (e) => {
      if (e.target === inputEl) return;
      if (resultsEl.contains(e.target)) return;
      closeResults();
    });
  });
})();
