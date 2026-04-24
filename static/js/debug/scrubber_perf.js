/*
 * static/js/debug/scrubber_perf.js — SNOW-45 scrubber perf spike harness.
 *
 * Mounts a MapLibre client against the existing regions GeoJSON layer
 * and exposes six measurement functions on ``window.__perf``. The UI
 * panel wires the same functions to buttons; the console is equivalent.
 *
 * Measurements
 * ------------
 * 1. ``measureRestyleA()`` — setFeatureState path. Paints fill via a
 *    match on ['feature-state','rating']; each iteration sets a new
 *    rating on every region (149 calls), then waits one rAF. 100 iter,
 *    first 10 discarded. Reports median + p95 synchronous cost plus
 *    time-to-idle for the final iter.
 *
 * 2. ``measureRestyleB()`` — source.setData path. Paints fill via a
 *    match on ['get','rating']; each iteration mutates feature.properties
 *    on all regions and calls source.setData once.
 *
 * 3. ``measurePerDate()`` — 30 warm GETs to /api/debug/day-ratings/.
 *    Reports median, p95, and payload sizes (wire + decoded).
 *
 * 4. ``measureBulk()`` — one GET to /api/debug/season-ratings/. Reports
 *    transferred bytes, decoded bytes, and client-side JSON.parse time.
 *
 * 5. ``simulateDragUndebounced()`` — 120 requests spaced 16.67 ms
 *    (simulated 60 req/s for 2 s). Counts out-of-order responses.
 *
 * 6. ``simulateDragDebounced()`` — trailing-edge 50 ms debounce over
 *    the same 2 s window (~40 requests).
 *
 * This is a throwaway harness. No test coverage, no i18n.
 */

(function () {
  'use strict';

  const mapEl = document.getElementById('map');
  const REGIONS_URL        = mapEl.dataset.regionsUrl;
  const DAY_RATINGS_URL    = mapEl.dataset.dayRatingsUrl;
  const SEASON_RATINGS_URL = mapEl.dataset.seasonRatingsUrl;
  const BASEMAP_STYLE      = mapEl.dataset.basemapStyle;

  // Same palette as the prod map — re-used so the measurement hits the
  // same renderer path (same number of fill-color expression branches).
  const RATING_COLOURS = {
    low:          '#ccff66',
    moderate:     '#ffff00',
    considerable: '#ff9900',
    high:         '#ff0000',
    very_high:    '#a500a5',
    no_rating:    '#e0e0e0',
  };
  const RATING_KEYS = ['no_rating', 'low', 'moderate', 'considerable', 'high', 'very_high'];

  // ------------------------------------------------------------------
  // Log pane
  // ------------------------------------------------------------------

  const logEl = document.getElementById('log');
  function log(line, cls) {
    const t = new Date().toISOString().slice(11, 19);
    const span = document.createElement('div');
    if (cls) span.className = cls;
    span.textContent = `${t}  ${line}`;
    logEl.appendChild(span);
    logEl.scrollTop = logEl.scrollHeight;
  }

  // ------------------------------------------------------------------
  // Stats helpers
  // ------------------------------------------------------------------

  function stats(samples) {
    const s = [...samples].sort((a, b) => a - b);
    const n = s.length;
    const pick = (p) => s[Math.min(n - 1, Math.floor(p * n))];
    const mean = s.reduce((a, b) => a + b, 0) / n;
    return {
      n,
      min:    s[0],
      median: pick(0.5),
      p95:    pick(0.95),
      max:    s[n - 1],
      mean,
    };
  }
  function fmt(s) {
    return `n=${s.n} min=${s.min.toFixed(2)} median=${s.median.toFixed(2)} p95=${s.p95.toFixed(2)} max=${s.max.toFixed(2)} mean=${s.mean.toFixed(2)}`;
  }

  // Wait one animation frame — resolves just before the next paint.
  function nextFrame() {
    return new Promise((r) => requestAnimationFrame(() => r(performance.now())));
  }
  // Wait until the map reports idle (source loaded, rendering complete).
  function mapIdle(map) {
    return new Promise((r) => map.once('idle', () => r(performance.now())));
  }

  // ------------------------------------------------------------------
  // Map bootstrap
  // ------------------------------------------------------------------

  const map = new maplibregl.Map({
    container: 'map',
    style: BASEMAP_STYLE,
    bounds: [[5.9, 45.8], [10.5, 47.9]],
    fitBoundsOptions: { padding: 20 },
    minZoom: 5,
    maxZoom: 12,
    attributionControl: { compact: true },
  });

  let geojson = null;        // FeatureCollection (mutated in Path B)
  let featureCount = 0;      // ~149
  let ready = false;

  map.on('load', async () => {
    log('Fetching regions GeoJSON…');
    const t0 = performance.now();
    const res = await fetch(REGIONS_URL);
    geojson = await res.json();
    const t1 = performance.now();
    log(`Regions fetched in ${(t1 - t0).toFixed(0)} ms (${geojson.features.length} features)`);

    featureCount = geojson.features.length;
    // Numeric ids — required by setFeatureState.
    geojson.features.forEach((f, i) => {
      f.id = i;
      f.properties.rating = 'no_rating';
    });

    map.addSource('regions', { type: 'geojson', data: geojson });
    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions',
      paint: { 'fill-color': RATING_COLOURS.no_rating, 'fill-opacity': 0.55 },
    });

    ready = true;
    log('Map ready. Click a button or call window.__perf.* in console.', 'hi');
    // Prefill date input with today.
    const d = new Date();
    document.getElementById('date-input').value = d.toISOString().slice(0, 10);
  });

  // ------------------------------------------------------------------
  // Paint swaps
  // ------------------------------------------------------------------

  function paintFromFeatureState() {
    map.setPaintProperty('regions-fill', 'fill-color', [
      'match',
      ['coalesce', ['feature-state', 'rating'], 'no_rating'],
      'low',          RATING_COLOURS.low,
      'moderate',     RATING_COLOURS.moderate,
      'considerable', RATING_COLOURS.considerable,
      'high',         RATING_COLOURS.high,
      'very_high',    RATING_COLOURS.very_high,
      RATING_COLOURS.no_rating,
    ]);
  }
  function paintFromProperties() {
    map.setPaintProperty('regions-fill', 'fill-color', [
      'match', ['get', 'rating'],
      'low',          RATING_COLOURS.low,
      'moderate',     RATING_COLOURS.moderate,
      'considerable', RATING_COLOURS.considerable,
      'high',         RATING_COLOURS.high,
      'very_high',    RATING_COLOURS.very_high,
      RATING_COLOURS.no_rating,
    ]);
  }

  // ------------------------------------------------------------------
  // 1. Restyle — Path A (setFeatureState)
  // ------------------------------------------------------------------

  async function measureRestyleA() {
    if (!ready) { log('Map not ready yet', 'err'); return; }
    log('-- Path A · setFeatureState (100 iter, 149 regions each) --', 'hi');
    paintFromFeatureState();
    await mapIdle(map);

    const samples = [];
    for (let i = 0; i < 100; i++) {
      const ratingKey = RATING_KEYS[i % RATING_KEYS.length];
      const t0 = performance.now();
      for (let j = 0; j < featureCount; j++) {
        map.setFeatureState({ source: 'regions', id: j }, { rating: ratingKey });
      }
      const t1 = performance.now();
      samples.push(t1 - t0);
      await nextFrame();  // keep the browser responsive between iters
    }
    const tIdleStart = performance.now();
    await mapIdle(map);
    const tIdleEnd = performance.now();

    const s = stats(samples.slice(10));  // discard warm-up
    log(`Path A sync cost per iter (ms): ${fmt(s)}`);
    log(`Path A idle lag after final iter: ${(tIdleEnd - tIdleStart).toFixed(2)} ms`);
    return s;
  }

  // ------------------------------------------------------------------
  // 2. Restyle — Path B (source.setData)
  // ------------------------------------------------------------------

  async function measureRestyleB() {
    if (!ready) { log('Map not ready yet', 'err'); return; }
    log('-- Path B · source.setData (100 iter, 149 regions each) --', 'hi');
    paintFromProperties();
    await mapIdle(map);

    const source = map.getSource('regions');
    const samples = [];
    for (let i = 0; i < 100; i++) {
      const ratingKey = RATING_KEYS[i % RATING_KEYS.length];
      const t0 = performance.now();
      for (let j = 0; j < featureCount; j++) {
        geojson.features[j].properties.rating = ratingKey;
      }
      source.setData(geojson);
      const t1 = performance.now();
      samples.push(t1 - t0);
      await nextFrame();
    }
    const tIdleStart = performance.now();
    await mapIdle(map);
    const tIdleEnd = performance.now();

    const s = stats(samples.slice(10));
    log(`Path B sync cost per iter (ms): ${fmt(s)}`);
    log(`Path B idle lag after final iter: ${(tIdleEnd - tIdleStart).toFixed(2)} ms`);
    return s;
  }

  // ------------------------------------------------------------------
  // 3. Per-date endpoint — 30 warm requests
  // ------------------------------------------------------------------

  async function measurePerDate() {
    const dateStr = document.getElementById('date-input').value;
    if (!dateStr) { log('Set a date first', 'err'); return; }
    log(`-- Per-date endpoint · ${dateStr} · 30 warm GETs --`, 'hi');

    // Warm up (discarded).
    for (let i = 0; i < 3; i++) {
      await fetch(`${DAY_RATINGS_URL}?date=${dateStr}`).then((r) => r.text());
    }

    const samples = [];
    let lastDecoded = 0;
    let lastWire   = 0;
    for (let i = 0; i < 30; i++) {
      const t0 = performance.now();
      const res = await fetch(`${DAY_RATINGS_URL}?date=${dateStr}`);
      const body = await res.text();
      const t1 = performance.now();
      samples.push(t1 - t0);
      lastDecoded = new TextEncoder().encode(body).length;
      // content-length may be missing when transfer-encoding=chunked.
      const cl = res.headers.get('content-length');
      if (cl) lastWire = parseInt(cl, 10);
    }
    const s = stats(samples);
    log(`Per-date response time (ms): ${fmt(s)}`);
    log(`Per-date decoded payload: ${lastDecoded} bytes${lastWire ? ` · wire (content-length): ${lastWire}` : ' · wire: n/a (chunked)'}`);
    return s;
  }

  // ------------------------------------------------------------------
  // 4. Bulk endpoint — one fetch, measure size + parse cost
  // ------------------------------------------------------------------

  async function measureBulk() {
    log('-- Season bundle · 1 GET --', 'hi');
    const t0 = performance.now();
    const res = await fetch(SEASON_RATINGS_URL);
    const body = await res.text();
    const t1 = performance.now();
    const parsed = JSON.parse(body);
    const t2 = performance.now();

    const decoded = new TextEncoder().encode(body).length;
    const wire = res.headers.get('content-length');
    const dateCount = Object.keys(parsed).length;
    const regionsFirstDate = Object.keys(parsed[Object.keys(parsed)[0]] || {}).length;
    log(`Fetch+text: ${(t1 - t0).toFixed(1)} ms · parse: ${(t2 - t1).toFixed(1)} ms`);
    log(`Decoded: ${decoded} bytes (${(decoded / 1024).toFixed(1)} KiB)${wire ? ` · wire: ${wire} (${(parseInt(wire, 10) / 1024).toFixed(1)} KiB)` : ' · wire: n/a (chunked)'}`);
    log(`Shape: ${dateCount} dates × ${regionsFirstDate} regions (day 1)`);
    // Return to caller so it can compute Content-Encoding savings from DevTools.
    return { decoded, wire, fetchMs: t1 - t0, parseMs: t2 - t1, dateCount };
  }

  // ------------------------------------------------------------------
  // 5/6. Drag realism — un-debounced vs debounced
  // ------------------------------------------------------------------

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  async function simulateDrag(debounceMs) {
    const dateStr = document.getElementById('date-input').value;
    if (!dateStr) { log('Set a date first', 'err'); return; }
    const tickMs      = 1000 / 60;             // 60 fps drag
    const totalMs     = 2000;
    const totalTicks  = Math.round(totalMs / tickMs);
    const label = debounceMs ? `debounced (${debounceMs} ms trailing)` : 'un-debounced';
    log(`-- Drag sim · ${label} · ${totalTicks} ticks over ${totalMs} ms --`, 'hi');

    const inflight = [];
    const samples  = [];
    const completionOrder = [];
    let requestCount = 0;
    let lastFireAt = -Infinity;
    let pendingTrail = null;
    const start = performance.now();

    // Fire exactly once per tick when un-debounced; trailing-edge only
    // when debounced — simulates a drag that collapses rapid ticks.
    for (let i = 0; i < totalTicks; i++) {
      const tickAt = start + i * tickMs;
      while (performance.now() < tickAt) await sleep(0.5);
      const now = performance.now();
      const shouldFire = !debounceMs || (now - lastFireAt >= debounceMs);
      if (shouldFire) {
        lastFireAt = now;
        const seq = requestCount++;
        const t0 = now;
        const p = fetch(`${DAY_RATINGS_URL}?date=${dateStr}`)
          .then((r) => r.text())
          .then(() => {
            samples.push(performance.now() - t0);
            completionOrder.push(seq);
          });
        inflight.push(p);
      } else if (debounceMs) {
        // Reset trailing timer: we'd fire this if no more ticks arrive
        // within debounceMs. For the sim we always get more ticks, so
        // the trailing won't fire here; it fires on the post-loop flush.
        pendingTrail = true;
      }
    }
    // Trailing flush for debounced mode.
    if (debounceMs && pendingTrail) {
      const seq = requestCount++;
      const t0 = performance.now();
      const p = fetch(`${DAY_RATINGS_URL}?date=${dateStr}`)
        .then((r) => r.text())
        .then(() => {
          samples.push(performance.now() - t0);
          completionOrder.push(seq);
        });
      inflight.push(p);
    }
    await Promise.all(inflight);
    const elapsed = performance.now() - start;

    // Out-of-order = any completion where the index is less than a
    // previously-seen index in the completion stream.
    let maxSeen = -1;
    let outOfOrder = 0;
    for (const seq of completionOrder) {
      if (seq < maxSeen) outOfOrder++;
      else maxSeen = seq;
    }

    const s = stats(samples);
    log(`Requests fired: ${requestCount} · elapsed: ${elapsed.toFixed(0)} ms`);
    log(`Response time (ms): ${fmt(s)}`);
    log(`Out-of-order responses: ${outOfOrder} / ${requestCount}`);
    return { requestCount, outOfOrder, stats: s };
  }

  // ------------------------------------------------------------------
  // Wire-up
  // ------------------------------------------------------------------

  async function run(btnId, fn) {
    const btn = document.getElementById(btnId);
    btn.disabled = true;
    try { await fn(); }
    catch (e) { log(`ERROR: ${e.message || e}`, 'err'); console.error(e); }
    finally { btn.disabled = false; }
  }

  document.getElementById('btn-restyle-a')
    .addEventListener('click', () => run('btn-restyle-a', measureRestyleA));
  document.getElementById('btn-restyle-b')
    .addEventListener('click', () => run('btn-restyle-b', measureRestyleB));
  document.getElementById('btn-per-date')
    .addEventListener('click', () => run('btn-per-date', measurePerDate));
  document.getElementById('btn-bulk')
    .addEventListener('click', () => run('btn-bulk', measureBulk));
  document.getElementById('btn-drag-undebounced')
    .addEventListener('click', () => run('btn-drag-undebounced', () => simulateDrag(0)));
  document.getElementById('btn-drag-debounced')
    .addEventListener('click', () => run('btn-drag-debounced', () => simulateDrag(50)));

  window.__perf = {
    measureRestyleA,
    measureRestyleB,
    measurePerDate,
    measureBulk,
    simulateDragUndebounced: () => simulateDrag(0),
    simulateDragDebounced:   () => simulateDrag(50),
    map,
    stats,
  };
})();
