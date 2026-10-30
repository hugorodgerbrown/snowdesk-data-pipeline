/*
 * static/js/offline_journey.js — paint one panel per downloaded area on
 * /offline/ and static/offline.html (SNOW-1308).
 *
 * A reading layer over what `offline_audit.js` already collects. It takes
 * no storage walk of its own: the audit announces every collection as
 * `snowdesk:offline-readings` (and answers `pwaOfflineAudit.latest()`),
 * and this module paints from that — on load it asks the audit for one
 * read, so every panel is answered before anyone runs the check.
 *
 * The one reading the audit does not take is arithmetic, not storage:
 * whether every basemap tile an area's ground implies is in its bucket,
 * which `offline_journey_core.js`'s `tileReading` asks of
 * `pwaBasemapDownloadCore.blobFullyCached` over the bucket entries the
 * audit already listed. Where that core is absent (static/offline.html)
 * the reading is unknown, and the map stage says "Can't tell" rather than
 * guessing.
 *
 * ## Two controls per panel, both existing verbs
 *
 *   - **Sync now** — the audit's own (`pwaOfflineAudit.syncArea`), offered
 *     exactly where the report used to offer it: a host that can fetch,
 *     and an area whose tiles verify. A "Needs a refresh — tap to fix"
 *     verdict is the same control; on a page opened with `?sync=all` it
 *     runs the whole "Get ready for offline" instead. On a host that
 *     cannot fetch, the verdict links to `/offline/?sync=<id>`.
 *   - **Remove** — `pwaBasemapAreaRemoval.evict`, the removal the map's
 *     Manage downloads sheet uses, for a recorded area and an orphaned
 *     bucket alike. Offered only where that module and what it needs are
 *     loaded. Afterwards the audit re-reads storage, and the panels are
 *     repainted from what is actually there.
 *
 * ## No class strings, no innerHTML
 *
 * Every element is cloned from a `<template>` in the host
 * (templates/includes/_offline_journey.html, or its inline copy in
 * static/offline.html) and filled through `textContent` and attributes.
 * The classes live in the templates, where ds-lint reads them; the
 * fallback page's copy carries none and styles by attribute.
 */

(function () {
  'use strict';

  var ROOT_SELECTOR = '[data-offline-journey]';

  // English copy, for static/offline.html, where `pwaStrings` is absent —
  // and the fallback wherever a key is missing from the strings template.
  var FALLBACKS = {
    'verdict-ready': 'Ready for offline',
    'verdict-refresh': 'Needs a refresh — tap to fix',
    'verdict-open-map': 'Needs a refresh — open the map',
    'verdict-unknown': 'Can’t tell',
    'stage-map': 'The map',
    'stage-conditions': 'Bulletins and weather',
    'stage-things': 'Your things',
    'answer-yes': 'Yes',
    'answer-no': 'No',
    'answer-unknown': '—',
    'note-map-tiles': 'some of its map tiles are missing',
    'note-map-incomplete': 'what the map needs to draw it is incomplete',
    'note-map-missing': 'recorded as downloaded, but nothing is stored',
    'note-map-orphaned': 'an unfinished download taking up space — remove it',
    'note-map-unreadable': 'this device’s storage did not answer',
    'note-map-unverifiable': 'downloaded before the app recorded what an area needs',
    'note-map-tiles-unread': 'its map tiles could not be checked here',
    'note-conditions-stale': 'from %(day)s, an earlier day',
    'note-conditions-never': 'never saved',
    'note-things-unrecorded': 'not recorded yet — sync this area to list them',
    'note-things-none': 'none of your trips or pins are in this area',
    'note-things-other-account': 'saved by another account',
    'unnamed-trip': 'Unnamed trip',
    'unnamed-pin': 'Unnamed pin',
    'label-dropzone': '%(name)s (drop zone)',
    'label-custom': '%(name)s (area you drew)',
    'default-custom-name': 'Custom area %(n)s',
    sync: 'Sync now',
    'sync-label': 'Sync the bulletins and weather saved inside %(name)s',
    remove: 'Remove',
    'remove-label': 'Remove %(name)s from this device',
  };

  // Which note a stage's reason reads as.
  var MAP_NOTES = {
    tiles: 'note-map-tiles',
    incomplete: 'note-map-incomplete',
    missing: 'note-map-missing',
    orphaned: 'note-map-orphaned',
    unreadable: 'note-map-unreadable',
    unverifiable: 'note-map-unverifiable',
    'tiles-unread': 'note-map-tiles-unread',
  };
  var THINGS_NOTES = {
    unrecorded: 'note-things-unrecorded',
    'none-here': 'note-things-none',
    'other-account': 'note-things-other-account',
  };

  /**
   * The translated copy, or the English fallbacks.
   *
   * @returns {Record<string, string>}
   */
  function strings() {
    if (self.pwaStrings && typeof self.pwaStrings.read === 'function') {
      return self.pwaStrings.read('offline-journey-strings-template', FALLBACKS);
    }
    return FALLBACKS;
  }

  /**
   * One string, filled.
   *
   * @param {Record<string, string>} t
   * @param {string} key
   * @param {Record<string, string>} [values]
   * @returns {string}
   */
  function say(t, key, values) {
    var template = String(t[key] || FALLBACKS[key] || '');
    return template.replace(/%\((\w+)\)s/g, function (_m, name) {
      return values && Object.prototype.hasOwnProperty.call(values, name) ? values[name] : '';
    });
  }

  /**
   * The first element a template holds, cloned.
   *
   * @param {Document} doc
   * @param {string} id
   * @returns {HTMLElement|null}
   */
  function clone(doc, id) {
    var template = /** @type {HTMLTemplateElement|null} */ (doc.getElementById(id));
    if (!template || !template.content) return null;
    var first = template.content.firstElementChild;
    return first ? /** @type {HTMLElement} */ (first.cloneNode(true)) : null;
  }

  /**
   * Set one slot's text, by its data attribute.
   *
   * @param {HTMLElement} el
   * @param {string} attr
   * @param {string} text
   * @returns {HTMLElement|null}
   */
  function fillSlot(el, attr, text) {
    var slot = /** @type {HTMLElement|null} */ (el.querySelector('[' + attr + ']'));
    if (slot) slot.textContent = text;
    return slot;
  }

  /**
   * The day content was fetched on, as the downloads surfaces date it
   * (SNOW-1139's `pwaCachedStampCore.formatDay`), or its ISO date where
   * that core is absent (static/offline.html).
   *
   * @param {string|null} contentAt
   * @returns {string}
   */
  function contentDay(contentAt) {
    if (!contentAt) return '';
    var stamp = self.pwaCachedStampCore;
    if (stamp && typeof stamp.formatDay === 'function') {
      var day = stamp.formatDay(contentAt);
      if (day) return day;
    }
    return String(contentAt).slice(0, 10);
  }

  /**
   * A panel's label: its name, and its kind where that is not obvious.
   *
   * @param {Object} panel
   * @param {Record<string, string>} t
   * @returns {string}
   */
  function panelLabel(panel, t) {
    if (panel.kind === 'dropzone') return say(t, 'label-dropzone', { name: panel.name });
    if (panel.kind === 'custom') return say(t, 'label-custom', { name: panel.name });
    return panel.name;
  }

  /**
   * One stage row.
   *
   * @param {Document} doc
   * @param {string} id
   * @param {Object} stage
   * @param {string} note
   * @param {Record<string, string>} t
   * @returns {HTMLElement|null}
   */
  function renderStage(doc, id, stage, note, t) {
    var el = clone(doc, 'offline-journey-stage-template');
    if (!el) return null;
    el.setAttribute('data-journey-stage', id);
    el.setAttribute('data-journey-status', stage.status);
    fillSlot(el, 'data-journey-stage-label', say(t, 'stage-' + id));
    fillSlot(el, 'data-journey-stage-value', say(t, 'answer-' + stage.status));
    var noteEl = fillSlot(el, 'data-journey-stage-note', note);
    if (noteEl) noteEl.hidden = !note;
    var list = /** @type {HTMLElement|null} */ (el.querySelector('[data-journey-things]'));
    var rows = Array.isArray(stage.rows) ? stage.rows : [];
    if (list && rows.length) {
      rows.forEach(function (row) {
        var item = clone(doc, 'offline-journey-thing-template');
        if (!item) return;
        item.setAttribute('data-journey-thing', row.kind);
        item.setAttribute('data-journey-status', row.status);
        fillSlot(
          item,
          'data-journey-thing-name',
          row.name || say(t, row.kind === 'trip' ? 'unnamed-trip' : 'unnamed-pin'),
        );
        fillSlot(item, 'data-journey-thing-value', say(t, 'answer-' + row.status));
        list.appendChild(item);
      });
      list.hidden = false;
    }
    return el;
  }

  /**
   * Paint one panel.
   *
   * @param {Document} doc
   * @param {Object} panel From `buildPanels`.
   * @param {Record<string, string>} t
   * @param {{canRemove: boolean, mapUrl: string, offlineUrl: string,
   *   canFetch: boolean}} host
   * @returns {HTMLElement|null}
   */
  function renderPanel(doc, panel, t, host) {
    var el = clone(doc, 'offline-journey-panel-template');
    if (!el) return null;
    var label = panelLabel(panel, t);
    el.setAttribute('data-journey-panel', panel.id);
    el.setAttribute('data-journey-verdict-state', panel.verdict);
    fillSlot(el, 'data-journey-name', label);

    var verdictEl = /** @type {HTMLElement|null} */ (el.querySelector('[data-journey-verdict]'));
    if (verdictEl) {
      var text = say(t, 'verdict-' + panel.verdict);
      var fix = null;
      if (panel.verdict === 'open-map' && host.mapUrl) {
        fix = clone(doc, 'offline-journey-fix-link-template');
        if (fix) fix.setAttribute('href', host.mapUrl);
      } else if (panel.verdict === 'refresh' && panel.canSync) {
        fix = clone(doc, 'offline-journey-fix-button-template');
        if (fix) fix.setAttribute('data-journey-fix', panel.id);
      } else if (panel.verdict === 'refresh' && !host.canFetch && host.offlineUrl) {
        // The fallback page cannot fetch; the page that can is one link
        // away, and runs this area's Sync now on arrival (SNOW-951).
        fix = clone(doc, 'offline-journey-fix-link-template');
        if (fix) {
          fix.setAttribute('href', host.offlineUrl + '?sync=' + encodeURIComponent(panel.id));
        }
      }
      if (fix) {
        fix.textContent = text;
        verdictEl.appendChild(fix);
      } else {
        verdictEl.textContent = text;
      }
    }

    var stages = /** @type {HTMLElement|null} */ (el.querySelector('[data-journey-stages]'));
    if (stages) {
      var map = panel.stages.map;
      var mapNote = map.status === 'yes' ? '' : say(t, MAP_NOTES[map.reason] || '');
      var mapRow = renderStage(doc, 'map', map, mapNote, t);
      if (mapRow) stages.appendChild(mapRow);

      var conditions = panel.stages.conditions;
      var conditionsNote = '';
      if (conditions.reason === 'stale') {
        conditionsNote = say(t, 'note-conditions-stale', { day: contentDay(panel.contentAt) });
      } else if (conditions.reason === 'never') {
        conditionsNote = say(t, 'note-conditions-never');
      }
      var conditionsRow = renderStage(doc, 'conditions', conditions, conditionsNote, t);
      if (conditionsRow) stages.appendChild(conditionsRow);

      var things = panel.stages.things;
      if (things) {
        var thingsNote = things.reason ? say(t, THINGS_NOTES[things.reason] || '') : '';
        var thingsRow = renderStage(doc, 'things', things, thingsNote, t);
        if (thingsRow) stages.appendChild(thingsRow);
      }
    }

    var sync = /** @type {HTMLButtonElement|null} */ (el.querySelector('[data-journey-sync]'));
    if (sync) {
      sync.hidden = !panel.canSync;
      sync.textContent = say(t, 'sync');
      sync.setAttribute('aria-label', say(t, 'sync-label', { name: label }));
      sync.setAttribute('data-journey-sync', panel.id);
    }
    var remove = /** @type {HTMLButtonElement|null} */ (el.querySelector('[data-journey-remove]'));
    if (remove) {
      remove.hidden = !host.canRemove;
      remove.textContent = say(t, 'remove');
      remove.setAttribute('aria-label', say(t, 'remove-label', { name: label }));
      remove.setAttribute('data-journey-remove', panel.id);
    }
    return el;
  }

  /**
   * Each area's tile reading, by id.
   *
   * @param {Object} readings
   * @returns {Record<string, boolean|null>}
   */
  function tileReadings(readings) {
    var core = self.pwaOfflineJourneyCore;
    var audit = self.pwaOfflineAuditCore;
    var out = {};
    if (!core || !audit || !readings) return out;
    var download = self.pwaBasemapDownloadCore || null;
    audit.chosenAreas(readings).forEach(function (area) {
      out[area.id] = core.tileReading(area, download);
    });
    return out;
  }

  /**
   * Whether this page can remove an area.
   *
   * @returns {boolean}
   */
  function canRemove() {
    var removal = self.pwaBasemapAreaRemoval;
    return !!(removal && typeof removal.available === 'function' && removal.available());
  }

  /**
   * Bind one host.
   *
   * @param {HTMLElement} root
   * @returns {void}
   */
  function bind(root) {
    var doc = root.ownerDocument;
    var output = /** @type {HTMLElement|null} */ (root.querySelector('[data-journey-output]'));
    var empty = /** @type {HTMLElement|null} */ (root.querySelector('[data-journey-empty]'));
    var waiting = /** @type {HTMLElement|null} */ (root.querySelector('[data-journey-waiting]'));
    if (!output) return;
    var t = strings();
    var busy = false;
    var lastDetail = null;

    var paint = function (detail) {
      var core = self.pwaOfflineJourneyCore;
      if (!core || !detail || !detail.readings) return;
      lastDetail = detail;
      var readings = detail.readings;
      var panels = core.buildPanels(readings, t, {
        tiles: tileReadings(readings),
        origin: self.location ? self.location.origin : '',
        canFetch: !!detail.canFetch,
      });
      var host = {
        canRemove: canRemove(),
        canFetch: !!detail.canFetch,
        mapUrl: root.getAttribute('data-map-downloads-url') || '',
        offlineUrl: root.getAttribute('data-offline-url') || '',
      };
      output.textContent = '';
      panels.forEach(function (panel) {
        var el = renderPanel(doc, panel, t, host);
        if (el) output.appendChild(el);
      });
      if (waiting) waiting.hidden = true;
      if (empty) empty.hidden = panels.length > 0;
      setBusy(busy || !!detail.running);
    };

    var setBusy = function (on) {
      var controls = output.querySelectorAll(
        '[data-journey-sync], [data-journey-remove], button[data-journey-fix]',
      );
      for (var i = 0; i < controls.length; i += 1) {
        /** @type {HTMLButtonElement} */ (controls[i]).disabled = on;
      }
    };

    var audit = function () {
      return self.pwaOfflineAudit;
    };

    var act = async function (work) {
      if (busy) return;
      busy = true;
      setBusy(true);
      try {
        await work();
      } catch (_err) {
        // The re-read below says what is true either way.
      } finally {
        busy = false;
      }
      if (lastDetail) setBusy(false);
    };

    var sync = function (areaId) {
      return act(async function () {
        var api = audit();
        if (!api) return;
        var detail = api.latest ? api.latest() : null;
        if (detail && detail.syncAllRequested) await api.syncAll();
        else await api.syncArea(areaId);
      });
    };

    var removeArea = function (areaId) {
      return act(async function () {
        var removal = self.pwaBasemapAreaRemoval;
        if (removal && typeof removal.evict === 'function') {
          await removal.evict([areaId]);
        }
        // Never trust the attempt: read storage again, and the panels
        // repaint from what is actually there.
        var api = audit();
        if (api && typeof api.refresh === 'function') await api.refresh();
      });
    };

    output.addEventListener('click', function (event) {
      var target = /** @type {HTMLElement} */ (event.target);
      if (!target || !target.closest) return;
      var syncButton = target.closest('[data-journey-sync], button[data-journey-fix]');
      if (syncButton) {
        var id = syncButton.getAttribute('data-journey-sync') ||
          syncButton.getAttribute('data-journey-fix');
        if (id) sync(id);
        return;
      }
      var removeButton = target.closest('[data-journey-remove]');
      if (removeButton) {
        var removeId = removeButton.getAttribute('data-journey-remove');
        if (removeId) removeArea(removeId);
      }
    });

    doc.addEventListener('snowdesk:offline-readings', function (event) {
      paint(/** @type {CustomEvent} */ (event).detail);
    });

    var api = audit();
    if (!api || typeof api.latest !== 'function') return;
    var current = api.latest();
    if (current && current.readings) {
      paint(current);
    } else if (!current || !current.running) {
      // No read yet, and none on its way: ask for one. A run already in
      // flight (`?sync=`) announces its own readings when it lands.
      api.refresh();
    }
  }

  /**
   * Bind every host on the page.
   *
   * @returns {void}
   */
  function init() {
    var roots = document.querySelectorAll(ROOT_SELECTOR);
    for (var i = 0; i < roots.length; i += 1) {
      bind(/** @type {HTMLElement} */ (roots[i]));
    }
  }

  self.pwaOfflineJourney = Object.freeze({ init: init });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
