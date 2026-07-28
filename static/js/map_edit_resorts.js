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
 * Placement
 * ---------
 * Positioning uses the same shared centre pin as the favourite-create and
 * field-observation flows (window.PlacePicker, static/js/place_picker.js):
 * the pin is fixed on screen and the operator pans the map underneath it,
 * with the coordinate read off the map centre on every 'moveend'. It
 * replaces the draggable ``maplibregl.Marker`` this file used to drop on
 * click — a dragged pin is occluded by the cursor/finger placing it, and
 * two placement mechanics in one app is one too many.
 *
 * A click on the map still "places" in the sense the operator expects: it
 * pans the clicked point under the pin rather than moving a marker to it.
 *
 * Panel-side consequences worth knowing:
 *   - Placement is armed for as long as a resort is selected, so Save is
 *     live from the moment of selection (the point under the pin is always
 *     a valid coordinate). Cancel — and Escape — clears the selection.
 *   - While armed, PlacePicker clears the map down to the basemap
 *     (window.PlacementFocus), which hides the edit-resorts points layer
 *     along with everything else. Tapping a pin on the map to jump to that
 *     resort therefore works only with nothing selected; otherwise pick the
 *     row from the panel list.
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
  const errorEl                   = document.getElementById('edit-resorts-error');
  const searchInput               = document.getElementById('edit-resorts-search');
  const hideSetInput              = document.getElementById('edit-resorts-hide-set');
  const pasteInput                = document.getElementById('edit-resorts-paste');
  const detailsEl                 = document.getElementById('edit-resorts-details');

  // Every hand-curated metadata input in the panel, keyed by the Resort
  // field name it edits. The template is the source of the field list
  // (``data-resort-field``) so adding one there needs no change here —
  // it only has to be in regions.forms.RESORT_DETAIL_FIELDS server-side.
  const detailInputs = new Map(
    Array.from(
      panel.querySelectorAll('[data-resort-field]'),
      (input) => [input.dataset.resortField, input],
    ),
  );

  // ``hide-set`` toggle preference is persisted across reloads — the
  // operator commonly works through unset/review rows over multiple
  // sessions and shouldn't have to re-flip the toggle each time.
  const HIDE_SET_STORAGE_KEY = 'snowdesk.edit_resorts.hide_set';

  // State.
  //
  // ``allResorts`` is the full catalogue rendered in the side panel
  // — the operator works through the list manually, so there's no
  // separate "queue" of unset rows any more (SNOW-85 simplified the
  // workflow). ``currentTarget`` is the row the operator most
  // recently clicked. ``subRegionLabels`` is a {prefix: name} map
  // (e.g. {"CH-41": "Lower Valais"}) used for the L2 section headers
  // in the resorts list.
  let allResorts         = [];   // Full catalogue, sorted by region+name.
  let subRegionLabels    = {};   // {prefix: name} for L2 section headers.
  let currentTarget      = null; // The selected resort entry, or null.
  let draftCoord         = null; // {lat, lng} under the centre pin, or null.
  let selectedRegionFid  = null; // Numeric feature id of the highlighted region.

  // Format a coord pair to 5 decimal places (≈1m precision in Switzerland).
  const fmtCoord = (lat, lng) =>
    `${lat.toFixed(5)}, ${lng.toFixed(5)}`;

  // The error line is hidden with Tailwind's ``hidden`` utility, so the
  // ``hidden`` *attribute* alone would not reveal it (display:none from
  // the class wins) — toggle the class.
  const showError = (msg) => {
    errorEl.textContent = msg;
    errorEl.classList.remove('hidden');
  };

  const clearError = () => {
    errorEl.textContent = '';
    errorEl.classList.add('hidden');
  };

  // Header counter — "{set count} / {total} set". Replaces the
  // SNOW-74 queue-remaining counter; with the manual workflow the
  // operator wants progress feedback (how much of the catalogue is
  // already placed) rather than queue depth.
  const renderRemaining = () => {
    const total = allResorts.length;
    const set = allResorts.reduce((acc, r) => acc + (r.has_coords ? 1 : 0), 0);
    remainingEl.textContent = `${set} / ${total} set`;
  };

  // Status pill for a catalogue row. Three states with distinct colour
  // coding so the operator can see at a glance which resorts are
  // already placed vs still need work. Shared with the search filter
  // (when active, the same rows render with the same pills).
  const statusBadge = (m) => {
    if (m.needs_review) {
      return { label: 'Review', cls: 'bg-red-100 text-red-800' };
    }
    if (m.has_coords) {
      return { label: 'Set', cls: 'bg-emerald-100 text-emerald-800' };
    }
    return { label: 'Unset', cls: 'bg-amber-100 text-amber-800' };
  };

  // L2 prefix — the first 5 chars of an SLF region_id (e.g.
  // "CH-4115" → "CH-41") is the EAWS sub-region key. Used to insert
  // section headers between L2 groups in the resorts list.
  const l2Of = (regionId) => regionId.slice(0, 5);

  // "Set" rows are the ones the operator considers correct
  // (geocoded and not flagged for review). When ``hide-set`` is on
  // we drop these from the rendered list so the operator can sweep
  // through only the rows that still need attention.
  const isSet = (entry) => entry.has_coords && !entry.needs_review;

  // Render the full resort catalogue — sorted by region_id then name
  // server-side (SNOW-85) — with a Set/Unset/Review pill on every
  // row and a section header before each L2 group, labelled with the
  // L2 region's name (e.g. "Lower Valais" before all CH-411x rows).
  // The search input and the hide-set toggle filter this same list
  // in place; headers are emitted for whichever L2 groups have
  // surviving rows after filtering.
  //
  // The DOM ID is still ``edit-resorts-queue`` for minimal-diff
  // reasons — the element holds a list, not a queue, but renaming
  // the ID would churn the panel template + every CSS selector for
  // no real benefit.
  const renderResortsList = () => {
    queueListEl.innerHTML = '';
    const filter = searchInput.value.trim().toLowerCase();
    const hideSet = !!(hideSetInput && hideSetInput.checked);
    const rows = allResorts.filter((r) => {
      if (filter && !r.name.toLowerCase().includes(filter)) return false;
      if (hideSet && isSet(r)) return false;
      return true;
    });
    if (rows.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'italic text-slate-400';
      let msg = 'No resorts loaded.';
      if (filter && hideSet) msg = 'No unset matches.';
      else if (filter)       msg = 'No matches.';
      else if (hideSet)      msg = 'All resorts are set — toggle off to see the rest.';
      empty.textContent = msg;
      queueListEl.appendChild(empty);
      return;
    }
    let lastL2 = null;
    for (const entry of rows) {
      const l2 = l2Of(entry.region_id);
      if (l2 !== lastL2) {
        // Section header for the L2 area — shows the human-readable
        // sub-region name with the prefix code as a subtitle for
        // operators who think in codes. The first header gets no top
        // margin/border via the first:* utilities so it sits flush
        // with the list label.
        const label = subRegionLabels[l2] || l2;
        const header = document.createElement('li');
        header.className = 'mt-3 flex items-baseline justify-between border-t border-slate-200 px-2 pt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 first:mt-0 first:border-t-0 first:pt-0';
        const labelSpan = document.createElement('span');
        labelSpan.textContent = label;
        header.appendChild(labelSpan);
        const codeSpan = document.createElement('span');
        codeSpan.className = 'font-mono text-[9px] text-slate-300';
        codeSpan.textContent = l2;
        header.appendChild(codeSpan);
        queueListEl.appendChild(header);
        lastL2 = l2;
      }

      const li = document.createElement('li');
      const isCurrent = currentTarget && currentTarget.id === entry.id;
      li.className = [
        'flex cursor-pointer items-center justify-between gap-2 rounded px-2 py-1',
        isCurrent ? 'bg-sky-100 font-semibold text-sky-900' : 'hover:bg-slate-100',
      ].join(' ');
      li.dataset.resortId = String(entry.id);

      const left = document.createElement('span');
      left.className = 'flex items-baseline gap-2 truncate';
      const name = document.createElement('span');
      name.className = 'truncate';
      name.textContent = entry.name;
      left.appendChild(name);
      const region = document.createElement('span');
      region.className = 'shrink-0 text-xs text-slate-400';
      region.textContent = entry.region_id;
      left.appendChild(region);
      li.appendChild(left);

      const badge = statusBadge(entry);
      const right = document.createElement('span');
      right.className = `shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${badge.cls}`;
      right.textContent = badge.label;
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
    const draftCoords = draftCoord
      ? fmtCoord(draftCoord.lat, draftCoord.lng)
      : '—';
    targetEl.innerHTML = `
      <p class="font-semibold text-slate-900">${escapeHtml(t.name)}</p>
      <p class="text-xs text-slate-500">${escapeHtml(t.region_name)} (${escapeHtml(t.region_id)}) · ${escapeHtml(t.canton)}</p>
      <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        <dt class="text-slate-500">Current</dt>
        <dd class="font-mono text-slate-700">${escapeHtml(currentCoords)}</dd>
        <dt class="text-slate-500">Under pin</dt>
        <dd class="font-mono ${draftCoord ? 'text-amber-700' : 'text-slate-400'}">${escapeHtml(draftCoords)}</dd>
      </dl>
    `;
    saveBtn.disabled = !draftCoord;
    cancelBtn.disabled = !currentTarget;
  };

  // Details form -------------------------------------------------------------
  //
  // The panel's metadata inputs are populated from the catalogue entry's
  // ``details`` object (served by the queue endpoint) and read back as the
  // ``details`` object the save endpoint validates. Values travel as
  // strings; the server coerces and validates them, so nothing here needs
  // to know which fields are numeric.

  // Marks an input the server rejected. The inputs are not inside a
  // <form>, so there is no native validation bubble to lean on —
  // setCustomValidity() would set state nothing ever renders.
  const ERROR_CLASS = 'border-form-error';

  const clearFieldErrors = () => {
    for (const [, input] of detailInputs) input.classList.remove(ERROR_CLASS);
  };

  const writeDetailsForm = (details) => {
    let anyFilled = false;
    for (const [field, input] of detailInputs) {
      const value = details && details[field] != null ? details[field] : '';
      input.value = String(value);
      input.classList.remove(ERROR_CLASS);
      if (input.value !== '') anyFilled = true;
    }
    // Auto-open the section when there is already something to see —
    // otherwise a resort with a filled-in record looks empty until the
    // operator thinks to expand it.
    if (detailsEl) detailsEl.open = anyFilled;
  };

  const readDetailsForm = () => {
    const details = {};
    for (const [field, input] of detailInputs) {
      details[field] = input.value.trim();
    }
    return details;
  };

  const clearDetailsForm = () => {
    writeDetailsForm(null);
    if (detailsEl) detailsEl.open = false;
  };

  // Surface the server's per-field validation errors (the ``fields`` object
  // in a 400 invalid_details body) on the inputs that produced them, so the
  // operator can see which of ten fields is at fault without decoding one
  // summary line.
  const showFieldErrors = (fields) => {
    clearFieldErrors();
    const messages = [];
    for (const [field, errors] of Object.entries(fields || {})) {
      const text = Array.isArray(errors) ? errors.join(' ') : String(errors);
      const input = detailInputs.get(field);
      if (input) input.classList.add(ERROR_CLASS);
      messages.push(`${field}: ${text}`);
    }
    if (detailsEl && messages.length > 0) detailsEl.open = true;
    return messages.join(' · ');
  };

  const escapeHtml = (s) =>
    String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  // Region focus ------------------------------------------------------------
  //
  // The L4 region polygons are loaded by static/js/map.js into the
  // ``regions`` source and indexed at module scope as
  // ``FEATURE_BY_REGION_ID[regionId]``. We re-use that lookup to frame
  // the map on the resort's parent region whenever the operator picks
  // an unplaced resort, and to highlight the matching outline so
  // "select" is literal as well as positional. The regions-line
  // ``line-opacity`` paint expression is rewritten by
  // ``enterEditModeVisuals`` to honour an ``edit-selected`` feature
  // state, so toggling that state is all that's needed to swap which
  // region's outline is bright.

  // Compute the lng/lat bounding box of a GeoJSON Polygon or MultiPolygon
  // feature. Mirrors the equivalent helper in static/js/map.js, which is
  // closure-scoped inside the main IIFE there and not exported. The
  // duplication is small (~12 lines) and avoids reshuffling map.js.
  const featureBBoxOf = (feature) => {
    const coords = feature.geometry.type === 'Polygon'
      ? feature.geometry.coordinates
      : feature.geometry.coordinates.flat();
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

  // Look up the region feature for an SLF region_id. Returns ``null``
  // when the regions source isn't ready yet or the region has no
  // boundary — callers must tolerate that.
  const regionFeatureFor = (regionId) => {
    if (typeof FEATURE_BY_REGION_ID === 'undefined' || !FEATURE_BY_REGION_ID) {
      return null;
    }
    return FEATURE_BY_REGION_ID[regionId] || null;
  };

  const fitMapToRegion = (regionId) => {
    if (typeof MAP === 'undefined' || !MAP) return;
    const feature = regionFeatureFor(regionId);
    if (!feature || !feature.geometry) return;
    // Modest padding and a maxZoom cap — small regions otherwise zoom
    // past the city-detail level we want for placing a pin.
    //
    // The padding is uniform even though the panel covers the right 360px:
    // MapLibre keeps a fitBounds padding as the map's padding, and an
    // asymmetric one moves the map centre away from the placement pin's
    // screen position (see armPlacement). Symmetric padding puts the
    // region's centre under the pin, which is what the operator is about
    // to aim with, at the cost of the region's right edge sitting behind
    // the panel until they pan.
    MAP.fitBounds(featureBBoxOf(feature), {
      padding: 60,
      maxZoom: 11,
      duration: 400,
    });
  };

  const clearSelectedRegion = () => {
    if (selectedRegionFid === null) return;
    if (typeof MAP === 'undefined' || !MAP) {
      selectedRegionFid = null;
      return;
    }
    try {
      MAP.setFeatureState(
        { source: 'regions', id: selectedRegionFid },
        { 'edit-selected': false },
      );
    } catch (_) { /* source may not be installed yet */ }
    selectedRegionFid = null;
  };

  const setSelectedRegion = (regionId) => {
    clearSelectedRegion();
    if (typeof MAP === 'undefined' || !MAP) return;
    const feature = regionFeatureFor(regionId);
    if (!feature || feature.id === undefined) return;
    try {
      MAP.setFeatureState(
        { source: 'regions', id: feature.id },
        { 'edit-selected': true },
      );
      selectedRegionFid = feature.id;
    } catch (_) { /* source not ready — silently skip */ }
  };

  // Selection ---------------------------------------------------------------

  const selectTarget = (entry) => {
    // Track the region we were on *before* this selection so we can
    // tell whether we're crossing region boundaries. Same-region
    // navigation must preserve the operator's manual zoom — they
    // commonly zoom in to place a precise pin on resort A then click
    // resort B in the same region; flinging them back to the region
    // bbox would force them to re-zoom every time.
    const previousRegionId = currentTarget ? currentTarget.region_id : null;
    const isRegionChange = entry.region_id !== previousRegionId;

    currentTarget = entry;
    clearError();
    // Highlight the parent region in both the placed-pin and unplaced
    // cases. The highlight is only actually on screen before placement
    // arms (PlacePicker clears every overlay), but it survives in
    // feature-state and comes back when the selection is cleared.
    setSelectedRegion(entry.region_id);
    writeDetailsForm(entry.details);

    if (entry.latitude != null && entry.longitude != null) {
      // Already placed — frame the saved point, then arm the picker on it
      // so the coordinate under the pin starts out as the stored one and
      // any pan from here is a deliberate correction.
      if (typeof MAP !== 'undefined' && MAP && isRegionChange) {
        // Crossed regions — frame on the resort at zoom 12. flyTo
        // animates both pan and zoom; arming afterwards re-centres
        // instantly on the same point, so the animation is not fought.
        MAP.flyTo({ center: [entry.longitude, entry.latitude], zoom: 12 });
      }
      // Same region — preserve the operator's manual zoom; armPlacement's
      // recentre pans to the new resort on its own.
      armPlacement([entry.longitude, entry.latitude]);
    } else {
      if (isRegionChange) {
        // Unplaced resort in a new region — fit the map to that region's
        // polygon so the pin starts somewhere sensible. Without this,
        // picking an unplaced resort leaves the view at the previous
        // frame (typically the whole-Switzerland framing the map booted
        // with).
        fitMapToRegion(entry.region_id);
      }
      // Nothing to recentre on: the pin holds wherever the map now is,
      // and the operator pans the village under it.
      armPlacement(null);
    }
    renderResortsList();
    renderTarget();
  };

  const selectTargetById = (id) => {
    // The catalogue carries every resort with full display fields
    // (region_name, canton, latitude, longitude) so a single lookup
    // suffices — see public/api.py::edit_resorts_queue.
    const entry = allResorts.find((e) => e.id === id);
    if (entry) selectTarget(entry);
  };

  // Placement ---------------------------------------------------------------
  //
  // Positioning is delegated to the shared centre pin
  // (window.PlacePicker, static/js/place_picker.js) — the same surface
  // the favourite-create and field-observation flows use. It is armed for
  // as long as a resort is selected and reports the coordinate under the
  // pin on every 'moveend'; ``draftCoord`` is that reading and is what
  // Save posts.
  //
  // Arming also clears the map down to the basemap
  // (window.PlacementFocus, via the picker), which hides the
  // edit-resorts points layer along with every other overlay. Selecting
  // another resort by tapping its pin therefore works only with nothing
  // currently selected — otherwise use the panel list.
  //
  // No ``occludedBy`` is passed: the panel is a full-height side rail, not
  // a bottom sheet, so it never covers the middle of the map and the pin
  // stays plainly centred.

  const armPlacement = (recenterTo) => {
    if (typeof MAP === 'undefined' || !MAP) return;
    // The pin is drawn at the map container's geometric centre, and the
    // picker reads the coordinate from MAP.getCenter() — which is the
    // *padded* centre. Any leftover framing padding (fitMapToRegion's,
    // say) would put those two points in different places and the readout
    // would quietly describe somewhere the pin is not. Zero it first; the
    // picker snapshots this as its base and restores it on deactivate.
    MAP.setPadding({ top: 0, right: 0, bottom: 0, left: 0 });
    window.PlacePicker?.activate({
      recenterTo: recenterTo || undefined,
      onChange: (lat, lng) => {
        draftCoord = { lat: lat, lng: lng };
        renderTarget();
      },
    });
  };

  const disarmPlacement = () => {
    window.PlacePicker?.deactivate();
    draftCoord = null;
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

  // A pasted pair moves the map so the point lands under the pin — the
  // picker's 'moveend' read then updates ``draftCoord`` for free, so
  // there is nothing to write here beyond the camera move.
  const onPasteInput = () => {
    if (!currentTarget) return;
    const parsed = parseLatLonString(pasteInput.value);
    if (!parsed) return;
    if (typeof MAP !== 'undefined' && MAP) {
      MAP.flyTo({ center: [parsed.lon, parsed.lat], zoom: 13 });
    }
    clearError();
  };

  // Save / cancel / skip ----------------------------------------------------

  const save = async () => {
    if (!currentTarget || !draftCoord) return;
    const url = SAVE_URL_TEMPLATE.replace('__ID__', String(currentTarget.id));
    saveBtn.disabled = true;
    clearError();
    clearFieldErrors();
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CSRF_TOKEN,
        },
        // GeoJSON uses [lon, lat]; MapLibre returns {lng, lat}; the wire
        // format here is keyed by name — no ordering ambiguity.
        body: JSON.stringify({
          latitude: draftCoord.lat,
          longitude: draftCoord.lng,
          details: readDetailsForm(),
        }),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const errBody = await resp.json();
          detail = errBody.detail || errBody.error || detail;
          // A rejected detail field names itself; mark the offending
          // inputs and put the per-field reasons in the error line.
          if (errBody.fields) {
            const fieldDetail = showFieldErrors(errBody.fields);
            if (fieldDetail) detail = fieldDetail;
          }
        } catch (_) { /* response body wasn't JSON */ }
        showError(`Save failed: ${detail}`);
        saveBtn.disabled = false;
        return;
      }
      const data = await resp.json();
      // Patch the in-memory catalogue so the just-saved row's pill
      // flips Unset → Set, and a subsequent search hit on the same
      // resort renders the post-save state (new region_id from
      // auto-rebind, new lat/lon, has_coords true) without a page
      // reload. Catalogue order is by name, so no re-sort is needed.
      const catIdx = allResorts.findIndex((r) => r.id === data.id);
      if (catIdx !== -1) {
        allResorts[catIdx] = {
          ...allResorts[catIdx],
          region_id:   data.region_id,
          region_name: data.region_name,
          latitude:    data.latitude,
          longitude:   data.longitude,
          has_coords:  true,
          needs_review: data.needs_review,
          details:     data.details,
        };
      }
      // Keep the just-saved resort selected — and placement armed — so
      // the operator gets visual confirmation (the readout's "Current"
      // row catches up with "Under pin") and can nudge and re-save
      // without re-selecting. Patch ``currentTarget`` to the post-save
      // shape so re-clicking it doesn't read stale pre-save values. The
      // auto-advance to the "next in queue" that SNOW-74 had is gone —
      // the operator picks the next row themselves.
      if (pasteInput) pasteInput.value = '';
      if (currentTarget && currentTarget.id === data.id) {
        currentTarget = catIdx !== -1 ? allResorts[catIdx] : currentTarget;
        writeDetailsForm(data.details);
      }
      renderResortsList();
      renderRemaining();
      renderTarget();
      refreshResortsLayer();
    } catch (err) {
      showError(`Save failed: ${err.message || err}`);
      saveBtn.disabled = false;
    }
  };

  // Cancel drops the whole selection rather than just the pin position:
  // with the picker armed there is no "no draft yet" state to fall back
  // to, and clearing the target is what brings the overlays — and with
  // them tap-a-pin-to-select — back.
  const cancel = () => {
    disarmPlacement();
    clearSelectedRegion();
    currentTarget = null;
    clearError();
    clearDetailsForm();
    renderResortsList();
    renderTarget();
  };

  // Search ------------------------------------------------------------------
  //
  // The search input filters the main resorts list in place — there
  // is no separate dropdown any more. Typing narrows the visible
  // rows; clearing the box restores the full list.
  const onSearch = () => {
    renderResortsList();
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
    // Dim every region outline to 0.2 except the one currently selected
    // for editing (feature-state ``edit-selected`` flipped on by
    // ``setSelectedRegion``), which goes to full opacity. The same
    // expression is re-applied on every basemap swap via the styledata
    // handler at the end of this file, so the highlight survives a
    // setStyle().
    try {
      MAP.setPaintProperty('regions-line', 'line-opacity', [
        'case',
        ['boolean', ['feature-state', 'edit-selected'], false], 1.0,
        0.2,
      ]);
    } catch (_) { /* layer not yet installed */ }
    // A slightly heavier stroke for the selected region helps it read
    // against the swisstopo_winter basemap's contour clutter. Preserve
    // SNOW-61's zoom-interpolation shape (interpolate at the top level,
    // case as stops) so non-selected outlines still scale sensibly with
    // zoom and don't drop sub-pixel at country view.
    try {
      MAP.setPaintProperty('regions-line', 'line-width', [
        'interpolate', ['linear'], ['zoom'],
        5, ['case', ['boolean', ['feature-state', 'edit-selected'], false], 3.0, 1.2],
        9, ['case', ['boolean', ['feature-state', 'edit-selected'], false], 2.5, 0.6],
      ]);
    } catch (_) { /* layer not yet installed */ }
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

  // A click no longer drops anything — the pin does not move. It pans the
  // clicked point under the pin instead, which keeps the mouse-driven
  // "click roughly there, then fine-tune" habit working on desktop while
  // the coordinate still comes from exactly one place: the map centre.
  const onMapClick = (e) => {
    if (!currentTarget) return;
    if (typeof MAP === 'undefined' || !MAP) return;
    // If the click hit a resort point, the layer-specific handler runs and
    // we don't want to also move the map. queryRenderedFeatures filters by
    // layer; if any feature is returned, bail. (While placement is armed
    // the layer is hidden, so this only fires with nothing selected —
    // which the guard above has already returned on.)
    const hits = MAP.queryRenderedFeatures(e.point, { layers: [LAYER_ID] });
    if (hits && hits.length > 0) return;
    MAP.panTo(e.lngLat);
  };

  // Keyboard ----------------------------------------------------------------

  const onKeyDown = (e) => {
    if (e.key === 'Escape' && currentTarget) {
      cancel();
      e.preventDefault();
    } else if (e.key === 'Enter' && draftCoord && !saveBtn.disabled) {
      const tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      save();
      e.preventDefault();
    }
  };

  // Boot --------------------------------------------------------------------

  // Load the catalogue and render. No auto-select on first load — the
  // operator picks the first resort themselves. (SNOW-74 auto-selected
  // the head of the unset queue; that flow is gone with the manual
  // workflow.)
  const loadCatalogue = async () => {
    try {
      const resp = await fetch(QUEUE_URL);
      if (!resp.ok) {
        showError(`Could not load resorts (HTTP ${resp.status}).`);
        return;
      }
      const data = await resp.json();
      allResorts = data.all_resorts || [];
      subRegionLabels = data.sub_regions || {};
      renderRemaining();
      renderResortsList();
      renderTarget();
    } catch (err) {
      showError(`Could not load resorts: ${err.message || err}`);
    }
  };

  // Wire up --------------------------------------------------------------------

  saveBtn.addEventListener('click', save);
  cancelBtn.addEventListener('click', cancel);
  searchInput.addEventListener('input', onSearch);
  pasteInput.addEventListener('input', onPasteInput);
  document.addEventListener('keydown', onKeyDown);

  // Hide-set toggle: restore prior state from localStorage on boot
  // (operators commonly leave this on across sessions while sweeping
  // through unset rows), and persist on every change. Re-render on
  // change so the list updates immediately.
  if (hideSetInput) {
    try {
      hideSetInput.checked =
        window.localStorage.getItem(HIDE_SET_STORAGE_KEY) === '1';
    } catch (_) { /* private mode / disabled storage — start unchecked */ }
    hideSetInput.addEventListener('change', () => {
      try {
        window.localStorage.setItem(
          HIDE_SET_STORAGE_KEY,
          hideSetInput.checked ? '1' : '0',
        );
      } catch (_) { /* swallow — toggle still works without persistence */ }
      renderResortsList();
    });
  }

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
            // The regions source has just been re-installed by
            // map.js, which means the previous feature-state was
            // wiped; re-apply the highlight for the active target so
            // the selected outline survives a basemap swap.
            if (currentTarget) setSelectedRegion(currentTarget.region_id);
          }
          // Re-add the points if the style swap dropped them.
          if (!MAP.getLayer(LAYER_ID)) {
            refreshResortsLayer();
          }
        });
      }
    });
  }

  loadCatalogue();
})();
