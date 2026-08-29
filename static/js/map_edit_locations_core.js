/*
 * static/js/map_edit_locations_core.js — Pure helpers for the in-map
 * location editor (SNOW-755).
 *
 * The editor's DOM and map wiring live in ``map_edit_locations.js``;
 * everything here is a pure function of its arguments. Nothing in this
 * file touches the DOM, the map, ``fetch`` or module-level state, which is
 * what makes it importable under jsdom and therefore testable.
 *
 * The split is deliberate rather than stylistic. ``map_edit_resorts.js`` —
 * the editor this one is modelled on — is 1179 lines of a single IIFE that
 * reads the DOM by id at parse time, so it cannot be imported at all, and
 * consequently has zero unit tests. The codebase's answer to that is the
 * ``*_core.js`` convention (``search_core.js``, ``scrubber_core.js``,
 * ``basemap_download_core.js``): put the logic somewhere a test can reach
 * it and leave the wiring behind.
 *
 * ## The three modes
 *
 * One panel, three mutually-exclusive things to be doing:
 *
 *   CREATE — placing a NEW location and its first resort link.
 *   EDIT   — a location is selected; move it, rename it, re-classify it.
 *   LINK   — a location is selected; attach it to ANOTHER resort.
 *
 * LINK is the one the resort editor has no equivalent of, and the reason
 * the estate exists: Mont Fort is one row that Verbier, Nendaz, Veysonnaz
 * and Thyon all reference. Placing a second pin on the same summit is the
 * mistake the mode exists to make unnecessary, so ``linkableResorts``
 * removes the resorts already linked from the picker entirely.
 *
 * ``reduceMode`` is what keeps the three exclusive. Deriving visibility
 * from three independent booleans is how two forms end up on screen at
 * once, each writing to a different endpoint, with the operator unable to
 * tell which the Save button belongs to.
 *
 * ## kind vs role
 *
 * ``Location.kind`` describes the place — Mont Fort is a peak whoever is
 * looking at it. ``ResortLocation.role`` describes one resort's
 * relationship to it, and the same point is plausibly one resort's top and
 * another's mid-station. Neither is derived from the other anywhere in
 * this file; the payload builders carry both, separately.
 *
 * Exports (frozen ``self.pwaEditLocationsCore``):
 *
 *   MODE                        The four mode constants.
 *   reduceMode(mode, event)     The mode transition table.
 *   visibleSections(mode)       Which of the panel's three blocks show.
 *   filterLocations(rows, q)    Catalogue filtering, by place or resort.
 *   sortLocations(rows)         The server's order, reproduced client-side.
 *   upsertLocation(rows, row)   Splice a written row into the catalogue.
 *   linkableResorts(loc, all)   The resorts a location is NOT yet linked to.
 *   missingForCreate(draft)     Requirement keys still outstanding.
 *   missingForLink(draft)       Ditto, for the link form.
 *   createPayload(draft)        POST body for the create endpoint.
 *   savePayload(draft)          POST body for the save endpoint.
 *   linkPayload(draft)          POST body for the link endpoint.
 *   parseLatLon(text)           The "46.43, 6.97" paste box.
 *   formatCoord(lat, lon)       Five decimals — what the server stores.
 *   labelFor(choices, value)    A served {value,label} list, looked up.
 *
 * Coordinate-ordering reminder:
 *   - DB columns / wire format: latitude, longitude
 *   - GeoJSON:                  [longitude, latitude]
 *   - MapLibre marker:          {lng, lat}
 */

(function () {
  'use strict';

  /**
   * The panel's four states. Exactly one holds at a time.
   *
   * IDLE is "nothing selected" — the panel shows the catalogue and no
   * form at all, which is what it boots into.
   */
  var MODE = Object.freeze({
    IDLE: 'idle',
    CREATE: 'create',
    EDIT: 'edit',
    LINK: 'link',
  });

  /**
   * Coordinate precision, matching what the server stores.
   *
   * ``edit_location_create`` rounds a placed pin to five decimals (~1 m)
   * because that is what ``apps/locations/data/locations.tsv`` carries. A
   * readout showing more than the row will hold would report a position
   * that is about to change under the operator.
   */
  var COORD_DECIMALS = 5;

  /**
   * Return the mode ``event`` moves the panel to.
   *
   * Written as a transition table rather than a set of booleans so the
   * exclusivity is a property of this function and not of whichever
   * handler ran last. Events:
   *
   *   'new'    — the New location button.
   *   'select' — a catalogue row was picked.
   *   'link'   — the Link to another resort button (a toggle).
   *   'cancel' — the Cancel button, or Escape.
   *   'clear'  — the selection went away entirely.
   *
   * ``'link'`` needs a selected location to link, so it is a no-op from
   * IDLE and from CREATE — the location being created does not exist yet,
   * and its first link is part of the create form.
   *
   * ``'cancel'`` unwinds one step rather than jumping to IDLE: backing out
   * of LINK returns to the location that was being edited, which is where
   * the operator was, and cancelling an edit keeps the row selected
   * because the usual reason to cancel is a mis-click on the map.
   *
   * @param {string} mode Current mode.
   * @param {string} event One of the five above.
   * @returns {string} The next mode; ``mode`` itself for an unknown event.
   */
  function reduceMode(mode, event) {
    var current = mode || MODE.IDLE;
    switch (event) {
      case 'new':
        return MODE.CREATE;
      case 'select':
        return MODE.EDIT;
      case 'link':
        if (current === MODE.EDIT) return MODE.LINK;
        if (current === MODE.LINK) return MODE.EDIT;
        return current;
      case 'cancel':
        if (current === MODE.LINK) return MODE.EDIT;
        if (current === MODE.CREATE) return MODE.IDLE;
        return current;
      case 'clear':
        return MODE.IDLE;
      default:
        return current;
    }
  }

  /**
   * Return which of the panel's three form blocks are visible in ``mode``.
   *
   * The one place the exclusivity becomes DOM state. Two blocks showing at
   * once would put two Save buttons on screen writing to two endpoints.
   *
   * @param {string} mode A ``MODE`` value.
   * @returns {{create: boolean, edit: boolean, link: boolean}}
   */
  function visibleSections(mode) {
    return {
      create: mode === MODE.CREATE,
      edit: mode === MODE.EDIT,
      link: mode === MODE.LINK,
    };
  }

  /**
   * Fold ``text`` for case-insensitive matching.
   *
   * @param {*} text
   * @returns {string}
   */
  function normalise(text) {
    return String(text == null ? '' : text).toLowerCase().trim();
  }

  /**
   * Filter the catalogue by place name or by any linked resort's name.
   *
   * Matching on the resort as well as the place is what makes the filter
   * useful during curation: the operator works resort by resort ("show me
   * everything Verbier reaches"), not summit by summit, and a location's
   * own name rarely contains its resort's.
   *
   * @param {Array<Object>} locations Queue-endpoint location payloads.
   * @param {string} query Raw user input; blank returns everything.
   * @returns {Array<Object>} The surviving rows, in input order.
   */
  function filterLocations(locations, query) {
    var q = normalise(query);
    var rows = locations || [];
    if (!q) return rows.slice();
    return rows.filter(function (row) {
      if (normalise(row && row.name).indexOf(q) !== -1) return true;
      return ((row && row.links) || []).some(function (link) {
        return normalise(link && link.resort_name).indexOf(q) !== -1;
      });
    });
  }

  /**
   * Sort the catalogue as the queue endpoint does — name, then id.
   *
   * Applied after splicing in a created or saved row so it lands where a
   * page reload would have put it, rather than at the end of the list.
   *
   * @param {Array<Object>} locations
   * @returns {Array<Object>} A new array; the input is not mutated.
   */
  function sortLocations(locations) {
    return (locations || []).slice().sort(function (a, b) {
      return (
        String(a.name || '').localeCompare(String(b.name || '')) ||
        (a.id || 0) - (b.id || 0)
      );
    });
  }

  /**
   * Return the catalogue with ``row`` inserted or replaced, then re-sorted.
   *
   * Every write endpoint answers with the same location payload, so one
   * function covers create (insert), save (replace), link and unlink
   * (replace, because both change the row's ``links``).
   *
   * @param {Array<Object>} locations
   * @param {Object} row A location payload from any write endpoint.
   * @returns {Array<Object>} A new array; the input is not mutated.
   */
  function upsertLocation(locations, row) {
    if (!row || row.id == null) return (locations || []).slice();
    var kept = (locations || []).filter(function (existing) {
      return existing.id !== row.id;
    });
    kept.push(row);
    return sortLocations(kept);
  }

  /**
   * Return the resorts ``location`` is not already linked to.
   *
   * The link picker offers only these. Offering the rest would put the
   * duplicate-link 400 one click away from an operator who cannot see,
   * from the picker alone, which resorts are already attached.
   *
   * @param {Object} location A location payload, or null.
   * @param {Array<Object>} resorts The queue endpoint's resort catalogue.
   * @returns {Array<Object>} The linkable subset, in input order.
   */
  function linkableResorts(location, resorts) {
    var linked = ((location && location.links) || []).map(function (link) {
      return link.resort_id;
    });
    return (resorts || []).filter(function (resort) {
      return linked.indexOf(resort.id) === -1;
    });
  }

  /**
   * Return the requirement keys a create draft is still missing.
   *
   * Keys, not sentences: the panel renders them through its strings
   * template, so the copy stays server-side and translatable while the
   * rule stays here and testable.
   *
   * A blank ``kind`` is deliberately absent — an unclassified place is a
   * legitimate row, both in the model and in the sheet.
   *
   * @param {Object} draft {name, hasPin, resortId, role}
   * @returns {string[]} Any of 'name', 'pin', 'resort', 'role'.
   */
  function missingForCreate(draft) {
    var d = draft || {};
    var missing = [];
    if (!normalise(d.name)) missing.push('name');
    if (!d.hasPin) missing.push('pin');
    if (!d.resortId) missing.push('resort');
    if (!normalise(d.role)) missing.push('role');
    return missing;
  }

  /**
   * Return the requirement keys a link draft is still missing.
   *
   * No pin: the location already knows where it is, and linking says only
   * that another resort reaches it.
   *
   * @param {Object} draft {resortId, role}
   * @returns {string[]} Any of 'resort', 'role'.
   */
  function missingForLink(draft) {
    var d = draft || {};
    var missing = [];
    if (!d.resortId) missing.push('resort');
    if (!normalise(d.role)) missing.push('role');
    return missing;
  }

  /**
   * Round a coordinate to the precision the server stores.
   *
   * @param {number} value
   * @returns {number}
   */
  function roundCoord(value) {
    var factor = Math.pow(10, COORD_DECIMALS);
    return Math.round(Number(value) * factor) / factor;
  }

  /**
   * Build the create endpoint's request body.
   *
   * @param {Object} draft {name, kind, lat, lon, resortId, role, isPrimary}
   * @returns {Object} The JSON body.
   */
  function createPayload(draft) {
    var d = draft || {};
    return {
      name: String(d.name == null ? '' : d.name).trim(),
      kind: String(d.kind == null ? '' : d.kind),
      latitude: roundCoord(d.lat),
      longitude: roundCoord(d.lon),
      resort_id: d.resortId,
      role: String(d.role == null ? '' : d.role),
      is_primary: !!d.isPrimary,
    };
  }

  /**
   * Build the save endpoint's request body.
   *
   * No link fields: a save edits the place, and each resort's role is its
   * own — changing one is an unlink and a re-link, not a side effect of
   * moving the pin.
   *
   * @param {Object} draft {name, kind, lat, lon}
   * @returns {Object} The JSON body.
   */
  function savePayload(draft) {
    var d = draft || {};
    return {
      name: String(d.name == null ? '' : d.name).trim(),
      kind: String(d.kind == null ? '' : d.kind),
      latitude: roundCoord(d.lat),
      longitude: roundCoord(d.lon),
    };
  }

  /**
   * Build the link endpoint's request body.
   *
   * No coordinate, by design — see the endpoint's docstring.
   *
   * @param {Object} draft {resortId, role, isPrimary}
   * @returns {Object} The JSON body.
   */
  function linkPayload(draft) {
    var d = draft || {};
    return {
      resort_id: d.resortId,
      role: String(d.role == null ? '' : d.role),
      is_primary: !!d.isPrimary,
    };
  }

  /**
   * Parse a "lat, lon" string of the kind Google Maps shows.
   *
   * Tolerates whitespace variation and a degrees sign. Returns null for
   * anything that is not two finite numbers in valid WGS-84 ranges; the
   * Swiss bounding box is enforced server-side, because a pin just outside
   * it is a mistake worth an explanation rather than a silently ignored
   * keystroke.
   *
   * @param {string} raw
   * @returns {{lat: number, lon: number}|null}
   */
  function parseLatLon(raw) {
    if (!raw) return null;
    var cleaned = String(raw).replace(/[°\s]+/g, ' ').trim();
    var parts = cleaned.split(/[,;\s]+/).filter(Boolean);
    if (parts.length !== 2) return null;
    var lat = Number(parts[0]);
    var lon = Number(parts[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    return { lat: lat, lon: lon };
  }

  /**
   * Format a coordinate pair for the panel's readout.
   *
   * @param {number} lat
   * @param {number} lon
   * @returns {string} e.g. "46.10361, 7.29889"; "—" for a missing pair.
   */
  function formatCoord(lat, lon) {
    // ``Number(null)`` is 0, and 0 is finite — so a null check has to come
    // first or an unplaced pin reads as "0.00000, 0.00000", a coordinate
    // in the Gulf of Guinea that looks like a real answer.
    if (lat == null || lon == null) return '—';
    if (!Number.isFinite(Number(lat)) || !Number.isFinite(Number(lon))) {
      return '—';
    }
    return (
      Number(lat).toFixed(COORD_DECIMALS) +
      ', ' +
      Number(lon).toFixed(COORD_DECIMALS)
    );
  }

  /**
   * Look a value up in a served ``[{value, label}]`` list.
   *
   * ``Location.KIND`` and ``ResortLocation.ROLE`` are served by the queue
   * endpoint rather than duplicated here, so the model stays the single
   * source of both vocabularies.
   *
   * @param {Array<{value: string, label: string}>} choices
   * @param {string} value
   * @returns {string} The label, or ``value`` when it is not in the list —
   *   never blank, because a blank cell reads as missing data.
   */
  function labelFor(choices, value) {
    var match = (choices || []).find(function (choice) {
      return choice && choice.value === value;
    });
    return match ? match.label : String(value == null ? '' : value);
  }

  self.pwaEditLocationsCore = Object.freeze({
    MODE: MODE,
    COORD_DECIMALS: COORD_DECIMALS,
    reduceMode: reduceMode,
    visibleSections: visibleSections,
    filterLocations: filterLocations,
    sortLocations: sortLocations,
    upsertLocation: upsertLocation,
    linkableResorts: linkableResorts,
    missingForCreate: missingForCreate,
    missingForLink: missingForLink,
    createPayload: createPayload,
    savePayload: savePayload,
    linkPayload: linkPayload,
    parseLatLon: parseLatLon,
    formatCoord: formatCoord,
    labelFor: labelFor,
  });
})();
