/*
 * static/js/map_edit_locations.js — SNOW-755 in-map location editor.
 *
 * Loaded only when the page is rendered with ?edit=locations AND the
 * request user is a superuser (home.html guards both the panel include
 * and this <script> tag). The API endpoints re-check the same thing —
 * this file is a UI tool, not the trust boundary.
 *
 * Everything that is logic rather than wiring lives in
 * ``map_edit_locations_core.js`` (``self.pwaEditLocationsCore``): the mode
 * machine, catalogue filtering and sorting, the three request payloads,
 * the link picker's exclusions and the paste parser. What stays here is
 * the DOM, the map and ``fetch`` — the parts jsdom cannot usefully assert.
 *
 * Hooks into the global ``MAP`` and ``MAP_READY_PROMISE`` declared at the
 * top of ``static/js/map.js`` (top-level let/const in classic scripts
 * share scope across <script> tags in the same document, so no window
 * export is needed).
 *
 * Placement
 * ---------
 * Click the map to drop a draggable ``maplibregl.Marker``, then drag to
 * refine — the same choice ``map_edit_resorts.js`` documents at length,
 * for the same reason. Deliberately NOT the shared centre pin
 * (``window.PlacePicker``) the favourite-create and field-observation
 * flows use: that exists because a dragged pin is occluded by the finger
 * placing it, which is a touch problem. This is a staff tool driven with
 * a mouse, where a marker anchored to its coordinate is the better trade —
 * zooming in to check a summit keeps the pin on the spot it marks.
 *
 * Strings
 * -------
 * The user-facing strings built here carry ``// i18n-allow`` with a
 * staff-only reason, matching ``map_edit_resorts.js``. The surface is
 * superuser-gated at the view, the template and every endpoint, and is
 * never rendered for a member of the public.
 *
 * Coordinate-ordering reminder:
 *   - DB columns / wire format:  latitude, longitude
 *   - GeoJSON:                   [longitude, latitude]
 *   - MapLibre marker:           marker.getLngLat() → {lng, lat}
 */

(function () {
  'use strict';

  const panel = document.getElementById('edit-locations-panel');
  if (!panel) return;

  const core = self.pwaEditLocationsCore;
  const MODE = core.MODE;

  const QUEUE_URL          = panel.dataset.queueUrl;
  const CREATE_URL         = panel.dataset.createUrl;
  const SAVE_URL_TEMPLATE  = panel.dataset.saveUrlTemplate;
  const LINK_URL_TEMPLATE  = panel.dataset.linkUrlTemplate;
  const UNLINK_URL_TEMPLATE = panel.dataset.unlinkUrlTemplate;

  const csrfTokenInput = panel.querySelector('input[name="csrfmiddlewaretoken"]');
  const CSRF_TOKEN     = csrfTokenInput ? csrfTokenInput.value : '';

  const countEl      = document.getElementById('edit-locations-count');
  const searchInput  = document.getElementById('edit-locations-search');
  const listEl       = document.getElementById('edit-locations-list');
  const targetEl     = document.getElementById('edit-locations-target');
  const placeFields  = document.getElementById('edit-locations-place-fields');
  const linkFields   = document.getElementById('edit-locations-link-fields');
  const pasteRow     = document.getElementById('edit-locations-paste-row');
  const nameInput    = document.getElementById('edit-locations-name');
  const kindSelect   = document.getElementById('edit-locations-kind');
  const resortSelect = document.getElementById('edit-locations-resort');
  const roleSelect   = document.getElementById('edit-locations-role');
  const primaryInput = document.getElementById('edit-locations-primary');
  const pasteInput   = document.getElementById('edit-locations-paste');
  const saveBtn      = document.getElementById('edit-locations-save');
  const cancelBtn    = document.getElementById('edit-locations-cancel');
  const errorEl      = document.getElementById('edit-locations-error');
  const statusEl     = document.getElementById('edit-locations-status');
  const newBtn       = document.getElementById('edit-locations-new');
  const linkBtn      = document.getElementById('edit-locations-link');
  const linksBlock   = document.getElementById('edit-locations-links-block');
  const linksEl      = document.getElementById('edit-locations-links');

  // State. ``mode`` is the single discriminator — every visibility and
  // enablement decision below is derived from it via the core module,
  // rather than from a set of booleans that can disagree.
  let mode          = MODE.IDLE;
  let allLocations  = [];   // Catalogue, name-ordered as the server sends it.
  let allResorts    = [];   // Resort catalogue, for the link picker.
  let kinds         = [];   // Location.KIND, served.
  let roles         = [];   // ResortLocation.ROLE, served.
  let selected      = null; // The selected location payload, or null.
  let draftMarker   = null; // MapLibre Marker, draggable.
  let saveInFlight  = false;
  let statusTimer   = null;

  const SAVE_LABEL = saveBtn.textContent.trim();

  // Requirement key → the phrase the hint line names it with. The core
  // module returns keys so the rule stays testable and the copy stays
  // here, next to the rest of the panel's voice.
  // i18n-allow: the location editor is staff-only (?edit=locations,
  // superuser gate in home.html and at every endpoint) and is never
  // rendered for a member of the public.
  const REQUIREMENT_LABELS = {
    name: 'a name',
    pin: 'a pin',
    resort: 'a resort',
    role: 'a role',
  };

  // ---------------------------------------------------------------------
  // Error / status lines
  // ---------------------------------------------------------------------

  // Both lines are hidden with Tailwind's ``hidden`` utility, so the
  // ``hidden`` attribute alone would not reveal them — toggle the class.
  const showError = (msg) => {
    errorEl.textContent = msg;
    errorEl.classList.remove('hidden');
  };

  const clearError = () => {
    errorEl.textContent = '';
    errorEl.classList.add('hidden');
  };

  const clearStatus = () => {
    if (statusTimer !== null) {
      window.clearTimeout(statusTimer);
      statusTimer = null;
    }
    statusEl.textContent = '';
    statusEl.classList.add('hidden');
  };

  // A confirmation reports a completed action, so it fades rather than
  // lingering over the next one.
  const showStatus = (msg) => {
    clearStatus();
    statusEl.textContent = msg;
    statusEl.classList.remove('hidden');
    statusTimer = window.setTimeout(clearStatus, 4000);
  };

  // The disabled state is re-derived by render() (which a drag can
  // trigger), so ``saveInFlight`` is the flag both paths read rather than
  // a one-off ``disabled = true`` the next render would undo.
  const setSaving = (saving) => {
    saveInFlight = saving;
    // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
    saveBtn.textContent = saving ? 'Saving…' : SAVE_LABEL;
    render();
  };

  const escapeHtml = (s) =>
    String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  // ---------------------------------------------------------------------
  // Draft marker
  // ---------------------------------------------------------------------

  const placeDraftMarker = (lng, lat) => {
    removeDraftMarker();
    if (typeof MAP === 'undefined' || !MAP) return;
    draftMarker = new maplibregl.Marker({ draggable: true, color: '#f59e0b' })
      .setLngLat([lng, lat])
      .addTo(MAP);
    draftMarker.on('dragend', render);
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

  // The draft pin's coordinate, or the location's stored one when no pin
  // has been dropped this session.
  const draftCoords = () => {
    if (draftMarker) {
      const ll = draftMarker.getLngLat();
      return { lat: ll.lat, lon: ll.lng };
    }
    if (mode === MODE.EDIT && selected) {
      return { lat: selected.latitude, lon: selected.longitude };
    }
    return { lat: null, lon: null };
  };

  // ---------------------------------------------------------------------
  // Selects
  // ---------------------------------------------------------------------

  // Append the served choices to a <select>, keeping whichever blank
  // option the template declared as the first entry.
  const fillChoices = (select, choices) => {
    while (select.options.length > 1) select.remove(1);
    for (const choice of choices) {
      const option = document.createElement('option');
      option.value = choice.value;
      option.textContent = choice.label;
      select.appendChild(option);
    }
  };

  // The resort picker's contents depend on the mode: creating offers every
  // resort, linking offers only the ones this location does not already
  // reach — see ``linkableResorts``.
  const fillResortChoices = () => {
    const offered =
      mode === MODE.LINK ? core.linkableResorts(selected, allResorts) : allResorts;
    fillChoices(
      resortSelect,
      offered.map((resort) => ({
        value: String(resort.id),
        label: `${resort.name} (${resort.region_id})`,
      })),
    );
  };

  // ---------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------

  const renderCount = () => {
    const links = allLocations.reduce((acc, row) => acc + row.links.length, 0);
    // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
    countEl.textContent = `${allLocations.length} places / ${links} links`;
  };

  const renderList = () => {
    listEl.innerHTML = '';
    const rows = core.filterLocations(allLocations, searchInput.value);
    if (rows.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'italic text-text-3';
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      empty.textContent = allLocations.length === 0
        ? 'No locations yet — start with "New location".'
        : 'No matches.';
      listEl.appendChild(empty);
      return;
    }
    for (const row of rows) {
      const li = document.createElement('li');
      const isCurrent = selected && selected.short_id === row.short_id;
      li.className = [
        'flex cursor-pointer items-center justify-between gap-2 rounded-sm px-2 py-1',
        isCurrent
          ? 'bg-status-info-bg font-semibold text-status-info-text'
          : 'hover:bg-tag',
      ].join(' ');

      const left = document.createElement('span');
      left.className = 'flex items-baseline gap-2 truncate';
      const name = document.createElement('span');
      name.className = 'truncate';
      name.textContent = row.name;
      left.appendChild(name);
      const kind = document.createElement('span');
      kind.className = 'shrink-0 text-xs text-text-3';
      kind.textContent = core.labelFor(kinds, row.kind);
      left.appendChild(kind);
      li.appendChild(left);

      // The link count is the estate's whole point made visible: a "4"
      // here is one summit doing the job four rows used to.
      const badge = document.createElement('span');
      badge.className =
        'shrink-0 rounded-full bg-tag px-1.5 py-0.5 text-pill font-medium text-text-2';
      badge.textContent = String(row.links.length);
      li.appendChild(badge);

      li.addEventListener('click', () => selectLocation(row));
      listEl.appendChild(li);
    }
  };

  // The links of the selected location, each with its own unlink control.
  const renderLinks = () => {
    const showBlock = mode === MODE.EDIT || mode === MODE.LINK;
    linksBlock.classList.toggle('hidden', !showBlock);
    linkBtn.setAttribute('aria-pressed', mode === MODE.LINK ? 'true' : 'false');
    linksEl.innerHTML = '';
    if (!showBlock || !selected) return;
    if (selected.links.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'italic text-text-3';
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      empty.textContent = 'No resort uses this location yet.';
      linksEl.appendChild(empty);
      return;
    }
    for (const link of selected.links) {
      const li = document.createElement('li');
      li.className = 'flex items-center justify-between gap-2 rounded-sm px-2 py-1';

      const label = document.createElement('span');
      label.className = 'truncate text-text-2';
      label.textContent = `${link.resort_name} · ${core.labelFor(roles, link.role)}`;
      if (link.is_primary) label.textContent += ' ★';
      li.appendChild(label);

      const button = document.createElement('button');
      button.type = 'button';
      button.className =
        'shrink-0 rounded-sm border border-border px-2 py-0.5 text-xs text-text-2 hover:bg-card-subtle disabled:cursor-not-allowed disabled:opacity-50';
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      button.textContent = 'Unlink';
      button.disabled = saveInFlight;
      button.addEventListener('click', () => unlink(link));
      li.appendChild(button);

      linksEl.appendChild(li);
    }
  };

  // The readout, plus the hint line naming whatever is still outstanding —
  // a disabled Save button on its own never says why.
  const renderTarget = () => {
    if (mode === MODE.IDLE) {
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      targetEl.innerHTML =
        '<p class="italic text-text-3">No location selected.</p>';
      return;
    }
    const coords = draftCoords();
    const missing = missingNow();
    const hint = missing.length > 0
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      ? `Needs ${missing.map((key) => REQUIREMENT_LABELS[key]).join(' and ')}.`
      : 'Ready to save.';
    const heading = mode === MODE.CREATE
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      ? (nameInput.value.trim() || 'New location')
      : selected.name;
    const subtitle = mode === MODE.LINK
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      ? 'Attaching this location to another resort — the place itself is unchanged.'
      : elevationLine();
    targetEl.innerHTML = `
      <p class="font-semibold text-text-1">${escapeHtml(heading)}</p>
      <p class="text-xs text-text-2">${escapeHtml(subtitle)}</p>
      <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        <dt class="text-text-2">Pin</dt>
        <dd class="font-mono ${draftMarker ? 'text-status-warning-text' : 'text-text-2'}">${escapeHtml(core.formatCoord(coords.lat, coords.lon))}</dd>
      </dl>
      <p class="mt-1 text-xs ${missing.length > 0 ? 'text-text-2' : 'text-status-success-text'}">${escapeHtml(hint)}</p>
    `;
  };

  // The resolved height, which is how a mis-pinned summit announces
  // itself: a peak that resolves to 900 m is in the wrong valley.
  const elevationLine = () => {
    if (mode === MODE.CREATE) {
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      return 'Elevation is resolved from the pin, not typed.';
    }
    if (!selected || selected.elevation_m == null) {
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      return 'Elevation not resolved yet.';
    }
    // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
    return `Resolves to ${Math.round(selected.elevation_m)} m.`;
  };

  // What the current mode is still waiting for. The rules live in the core
  // module; this only picks which of them applies.
  const missingNow = () => {
    if (mode === MODE.CREATE) {
      return core.missingForCreate({
        name: nameInput.value,
        hasPin: !!draftMarker,
        resortId: resortSelect.value,
        role: roleSelect.value,
      });
    }
    if (mode === MODE.LINK) {
      return core.missingForLink({
        resortId: resortSelect.value,
        role: roleSelect.value,
      });
    }
    // EDIT: the row exists, so only the name can go missing. A pin is not
    // required — the location already has one, and a save that only
    // renames is a legitimate edit.
    return nameInput.value.trim() ? [] : ['name'];
  };

  const render = () => {
    const sections = core.visibleSections(mode);
    // ``hidden`` sets display:none, which would beat ``grid``/``flex`` —
    // so both classes are toggled rather than just the first.
    placeFields.classList.toggle('hidden', !(sections.create || sections.edit));
    placeFields.classList.toggle('grid', sections.create || sections.edit);
    linkFields.classList.toggle('hidden', !(sections.create || sections.link));
    linkFields.classList.toggle('grid', sections.create || sections.link);
    pasteRow.classList.toggle('hidden', !(sections.create || sections.edit));
    pasteRow.classList.toggle('flex', sections.create || sections.edit);
    newBtn.setAttribute('aria-pressed', sections.create ? 'true' : 'false');

    renderTarget();
    renderLinks();

    saveBtn.disabled = saveInFlight || mode === MODE.IDLE || missingNow().length > 0;
    cancelBtn.disabled = saveInFlight || mode === MODE.IDLE;
  };

  // ---------------------------------------------------------------------
  // Mode transitions
  // ---------------------------------------------------------------------

  const setMode = (event) => {
    const next = core.reduceMode(mode, event);
    if (next === mode) return;
    mode = next;
    clearError();
    clearStatus();
    if (mode === MODE.CREATE) {
      selected = null;
      nameInput.value = '';
      kindSelect.value = '';
      removeDraftMarker();
    }
    if (mode === MODE.IDLE) {
      selected = null;
      removeDraftMarker();
    }
    if (mode === MODE.EDIT && selected) {
      // Coming back from link mode, or landing on a fresh selection: the
      // place fields always mirror the row behind them.
      nameInput.value = selected.name;
      kindSelect.value = selected.kind;
    }
    resortSelect.value = '';
    roleSelect.value = '';
    primaryInput.checked = false;
    fillResortChoices();
    renderList();
    render();
  };

  // Cancel unwinds one step, and in edit mode there is no step to unwind
  // — ``reduceMode`` returns EDIT unchanged — so the discard is done here
  // rather than in the reducer, which must stay a pure function of the
  // mode. Without this the button would be inert in the mode the operator
  // spends most of their time in.
  const cancelEdits = () => {
    if (mode === MODE.EDIT && selected) {
      nameInput.value = selected.name;
      kindSelect.value = selected.kind;
      removeDraftMarker();
      placeDraftMarker(selected.longitude, selected.latitude);
      clearError();
      clearStatus();
      render();
      return;
    }
    setMode('cancel');
  };

  const selectLocation = (row) => {
    selected = row;
    mode = core.reduceMode(mode, 'select');
    clearError();
    clearStatus();
    removeDraftMarker();
    nameInput.value = row.name;
    kindSelect.value = row.kind;
    resortSelect.value = '';
    roleSelect.value = '';
    primaryInput.checked = false;
    fillResortChoices();
    // A location always has a coordinate, so the pin is pre-placed and the
    // operator drags to refine rather than clicking to start.
    placeDraftMarker(row.longitude, row.latitude);
    if (typeof MAP !== 'undefined' && MAP) {
      MAP.flyTo({ center: [row.longitude, row.latitude], zoom: 13 });
    }
    renderList();
    render();
  };

  // ---------------------------------------------------------------------
  // Requests
  // ---------------------------------------------------------------------

  const errorDetailFrom = async (resp) => {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail || body.error || detail;
    } catch (_) { /* response body wasn't JSON */ }
    return detail;
  };

  const postJson = (url, body) =>
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': CSRF_TOKEN,
      },
      body: JSON.stringify(body),
    });

  // Every write endpoint answers with the same location payload, so one
  // function absorbs all four responses: splice the row into the
  // catalogue, re-select it, and land the operator back in edit mode on
  // the thing they just changed.
  const absorb = (data, message) => {
    allLocations = core.upsertLocation(allLocations, data);
    selected = allLocations.find((row) => row.short_id === data.short_id) || null;
    mode = MODE.EDIT;
    nameInput.value = selected ? selected.name : '';
    kindSelect.value = selected ? selected.kind : '';
    resortSelect.value = '';
    roleSelect.value = '';
    primaryInput.checked = false;
    removeDraftMarker();
    if (selected) placeDraftMarker(selected.longitude, selected.latitude);
    fillResortChoices();
    setSaving(false);
    showStatus(message);
    renderCount();
    renderList();
    render();
  };

  const submit = async (url, body, verb) => {
    setSaving(true);
    clearError();
    clearStatus();
    try {
      const resp = await postJson(url, body);
      if (!resp.ok) {
        const detail = await errorDetailFrom(resp);
        setSaving(false);
        showError(`${verb} failed: ${detail}`);
        return null;
      }
      return await resp.json();
    } catch (err) {
      setSaving(false);
      showError(`${verb} failed: ${err.message || err}`);
      return null;
    }
  };

  const create = async () => {
    const coords = draftCoords();
    const data = await submit(
      CREATE_URL,
      core.createPayload({
        name: nameInput.value,
        kind: kindSelect.value,
        lat: coords.lat,
        lon: coords.lon,
        resortId: Number(resortSelect.value),
        role: roleSelect.value,
        isPrimary: primaryInput.checked,
      }),
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      'Create',
    );
    // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
    if (data) absorb(data, `Created ${data.name}.`);
  };

  const save = async () => {
    const coords = draftCoords();
    // SNOW-798: the panel addresses a location by its short id and a link
    // by its uuid — the same identifiers the public surfaces use.
    const url = SAVE_URL_TEMPLATE.replace('__SHORTID__', encodeURIComponent(selected.short_id));
    const data = await submit(
      url,
      core.savePayload({
        name: nameInput.value,
        kind: kindSelect.value,
        lat: coords.lat,
        lon: coords.lon,
      }),
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      'Save',
    );
    // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
    if (data) absorb(data, `Saved ${data.name} at ${core.formatCoord(data.latitude, data.longitude)}.`);
  };

  const link = async () => {
    const url = LINK_URL_TEMPLATE.replace('__SHORTID__', encodeURIComponent(selected.short_id));
    const data = await submit(
      url,
      core.linkPayload({
        resortId: Number(resortSelect.value),
        role: roleSelect.value,
        isPrimary: primaryInput.checked,
      }),
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      'Link',
    );
    // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
    if (data) absorb(data, `Linked ${data.name} to ${data.links.length} resort(s).`);
  };

  const unlink = async (linkRow) => {
    const url = UNLINK_URL_TEMPLATE.replace('__UUID__', encodeURIComponent(linkRow.uuid));
    const data = await submit(
      url,
      {},
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      'Unlink',
    );
    // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
    if (data) absorb(data, `Unlinked ${linkRow.resort_name}.`);
  };

  const onSave = () => {
    if (saveInFlight) return;
    if (mode === MODE.CREATE) return create();
    if (mode === MODE.LINK) return link();
    if (mode === MODE.EDIT && selected) return save();
    return undefined;
  };

  // ---------------------------------------------------------------------
  // Input handlers
  // ---------------------------------------------------------------------

  const onPasteInput = () => {
    if (mode !== MODE.CREATE && mode !== MODE.EDIT) return;
    const parsed = core.parseLatLon(pasteInput.value);
    if (!parsed) {
      // Pasted then cleared — drop the marker so the readout doesn't lie.
      if (pasteInput.value.trim() === '') {
        removeDraftMarker();
        render();
      }
      return;
    }
    placeDraftMarker(parsed.lon, parsed.lat);
    if (typeof MAP !== 'undefined' && MAP) {
      MAP.flyTo({ center: [parsed.lon, parsed.lat], zoom: 13 });
    }
    clearError();
    render();
  };

  const onMapClick = (e) => {
    if (mode !== MODE.CREATE && mode !== MODE.EDIT) return;
    if (typeof MAP === 'undefined' || !MAP) return;
    placeDraftMarker(e.lngLat.lng, e.lngLat.lat);
    render();
  };

  const onKeyDown = (e) => {
    if (e.key === 'Escape' && mode !== MODE.IDLE) {
      cancelEdits();
      e.preventDefault();
    } else if (e.key === 'Enter' && !saveBtn.disabled) {
      const tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      onSave();
      e.preventDefault();
    }
  };

  // ---------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------

  const loadCatalogue = async () => {
    try {
      const resp = await fetch(QUEUE_URL);
      if (!resp.ok) {
        // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
        showError(`Could not load locations (HTTP ${resp.status}).`);
        return;
      }
      const data = await resp.json();
      allLocations = core.sortLocations(data.locations || []);
      allResorts = data.resorts || [];
      kinds = data.kinds || [];
      roles = data.roles || [];
      fillChoices(kindSelect, kinds);
      fillChoices(roleSelect, roles);
      fillResortChoices();
      renderCount();
      renderList();
      render();
    } catch (err) {
      // i18n-allow: staff-only surface, same as REQUIREMENT_LABELS above.
      showError(`Could not load locations: ${err.message || err}`);
    }
  };

  // Hide the normal-mode UI the editor has no use for, and the page-level
  // search pill in particular — the panel has its own filter, and two
  // search affordances on one screen is one too many.
  const enterEditModeVisuals = () => {
    for (const id of ['sheet', 'season-scrubber', 'map-legend', 'search-pill']) {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    }
  };

  saveBtn.addEventListener('click', onSave);
  cancelBtn.addEventListener('click', cancelEdits);
  newBtn.addEventListener('click', () => setMode(mode === MODE.CREATE ? 'cancel' : 'new'));
  linkBtn.addEventListener('click', () => setMode('link'));
  searchInput.addEventListener('input', renderList);
  pasteInput.addEventListener('input', onPasteInput);
  // The readout echoes the typed name and the hint line names what is
  // still missing, so both re-derive on every keystroke and every choice.
  for (const el of [nameInput, kindSelect, resortSelect, roleSelect, primaryInput]) {
    el.addEventListener('input', render);
    el.addEventListener('change', render);
  }
  document.addEventListener('keydown', onKeyDown);

  enterEditModeVisuals();

  if (typeof MAP_READY_PROMISE !== 'undefined') {
    MAP_READY_PROMISE.then(() => {
      if (typeof MAP !== 'undefined' && MAP) MAP.on('click', onMapClick);
    });
  }

  loadCatalogue();
})();
