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
 * Two modes share one panel: editing a catalogue row (pick a resort, place
 * its pin, Save) and creating one ("New resort" — the same placement flow
 * plus Name/Canton inputs, POSTing to the create endpoint, which derives
 * the parent region from the pin). ``createMode`` is the switch; the two
 * are mutually exclusive.
 *
 * Placement
 * ---------
 * Click the map to drop a draggable ``maplibregl.Marker``, then drag to
 * refine. This deliberately does NOT use the shared centre pin
 * (window.PlacePicker) the favourite-create and field-observation flows
 * position with, where the pin is fixed on screen and the map pans
 * underneath it. That surface exists because a dragged pin is occluded by
 * the finger placing it — a touch problem. This is a staff tool driven with
 * a mouse on a desktop, where the trade runs the other way: a marker is
 * anchored to its coordinate, so zooming in to check the placement keeps
 * the pin locked to the spot it marks, instead of leaving the pin on screen
 * while the ground moves under it.
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
  const CREATE_URL                = panel.dataset.createUrl;
  const RESORTS_GEOJSON_URL       = panel.dataset.resortsGeojsonUrl;

  const csrfTokenInput            = panel.querySelector('input[name="csrfmiddlewaretoken"]');
  const CSRF_TOKEN                = csrfTokenInput ? csrfTokenInput.value : '';

  const remainingEl               = document.getElementById('edit-resorts-remaining');
  const queueListEl               = document.getElementById('edit-resorts-queue');
  const targetEl                  = document.getElementById('edit-resorts-target');
  const saveBtn                   = document.getElementById('edit-resorts-save');
  const cancelBtn                 = document.getElementById('edit-resorts-cancel');
  const errorEl                   = document.getElementById('edit-resorts-error');
  const statusEl                  = document.getElementById('edit-resorts-status');
  const searchInput               = document.getElementById('edit-resorts-search');
  const hideSetInput              = document.getElementById('edit-resorts-hide-set');
  const pasteInput                = document.getElementById('edit-resorts-paste');
  const detailsEl                 = document.getElementById('edit-resorts-details');
  const newBtn                    = document.getElementById('edit-resorts-new');
  const newFieldsEl               = document.getElementById('edit-resorts-new-fields');
  const newNameInput              = document.getElementById('edit-resorts-new-name');
  const newCantonInput            = document.getElementById('edit-resorts-new-canton');

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
  // Create mode (``New resort``) is the same placement flow with no row
  // behind it: no ``currentTarget``, the Name/Canton inputs visible, and
  // Save POSTing to the create endpoint. The two are mutually exclusive —
  // picking a catalogue row leaves create mode, and vice versa.
  let createMode         = false;
  let draftMarker        = null; // MapLibre Marker, draggable.
  let selectedRegionFid  = null; // Numeric feature id of the highlighted region.
  let saveInFlight       = false; // True between POST and its response.
  let statusTimer        = null;  // Handle clearing the save confirmation.

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

  // Save feedback ------------------------------------------------------------
  //
  // A save is a network round trip whose only other visible effect is the
  // readout's "Current" row catching up with "Draft" — easy to miss,
  // and indistinguishable from nothing having happened. So the button says
  // what it is doing while it does it, and a confirmation line (aria-live,
  // for the same reason) reports the outcome afterwards.

  // The button's resting label, read from the template so the i18n string
  // stays server-side.
  const SAVE_LABEL = saveBtn.textContent.trim();

  const clearStatus = () => {
    if (statusTimer !== null) {
      window.clearTimeout(statusTimer);
      statusTimer = null;
    }
    statusEl.textContent = '';
    statusEl.classList.add('hidden');
  };

  // Show a confirmation that fades out on its own — it reports a completed
  // action, so it should not linger over the next one.
  const showStatus = (msg) => {
    clearStatus();
    statusEl.textContent = msg;
    statusEl.classList.remove('hidden');
    statusTimer = window.setTimeout(clearStatus, 4000);
  };

  // Mark the save as in flight. The disabled state is re-derived by
  // renderTarget() (which a mid-request pan can trigger), so ``saveInFlight``
  // is the flag both paths read rather than a one-off ``disabled = true``
  // that the next 'moveend' would undo.
  const setSaving = (saving) => {
    saveInFlight = saving;
    saveBtn.textContent = saving ? 'Saving…' : SAVE_LABEL;
    renderTarget();
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

  // The draft pin's coordinate as the readout renders it, or a dash.
  const draftCoordsText = () => {
    if (!draftMarker) return '—';
    const ll = draftMarker.getLngLat();
    return fmtCoord(ll.lat, ll.lng);
  };

  // Create mode's readout. There is no "Current" row to show (nothing is
  // stored yet) and no region either — the server derives it from the pin
  // on save, so the row says where it will come from rather than guessing
  // client-side and risking a different answer to the authoritative one.
  //
  // A disabled Save says nothing about why, so the requirements are named
  // in a hint line under the readout and struck through as they are met.
  const renderCreateTarget = () => {
    const typedName = newNameInput ? newNameInput.value.trim() : '';
    const typedCanton = newCantonInput ? newCantonInput.value.trim() : '';
    // Canton is deliberately absent from this list: blank means "inherit
    // from the region the pin lands in", which is a valid way to save.
    const missing = [];
    if (!typedName)   missing.push('a name');
    if (!draftMarker) missing.push('a pin');
    const hint = missing.length > 0
      ? `Needs ${missing.join(' and ')}.`
      : 'Ready to save.';
    targetEl.innerHTML = `
      <p class="font-semibold text-slate-900">${escapeHtml(typedName || 'New resort')}</p>
      <p class="text-xs text-slate-500">Region from the pin${typedCanton ? ` · ${escapeHtml(typedCanton.toUpperCase())}` : ' · canton from the region'}</p>
      <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        <dt class="text-slate-500">Draft</dt>
        <dd class="font-mono ${draftMarker ? 'text-amber-700' : 'text-slate-400'}">${escapeHtml(draftCoordsText())}</dd>
      </dl>
      <p class="mt-1 text-xs ${missing.length > 0 ? 'text-slate-500' : 'text-emerald-700'}">${escapeHtml(hint)}</p>
    `;
    // Name is the only input the endpoint cannot source elsewhere, so it
    // and the pin are what Save waits for.
    saveBtn.disabled = saveInFlight || !draftMarker || !typedName;
    // Cancel leaves create mode, which is worth offering even before a
    // pin exists — it is the way back out of the mode.
    cancelBtn.disabled = saveInFlight;
  };

  const renderTarget = () => {
    if (createMode) {
      renderCreateTarget();
      return;
    }
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
    const draftCoords = draftCoordsText();
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
    saveBtn.disabled = saveInFlight || !draftMarker;
    cancelBtn.disabled = saveInFlight || !draftMarker;
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
    MAP.fitBounds(featureBBoxOf(feature), {
      padding: { top: 60, right: 380, bottom: 60, left: 60 }, // panel is 360px wide
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

  // Create mode -------------------------------------------------------------
  //
  // "New resort" is the edit flow with the row missing: same map click /
  // paste placement, same details form, same Save button. Only two things
  // differ — the panel asks for the Name and Canton it has nothing to read
  // them from, and Save posts to the create endpoint, which derives the
  // parent region from the pin.

  const setCreateFieldsVisible = (visible) => {
    if (!newFieldsEl) return;
    // The container is ``grid`` when shown; ``hidden`` when not (Tailwind's
    // ``hidden`` sets display:none, which would win over the grid class).
    newFieldsEl.classList.toggle('hidden', !visible);
    newFieldsEl.classList.toggle('grid', visible);
    if (newBtn) newBtn.setAttribute('aria-pressed', visible ? 'true' : 'false');
  };

  const enterCreateMode = () => {
    if (createMode) return;
    createMode = true;
    // Leave any selected row behind — a create has no row, and leaving the
    // old one highlighted would make Save look like it edits that resort.
    currentTarget = null;
    clearSelectedRegion();
    removeDraftMarker();
    clearError();
    clearStatus();
    clearDetailsForm();
    if (newNameInput) newNameInput.value = '';
    if (newCantonInput) newCantonInput.value = '';
    setCreateFieldsVisible(true);
    if (newNameInput) newNameInput.focus();
    renderResortsList();
    renderTarget();
  };

  const exitCreateMode = () => {
    if (!createMode) return;
    createMode = false;
    setCreateFieldsVisible(false);
    if (newNameInput) newNameInput.value = '';
    if (newCantonInput) newCantonInput.value = '';
    removeDraftMarker();
    clearDetailsForm();
  };

  // Selection ---------------------------------------------------------------

  const selectTarget = (entry) => {
    // Picking a catalogue row is the way out of create mode — the two
    // states are mutually exclusive (see ``createMode``).
    exitCreateMode();
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
    clearStatus();
    removeDraftMarker();
    // Highlight the parent region in both the placed-pin and unplaced
    // cases — gives the operator a visual confirmation of which region
    // their resort lives in. Idempotent on same-region (the
    // setFeatureState writes the same value back).
    setSelectedRegion(entry.region_id);
    writeDetailsForm(entry.details);
    // If the resort already has coords, pre-populate a draft marker so the
    // operator can drag-to-refine without first clicking-to-place.
    if (entry.latitude != null && entry.longitude != null) {
      placeDraftMarker(entry.longitude, entry.latitude);
      if (typeof MAP !== 'undefined' && MAP) {
        if (isRegionChange) {
          // Crossed regions — frame on the new pin at zoom 12. flyTo
          // animates both pan and zoom.
          MAP.flyTo({ center: [entry.longitude, entry.latitude], zoom: 12 });
        } else {
          // Same region — preserve the current zoom level, just pan
          // so the new pin sits roughly centred. panTo animates pan
          // only, leaving zoom untouched.
          MAP.panTo([entry.longitude, entry.latitude]);
        }
      }
    } else if (isRegionChange) {
      // Unplaced resort in a new region — fit the map to that region's
      // polygon so the operator can see the area before clicking to
      // drop a pin. Without this, picking an unplaced resort leaves
      // the view at the previous frame (typically the whole-Switzerland
      // framing the map booted with).
      fitMapToRegion(entry.region_id);
    }
    // Same-region unplaced selection: the operator's view is already
    // good (they were just placing a pin in this region); do nothing.
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

  // Draft marker ------------------------------------------------------------
  //
  // The draft marker's lifetime *is* the pin-positioning phase — Save and
  // Cancel are enabled only while it exists — so it is also the window in
  // which the map is cleared down to the basemap
  // (window.PlacementFocus, static/js/map_placement_focus.js). Choosing
  // where to click keeps full region context; refining the dropped pin
  // does not. One consequence worth knowing: with the resort-points layer
  // hidden, ``onMapClick``'s "did this tap hit an existing resort?" query
  // finds nothing, so while a draft is live every map click repositions
  // the draft rather than jumping to another resort — pick the other
  // resort from the panel list instead.

  const placeDraftMarker = (lng, lat) => {
    removeDraftMarker();
    if (typeof MAP === 'undefined' || !MAP) return;
    draftMarker = new maplibregl.Marker({ draggable: true, color: '#f59e0b' })
      .setLngLat([lng, lat])
      .addTo(MAP);
    draftMarker.on('dragend', () => {
      renderTarget();
    });
    window.PlacementFocus?.enter();
  };

  const removeDraftMarker = () => {
    if (draftMarker) {
      draftMarker.remove();
      draftMarker = null;
    }
    if (pasteInput) pasteInput.value = '';
    window.PlacementFocus?.exit();
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
    if (!currentTarget && !createMode) return;
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

  // Decode a failed response into the message to show, marking any
  // per-field details the server named. Shared by save and create, which
  // return the same error bodies.
  const errorDetailFrom = async (resp) => {
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
    return detail;
  };

  // The catalogue's sort order, mirroring the queue endpoint's
  // ``order_by("region__region_id", "name")`` so a created row lands where
  // a reload would put it.
  const catalogueOrder = (a, b) =>
    a.region_id.localeCompare(b.region_id) || a.name.localeCompare(b.name);

  // Project a create/save response onto a catalogue entry. The endpoints
  // answer with a superset of the entry shape (``_resort_save_payload``),
  // so this is a pick rather than a translation.
  const catalogueEntryFrom = (data) => ({
    id:           data.id,
    name:         data.name,
    region_id:    data.region_id,
    region_name:  data.region_name,
    canton:       data.canton,
    latitude:     data.latitude,
    longitude:    data.longitude,
    has_coords:   data.has_coords,
    needs_review: data.needs_review,
    details:      data.details,
  });

  // Create — the same round trip as ``save`` against a row that does not
  // exist yet. On success the new entry is spliced into the catalogue and
  // selected, so the operator lands in the ordinary edit flow on the
  // resort they just added.
  const createResort = async () => {
    if (!draftMarker || saveInFlight) return;
    const name = newNameInput ? newNameInput.value.trim() : '';
    // Blank canton is sent as-is: the endpoint reads it off the region
    // the pin lands in, which the panel cannot know until it answers.
    const canton = newCantonInput ? newCantonInput.value.trim().toUpperCase() : '';
    if (!name) return;
    const ll = draftMarker.getLngLat();
    setSaving(true);
    clearError();
    clearStatus();
    clearFieldErrors();
    try {
      const resp = await fetch(CREATE_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CSRF_TOKEN,
        },
        body: JSON.stringify({
          name: name,
          canton: canton,
          latitude: ll.lat,
          longitude: ll.lng,
          details: readDetailsForm(),
        }),
      });
      if (!resp.ok) {
        const detail = await errorDetailFrom(resp);
        setSaving(false);
        showError(`Create failed: ${detail}`);
        return;
      }
      const data = await resp.json();
      const entry = catalogueEntryFrom(data);
      allResorts.push(entry);
      allResorts.sort(catalogueOrder);
      setSaving(false);
      // selectTarget() leaves create mode, drops a marker on the saved
      // point and repopulates the details form from the response — the
      // operator continues editing the resort they just created.
      selectTarget(entry);
      showStatus(
        `Created ${data.name} in ${data.region_id} at ` +
        `${fmtCoord(data.latitude, data.longitude)}.`
      );
      renderRemaining();
      refreshResortsLayer();
    } catch (err) {
      setSaving(false);
      showError(`Create failed: ${err.message || err}`);
    }
  };

  const save = async () => {
    if (createMode) {
      await createResort();
      return;
    }
    if (!currentTarget || !draftMarker || saveInFlight) return;
    const ll = draftMarker.getLngLat();
    const url = SAVE_URL_TEMPLATE.replace('__ID__', String(currentTarget.id));
    setSaving(true);
    clearError();
    clearStatus();
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
          latitude: ll.lat,
          longitude: ll.lng,
          details: readDetailsForm(),
        }),
      });
      if (!resp.ok) {
        const detail = await errorDetailFrom(resp);
        setSaving(false);
        showError(`Save failed: ${detail}`);
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
      // Keep the just-saved resort selected so the operator gets
      // visual confirmation (panel readout still shows the resort,
      // pill flips to Set on the list row). Patch ``currentTarget``
      // to the post-save shape so re-clicking it doesn't read stale
      // pre-save values. The auto-advance to the "next in queue"
      // that SNOW-74 had is gone — the operator picks the next row
      // themselves.
      removeDraftMarker();
      if (currentTarget && currentTarget.id === data.id) {
        currentTarget = catIdx !== -1 ? allResorts[catIdx] : currentTarget;
        writeDetailsForm(data.details);
      }
      setSaving(false);
      showStatus(`Saved ${data.name} at ${fmtCoord(data.latitude, data.longitude)}.`);
      renderResortsList();
      renderRemaining();
      refreshResortsLayer();
    } catch (err) {
      setSaving(false);
      showError(`Save failed: ${err.message || err}`);
    }
  };

  // Cancel discards the draft pin, leaving the resort selected — the
  // operator is usually rejecting a mis-click, not the whole row.
  // Removing the marker also restores every overlay PlacementFocus hid,
  // which brings tap-a-pin-to-select back.
  // In create mode there is no row to fall back to, so Cancel leaves the
  // mode altogether rather than stranding the operator in an empty form.
  const cancel = () => {
    const wasCreating = createMode;
    exitCreateMode();
    removeDraftMarker();
    clearError();
    clearStatus();
    if (wasCreating) renderResortsList();
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

  const onMapClick = (e) => {
    if (!currentTarget && !createMode) return;
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
    // Escape also backs out of create mode before a pin is placed —
    // otherwise the only way out of the mode is the Cancel button.
    if (e.key === 'Escape' && (draftMarker || createMode)) {
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
  // "New resort" toggles: pressing it while already creating backs out,
  // matching the aria-pressed state the button advertises.
  if (newBtn) {
    newBtn.addEventListener('click', () => {
      if (createMode) {
        cancel();
      } else {
        enterCreateMode();
      }
    });
  }
  // Name/canton gate the Save button, so the readout re-derives on every
  // keystroke (it also echoes the typed name back).
  for (const input of [newNameInput, newCantonInput]) {
    if (input) input.addEventListener('input', renderTarget);
  }
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
