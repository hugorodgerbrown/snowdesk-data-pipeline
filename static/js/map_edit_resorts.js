/*
 * static/js/map_edit_resorts.js — SNOW-74 in-map resort editor.
 *
 * Loaded only when the page is rendered with ?edit=resorts AND
 * settings.DEBUG is True (the template guards both the panel include
 * and the <script> tag). The DEBUG guard at the API layer hard-refuses
 * any write attempt — this file is a UI tool, not the trust boundary.
 *
 * Hooks into the global ``MAP`` and ``MAP_READY_PROMISE`` declared at
 * the top of ``static/js/map.js`` (top-level let/const in classic
 * scripts share scope across <script> tags in the same document, so no
 * window export is needed).
 *
 * Coordinate-ordering reminder:
 *   - DB columns:        latitude, longitude
 *   - JSON wire format:  {"latitude": ..., "longitude": ...}
 *   - GeoJSON:           coordinates: [longitude, latitude]
 *   - MapLibre marker:   marker.getLngLat() → {lng, lat} (note: lng)
 */

(function () {
  'use strict';

  const panel = document.getElementById('edit-resorts-panel');
  if (!panel) return;

  const QUEUE_URL                 = panel.dataset.queueUrl;
  const SAVE_URL_TEMPLATE         = panel.dataset.saveUrlTemplate;
  const RESORTS_GEOJSON_URL       = panel.dataset.resortsGeojsonUrl;

  const csrfTokenInput            = panel.querySelector('input[name="csrfmiddlewaretoken"]');
  const CSRF_TOKEN                = csrfTokenInput ? csrfTokenInput.value : '';

  const remainingEl               = document.getElementById('edit-resorts-remaining');
  const queueListEl               = document.getElementById('edit-resorts-queue');
  const targetEl                  = document.getElementById('edit-resorts-target');
  const saveBtn                   = document.getElementById('edit-resorts-save');
  const cancelBtn                 = document.getElementById('edit-resorts-cancel');
  const skipBtn                   = document.getElementById('edit-resorts-skip');
  const errorEl                   = document.getElementById('edit-resorts-error');
  const searchInput               = document.getElementById('edit-resorts-search');
  const searchResultsEl           = document.getElementById('edit-resorts-search-results');
  const pasteInput                = document.getElementById('edit-resorts-paste');

  // State.
  let queue          = [];   // Array of queue entries (resorts needing geocoding).
  let allResorts     = [];   // Flat catalogue for the search box.
  let currentTarget  = null; // The selected queue entry.
  let draftMarker    = null; // MapLibre Marker, draggable.

  // Format a coord pair to 5 decimal places (≈1m precision in Switzerland).
  const fmtCoord = (lat, lng) =>
    `${lat.toFixed(5)}, ${lng.toFixed(5)}`;

  const showError = (msg) => {
    errorEl.textContent = msg;
    errorEl.hidden = false;
  };

  const clearError = () => {
    errorEl.textContent = '';
    errorEl.hidden = true;
  };

  const renderRemaining = () => {
    remainingEl.textContent = `${queue.length} ${queue.length === 1 ? 'left' : 'left'}`;
  };

  // L1 prefix — the first 4 chars of an SLF region_id (e.g. "CH-4115" → "CH-4")
  // is the EAWS major-region key. Used to group the queue into geographic
  // sections in the panel.
  const l1Of = (regionId) => regionId.slice(0, 4);

  const renderQueue = () => {
    queueListEl.innerHTML = '';
    if (queue.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'italic text-slate-400';
      empty.textContent = 'Queue empty — nice work.';
      queueListEl.appendChild(empty);
      return;
    }
    let lastL1 = null;
    for (const entry of queue) {
      const l1 = l1Of(entry.region_id);
      if (l1 !== lastL1) {
        // Section header for the L1 area. The first section gets no top
        // margin/border via the first:* utilities so it sits flush with
        // the queue list label.
        const header = document.createElement('li');
        header.className = 'mt-3 border-t border-slate-200 px-2 pt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 first:mt-0 first:border-t-0 first:pt-0';
        header.textContent = l1;
        queueListEl.appendChild(header);
        lastL1 = l1;
      }

      const li = document.createElement('li');
      const isCurrent = currentTarget && currentTarget.id === entry.id;
      li.className = [
        'flex cursor-pointer items-baseline justify-between rounded px-2 py-1',
        isCurrent ? 'bg-sky-100 font-semibold text-sky-900' : 'hover:bg-slate-100',
      ].join(' ');
      li.dataset.resortId = String(entry.id);

      const left = document.createElement('span');
      left.textContent = entry.name;
      li.appendChild(left);

      const right = document.createElement('span');
      right.className = 'text-xs text-slate-500';
      right.textContent = entry.region_id + (entry.needs_review ? ' ⚠' : '');
      li.appendChild(right);

      li.addEventListener('click', () => selectTarget(entry));
      queueListEl.appendChild(li);
    }
  };

  const renderTarget = () => {
    if (!currentTarget) {
      targetEl.innerHTML = '<p class="italic text-slate-400">No resort selected.</p>';
      saveBtn.disabled = true;
      cancelBtn.disabled = true;
      return;
    }
    const t = currentTarget;
    const currentCoords = (t.latitude != null && t.longitude != null)
      ? fmtCoord(t.latitude, t.longitude)
      : '(none)';
    let draftCoords = '—';
    if (draftMarker) {
      const ll = draftMarker.getLngLat();
      draftCoords = fmtCoord(ll.lat, ll.lng);
    }
    targetEl.innerHTML = `
      <p class="font-semibold text-slate-900">${escapeHtml(t.name)}</p>
      <p class="text-xs text-slate-500">${escapeHtml(t.region_name)} (${escapeHtml(t.region_id)}) · ${escapeHtml(t.canton)}</p>
      <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        <dt class="text-slate-500">Current</dt>
        <dd class="font-mono text-slate-700">${escapeHtml(currentCoords)}</dd>
        <dt class="text-slate-500">Draft</dt>
        <dd class="font-mono ${draftMarker ? 'text-amber-700' : 'text-slate-400'}">${escapeHtml(draftCoords)}</dd>
      </dl>
    `;
    saveBtn.disabled = !draftMarker;
    cancelBtn.disabled = !draftMarker;
  };

  const escapeHtml = (s) =>
    String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  // Selection ---------------------------------------------------------------

  const selectTarget = (entry) => {
    currentTarget = entry;
    clearError();
    removeDraftMarker();
    // If the resort already has coords, pre-populate a draft marker so the
    // operator can drag-to-refine without first clicking-to-place.
    if (entry.latitude != null && entry.longitude != null) {
      placeDraftMarker(entry.longitude, entry.latitude);
      // Pan to it so the operator can see what they're editing.
      if (typeof MAP !== 'undefined' && MAP) {
        MAP.flyTo({ center: [entry.longitude, entry.latitude], zoom: 12 });
      }
    }
    renderQueue();
    renderTarget();
  };

  const selectTargetById = (id) => {
    const entry = queue.find((e) => e.id === id) || allResorts.find((e) => e.id === id);
    if (!entry) return;
    // If the search hit isn't a queue entry, normalise it to queue shape so
    // selectTarget can render it. We need extra fields the catalogue lacks
    // (region_name, canton, latitude, longitude, name_alt) — fetch by hitting
    // the queue endpoint again would be wasteful for one row, so we fall
    // back to whatever shape we have. The render function tolerates blanks.
    if (!('region_name' in entry)) {
      // Fetch the full catalogue entry once via a minimal GET — re-using
      // the queue endpoint to keep the surface area tight.
      fetch(QUEUE_URL).then((r) => r.json()).then((data) => {
        const full = data.queue.find((e) => e.id === id)
          || data.all_resorts.find((e) => e.id === id);
        if (full) selectTarget(full);
      });
      return;
    }
    selectTarget(entry);
  };

  // Draft marker ------------------------------------------------------------

  const placeDraftMarker = (lng, lat) => {
    removeDraftMarker();
    if (typeof MAP === 'undefined' || !MAP) return;
    draftMarker = new maplibregl.Marker({ draggable: true, color: '#f59e0b' })
      .setLngLat([lng, lat])
      .addTo(MAP);
    draftMarker.on('dragend', () => {
      renderTarget();
    });
  };

  const removeDraftMarker = () => {
    if (draftMarker) {
      draftMarker.remove();
      draftMarker = null;
    }
    if (pasteInput) pasteInput.value = '';
  };

  // Parse a "lat, lon" string of the kind Google Maps shows above its
  // search results (e.g. ``46.431918, 6.978587``). Tolerates whitespace
  // variations and a trailing degrees sign. Returns ``null`` for any
  // input that does not parse to two finite numbers in valid lat/lon
  // ranges. Bbox enforcement happens server-side at save time.
  const parseLatLonString = (raw) => {
    if (!raw) return null;
    const cleaned = raw.replace(/[°\s]+/g, ' ').trim();
    const parts = cleaned.split(/[,;\s]+/).filter(Boolean);
    if (parts.length !== 2) return null;
    const lat = Number(parts[0]);
    const lon = Number(parts[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    return { lat, lon };
  };

  const onPasteInput = () => {
    if (!currentTarget) return;
    const parsed = parseLatLonString(pasteInput.value);
    if (!parsed) {
      // Empty / unparseable — clear any prior marker so the panel state
      // doesn't lie. (User may have pasted, then deleted the value.)
      if (pasteInput.value.trim() === '') {
        removeDraftMarker();
        renderTarget();
      }
      return;
    }
    placeDraftMarker(parsed.lon, parsed.lat);
    if (typeof MAP !== 'undefined' && MAP) {
      MAP.flyTo({ center: [parsed.lon, parsed.lat], zoom: 13 });
    }
    clearError();
    renderTarget();
  };

  // Save / cancel / skip ----------------------------------------------------

  const save = async () => {
    if (!currentTarget || !draftMarker) return;
    const ll = draftMarker.getLngLat();
    const url = SAVE_URL_TEMPLATE.replace('__ID__', String(currentTarget.id));
    saveBtn.disabled = true;
    clearError();
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CSRF_TOKEN,
        },
        // GeoJSON uses [lon, lat]; MapLibre returns {lng, lat}; the wire
        // format here is keyed by name — no ordering ambiguity.
        body: JSON.stringify({ latitude: ll.lat, longitude: ll.lng }),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const errBody = await resp.json();
          detail = errBody.detail || errBody.error || detail;
        } catch (_) { /* response body wasn't JSON */ }
        showError(`Save failed: ${detail}`);
        saveBtn.disabled = false;
        return;
      }
      const data = await resp.json();
      // Drop the saved row from the queue and advance.
      queue = queue.filter((e) => e.id !== data.id);
      removeDraftMarker();
      currentTarget = null;
      if (data.next_in_queue) {
        // The server's next_in_queue is already in queue-entry shape; the
        // queue array itself is computed fresh from the saved-list filter
        // above, but the next pointer might reference a row we already
        // know about (idempotent) or one that surfaced because the saved
        // row had needs_review=True and pushed others up.
        const known = queue.find((e) => e.id === data.next_in_queue.id);
        selectTarget(known || data.next_in_queue);
      }
      renderQueue();
      renderRemaining();
      renderTarget();
      refreshResortsLayer();
    } catch (err) {
      showError(`Save failed: ${err.message || err}`);
      saveBtn.disabled = false;
    }
  };

  const cancel = () => {
    removeDraftMarker();
    clearError();
    renderTarget();
  };

  const skip = () => {
    if (!currentTarget) return;
    removeDraftMarker();
    const idx = queue.findIndex((e) => e.id === currentTarget.id);
    const next = queue[idx + 1] || queue[0] || null;
    currentTarget = next && next.id !== currentTarget.id ? next : null;
    renderQueue();
    renderTarget();
  };

  // Search ------------------------------------------------------------------

  const statusBadge = (m) => {
    // Three states with distinct colour coding so the operator can see
    // at a glance which resorts are already placed vs still need work.
    if (m.needs_review) {
      return { label: 'Review', cls: 'bg-red-100 text-red-800' };
    }
    if (m.has_coords) {
      return { label: 'Set', cls: 'bg-emerald-100 text-emerald-800' };
    }
    return { label: 'Unset', cls: 'bg-amber-100 text-amber-800' };
  };

  const renderSearchResults = (matches) => {
    searchResultsEl.innerHTML = '';
    if (matches.length === 0) {
      const li = document.createElement('li');
      li.className = 'px-2 py-1 italic text-slate-400';
      li.textContent = 'No matches.';
      searchResultsEl.appendChild(li);
      searchResultsEl.hidden = false;
      return;
    }
    for (const m of matches.slice(0, 20)) {
      const li = document.createElement('li');
      li.className = 'flex cursor-pointer items-center justify-between gap-2 px-2 py-1 hover:bg-sky-50';
      li.dataset.resortId = String(m.id);

      const left = document.createElement('span');
      left.className = 'flex items-baseline gap-2 truncate';
      const name = document.createElement('span');
      name.className = 'truncate text-slate-900';
      name.textContent = m.name;
      left.appendChild(name);
      const region = document.createElement('span');
      region.className = 'text-xs text-slate-400';
      region.textContent = m.region_id;
      left.appendChild(region);
      li.appendChild(left);

      const badge = statusBadge(m);
      const right = document.createElement('span');
      right.className = `shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${badge.cls}`;
      right.textContent = badge.label;
      li.appendChild(right);

      li.addEventListener('click', () => {
        searchInput.value = '';
        searchResultsEl.hidden = true;
        selectTargetById(m.id);
      });
      searchResultsEl.appendChild(li);
    }
    searchResultsEl.hidden = false;
  };

  // Sort once at hydration time so the dropdown is always alphabetical.
  const sortedAllResorts = () =>
    [...allResorts].sort((a, b) => a.name.localeCompare(b.name));

  const onSearch = () => {
    const q = searchInput.value.trim().toLowerCase();
    const source = sortedAllResorts();
    const matches = q
      ? source.filter((r) => r.name.toLowerCase().includes(q))
      : source;
    renderSearchResults(matches);
  };

  // Show the full sorted list on focus so the operator can browse without
  // typing — useful when correcting a placed resort whose name they don't
  // remember exactly.
  const onSearchFocus = () => {
    if (allResorts.length > 0) renderSearchResults(sortedAllResorts());
  };

  // Resort points layer (existing geocoded resorts) -------------------------

  const SOURCE_ID = 'edit-resorts-source';
  const LAYER_ID  = 'edit-resorts-points';

  const refreshResortsLayer = async () => {
    if (typeof MAP === 'undefined' || !MAP) return;
    try {
      const resp = await fetch(RESORTS_GEOJSON_URL);
      if (!resp.ok) return;
      const data = await resp.json();
      const src = MAP.getSource(SOURCE_ID);
      if (src && 'setData' in src) {
        src.setData(data);
      } else {
        MAP.addSource(SOURCE_ID, { type: 'geojson', data: data });
        MAP.addLayer({
          id: LAYER_ID,
          type: 'circle',
          source: SOURCE_ID,
          paint: {
            'circle-radius': 6,
            'circle-color': [
              'case',
              ['get', 'needs_review'], '#dc2626',
              '#0284c7',
            ],
            'circle-stroke-color': '#ffffff',
            'circle-stroke-width': 1.5,
          },
        });
        MAP.on('click', LAYER_ID, (e) => {
          if (!e.features || e.features.length === 0) return;
          const id = e.features[0].properties.id;
          if (id != null) selectTargetById(Number(id));
        });
        MAP.on('mouseenter', LAYER_ID, () => {
          MAP.getCanvas().style.cursor = 'pointer';
        });
        MAP.on('mouseleave', LAYER_ID, () => {
          MAP.getCanvas().style.cursor = '';
        });
      }
    } catch (err) {
      console.warn('Failed to refresh resorts layer', err);
    }
  };

  // Visibility tweaks for edit mode -----------------------------------------

  const enterEditModeVisuals = () => {
    if (typeof MAP === 'undefined' || !MAP) return;
    // Hide the choropleth fill + labels; dim the outlines for region context.
    try { MAP.setLayoutProperty('regions-fill', 'visibility', 'none'); } catch (_) {}
    try { MAP.setLayoutProperty('regions-label', 'visibility', 'none'); } catch (_) {}
    try { MAP.setPaintProperty('regions-line', 'line-opacity', 0.2); } catch (_) {}
    // Hide normal-mode UI noise.
    const sheet = document.getElementById('sheet');
    if (sheet) sheet.style.display = 'none';
    const scrubber = document.getElementById('season-scrubber');
    if (scrubber) scrubber.style.display = 'none';
    const legend = document.getElementById('map-legend');
    if (legend) legend.style.display = 'none';
    // Force the swisstopo_winter basemap (resort villages) without
    // overwriting the operator's normal-mode preference in localStorage.
    forceWinterBasemap();
  };

  const forceWinterBasemap = () => {
    const button = document.querySelector('[data-basemap-key="swisstopo_winter"]');
    if (!button) return;
    const url = button.dataset.basemapUrl;
    if (!url || typeof MAP === 'undefined' || !MAP) return;
    // Only switch if the current style differs.
    const currentStyle = MAP.getStyle && MAP.getStyle();
    const currentSrc = currentStyle && currentStyle.sprite ? currentStyle.sprite : '';
    if (currentSrc.includes('swisstopo')) return;
    MAP.setStyle(url);
  };

  // Map click handler -------------------------------------------------------

  const onMapClick = (e) => {
    if (!currentTarget) return;
    if (typeof MAP === 'undefined' || !MAP) return;
    // If the click hit a resort point, the layer-specific handler runs and
    // we don't want to also drop a pin. queryRenderedFeatures filters by
    // layer; if any feature is returned, bail.
    const hits = MAP.queryRenderedFeatures(e.point, { layers: [LAYER_ID] });
    if (hits && hits.length > 0) return;
    placeDraftMarker(e.lngLat.lng, e.lngLat.lat);
    renderTarget();
  };

  // Keyboard ----------------------------------------------------------------

  const onKeyDown = (e) => {
    if (e.key === 'Escape' && draftMarker) {
      cancel();
      e.preventDefault();
    } else if (e.key === 'Enter' && draftMarker && !saveBtn.disabled) {
      const tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      save();
      e.preventDefault();
    }
  };

  // Boot --------------------------------------------------------------------

  const loadQueue = async () => {
    try {
      const resp = await fetch(QUEUE_URL);
      if (!resp.ok) {
        showError(`Could not load queue (HTTP ${resp.status}).`);
        return;
      }
      const data = await resp.json();
      queue = data.queue || [];
      allResorts = data.all_resorts || [];
      renderRemaining();
      renderQueue();
      if (queue.length > 0) selectTarget(queue[0]);
      else renderTarget();
    } catch (err) {
      showError(`Could not load queue: ${err.message || err}`);
    }
  };

  // Wire up --------------------------------------------------------------------

  saveBtn.addEventListener('click', save);
  cancelBtn.addEventListener('click', cancel);
  skipBtn.addEventListener('click', skip);
  searchInput.addEventListener('input', onSearch);
  searchInput.addEventListener('focus', onSearchFocus);
  pasteInput.addEventListener('input', onPasteInput);
  // Hide the dropdown when focus moves away — small delay so the click on a
  // result li still registers (focus leaves the input the moment we click).
  searchInput.addEventListener('blur', () => {
    setTimeout(() => { searchResultsEl.hidden = true; }, 150);
  });
  document.addEventListener('keydown', onKeyDown);

  // Hide the page-level header search (which covers regions + resorts) so
  // the operator has one search affordance — the panel's resort-only one.
  // Restore it if anything else removes the panel later.
  const headerSearchPill = document.getElementById('search-pill');
  if (headerSearchPill) headerSearchPill.style.display = 'none';

  if (typeof MAP_READY_PROMISE !== 'undefined') {
    MAP_READY_PROMISE.then(() => {
      enterEditModeVisuals();
      refreshResortsLayer();
      if (MAP) {
        MAP.on('click', onMapClick);
        // Re-apply visibility tweaks + re-add resort points whenever the
        // style changes. ``styledata`` is the reliable signal across
        // setStyle() swaps in MapLibre 4.x — see static/js/map.js, where
        // the regions source is also re-installed on this event. The
        // handler is idempotent (setLayoutProperty / re-fetching the
        // resort layer is safe when it has already happened).
        MAP.on('styledata', () => {
          if (
            MAP.getLayer('regions-fill') &&
            MAP.getLayoutProperty('regions-fill', 'visibility') !== 'none'
          ) {
            enterEditModeVisuals();
          }
          // Re-add the points if the style swap dropped them.
          if (!MAP.getLayer(LAYER_ID)) {
            refreshResortsLayer();
          }
        });
      }
    });
  }

  loadQueue();
})();
