// @ts-check
/**
 * static/js/map_panel_trail.js — moving between the map's three kinds of
 * place panel, and back (Hugo, 25 Oct 2026).
 *
 * The region, resort and weather sheets each answer one question, and each
 * ends with rows naming its neighbours (`includes/_place_link_row.html`): a
 * resort's avalanche region, a region's resorts, a weather point's resort
 * and region. A row is a real link to the place's page; on the map this
 * module opens the place's own panel instead, in place — the one-object-
 * sheet rule closes the panel it came from — and puts a back pill at the
 * top of the new panel naming where the reader came from.
 *
 * THE TRAIL IS ONE STEP DEEP. Following a row replaces it; the back pill
 * spends it; once the panel it led to has shown, anything that takes that
 * panel away — another place painted into it (a map tap, a search pick),
 * or its × — drops it. A trail of more than one step would need a stack
 * the reader cannot see; one step answers "take me back" and nothing more.
 *
 * HOW A PLACE OPENS. Each kind already has a door, and this module only
 * knocks on it:
 *   region  — `snowdesk:region-open` (map.js selects it as a search pick
 *             does, and the region sheet opens for the selection);
 *   resort  — `snowdesk:resort-open` (map.js flies to the pin and opens the
 *             place sheet, SNOW-1161; a resort with no pin opens the sheet
 *             all the same, without one). A resort row in the region sheet is
 *             already opened by map_region_sheet.js, which prevents the
 *             link's default; this module then only records the trail;
 *   weather — `window.pwaWeatherDetail.open(shortId, dateKey)`.
 *
 * A NEW PANEL OPENS AT THE HEIGHT THE OLD ONE HAD. On a phone a row or the
 * back pill tells map_sheet.js (`MapSheet.handOff`) the detent the open
 * sheet rests at, before anything closes it, and the next sheet opens at
 * that detent rather than its peek: a reader at FULL stays at FULL (Hugo,
 * 25 Oct 2026).
 *
 * A NEW PANEL OPENS AT ITS TOP. A sheet element is kept between openings,
 * and so is its scroll: the region sheet reopened for another region, or
 * reached from a resort, used to open part-way down where the last region
 * had been left (Hugo, 25 Oct 2026). Whenever an open sheet shows a place
 * it was not showing, or opens at all, it is scrolled back to the top. A
 * repaint of the same place — a change of day — keeps the reader's place.
 *
 * WHERE A PANEL SAYS WHAT IT IS. Each sheet body's root carries
 * `data-panel-key` ("region:CH-4115", "resort:verbier", "weather:ab12cd")
 * and `data-panel-label` (its name), so the trail can tell which panel is
 * showing without knowing any sheet's internals.
 */
(function () {
  'use strict';

  var SHEET_IDS = ['region-sheet', 'place-sheet', 'weather-sheet'];
  // SNOW-1359: the Favourites panel's "Today" rows open a region the same
  // way, so a row in it is followed too. It is not one of the place
  // sheets above (it has no panel key, gets no back pill and is not
  // scrolled), so it joins only the list a followed row may sit in.
  var LINK_SHEET_IDS = SHEET_IDS.concat(['favourite-sheet']);
  var found = document.getElementById('panel-trail-back-template');
  if (!(found instanceof HTMLTemplateElement)) return;
  var template = found;

  var FALLBACKS = {
    back: 'Back to {name}',
  };
  var strings = window.pwaStrings
    ? window.pwaStrings.read('panel-trail-strings-template', FALLBACKS)
    : FALLBACKS;

  /** @type {{from: {key: string, label: string}, to: string, shown: boolean}|null} */
  var trail = null;
  /** @type {string|null} */
  var dateKey = null;
  /**
   * The place each open sheet last showed, by sheet id; a closed sheet has
   * none, so the next opening counts as new.
   *
   * @type {Object<string, string|null>}
   */
  var shownKeys = {};

  /**
   * The sheets this module watches, as present in the page.
   *
   * @returns {HTMLElement[]}
   */
  function sheets() {
    return SHEET_IDS
      .map(function (id) { return document.getElementById(id); })
      .filter(function (el) { return el !== null; });
  }

  /**
   * The key a row opens: "<kind>:<id>".
   *
   * @param {Element} link A `[data-panel-open]` row.
   * @returns {string}
   */
  function keyFor(link) {
    return (link.getAttribute('data-panel-open') || '') + ':' + (link.getAttribute('data-panel-id') || '');
  }

  /**
   * Open the panel for a key, through that kind's own door.
   *
   * @param {string} key "<kind>:<id>".
   * @param {string} href The place's page, for a place the map cannot open.
   * @returns {void}
   */
  function openKey(key, href) {
    var cut = key.indexOf(':');
    var kind = key.slice(0, cut);
    var id = key.slice(cut + 1);
    if (!id) return;
    if (kind === 'region') {
      document.dispatchEvent(new CustomEvent('snowdesk:region-open', {
        detail: { region_id: id, href: href },
      }));
    } else if (kind === 'resort') {
      document.dispatchEvent(new CustomEvent('snowdesk:resort-open', {
        detail: { slug: id, href: href },
      }));
    } else if (kind === 'weather') {
      if (window.pwaWeatherDetail) window.pwaWeatherDetail.open(id, dateKey || undefined);
      else if (href) window.location.assign(href);
    }
  }

  /**
   * Build the back pill for the trail's origin.
   *
   * @param {{key: string, label: string}} from
   * @returns {HTMLElement}
   */
  function buildPill(from) {
    var fragment = /** @type {DocumentFragment} */ (template.content.cloneNode(true));
    var pill = /** @type {HTMLElement} */ (fragment.querySelector('[data-panel-back]'));
    var name = pill.querySelector('[data-panel-back-name]');
    if (name) name.textContent = from.label;
    pill.setAttribute('aria-label', strings.back.replace('{name}', from.label));
    return pill;
  }

  /**
   * Put the pill on the open panel the trail points at and take it off
   * every other one. A trail whose panel has been shown and is no longer
   * showing — another place painted into it, or it closed — is spent.
   *
   * @returns {void}
   */
  function sync() {
    var showing = false;
    sheets().forEach(function (sheet) {
      var body = sheet.querySelector('[data-panel-key]');
      var pill = sheet.querySelector('[data-panel-back]');
      toTopOnNewPlace(sheet, body);
      var match = trail !== null && body !== null && !sheet.hasAttribute('hidden') &&
        body.getAttribute('data-panel-key') === trail.to;
      if (match && trail && body) {
        showing = true;
        if (pill) return;
        var host = body.querySelector('[data-sheet-peek]') || body;
        host.insertBefore(buildPill(trail.from), host.firstChild);
        return;
      }
      if (pill) pill.remove();
    });
    if (!trail) return;
    if (showing) trail.shown = true;
    else if (trail.shown) trail = null;
  }

  /**
   * Scroll a sheet to its top when it opens, or when it starts showing a
   * place it was not showing.
   *
   * @param {HTMLElement} sheet
   * @param {Element|null} body The sheet's `[data-panel-key]` root, if painted.
   * @returns {void}
   */
  function toTopOnNewPlace(sheet, body) {
    if (sheet.hasAttribute('hidden')) {
      shownKeys[sheet.id] = null;
      return;
    }
    var key = body ? body.getAttribute('data-panel-key') : '';
    if (shownKeys[sheet.id] === key) return;
    shownKeys[sheet.id] = key;
    sheet.scrollTop = 0;
  }

  /**
   * The row a click is following, read before anything handles the click.
   *
   * @type {{key: string, href: string}|null}
   */
  var following = null;

  // CAPTURE: read the row, and record the trail, before any sheet's own
  // handler runs. The region sheet opens a resort in its own click handler,
  // and the one-object-sheet rule closes the region sheet — repainting it —
  // inside that same click; by the bubble phase the row is no longer in any
  // sheet and names no panel to come back to (Hugo, 25 Oct 2026).
  document.addEventListener('click', function (ev) {
    following = null;
    var target = /** @type {Element|null} */ (ev.target instanceof Element ? ev.target : null);
    if (!target) return;
    var link = target.closest('a[data-panel-open]');
    if (!link) return;
    var inSheet = LINK_SHEET_IDS.some(function (id) {
      var sheet = document.getElementById(id);
      return sheet !== null && sheet.contains(link);
    });
    if (!inSheet) return;
    var mouse = /** @type {MouseEvent} */ (ev);
    if (mouse.button !== 0 || mouse.metaKey || mouse.ctrlKey || mouse.shiftKey || mouse.altKey) return;

    var source = link.closest('[data-panel-key]');
    var to = keyFor(link);
    trail = source
      ? {
        from: {
          key: source.getAttribute('data-panel-key') || '',
          label: source.getAttribute('data-panel-label') || '',
        },
        to: to,
        shown: false,
      }
      : null;
    following = { key: to, href: /** @type {HTMLAnchorElement} */ (link).href };
    window.MapSheet?.handOff?.();
  }, true);

  // BUBBLE: open the place, unless a sheet's own handler (the region
  // sheet's resort rows) already has — then only the trail was ours.
  document.addEventListener('click', function (ev) {
    var target = /** @type {Element|null} */ (ev.target instanceof Element ? ev.target : null);
    if (!target) return;

    var back = target.closest('[data-panel-back]');
    if (back) {
      ev.preventDefault();
      if (!trail) return;
      window.MapSheet?.handOff?.();
      var from = trail.from;
      trail = null;
      back.remove();
      openKey(from.key, '');
      return;
    }

    var row = following;
    following = null;
    if (!row || ev.defaultPrevented) return;
    ev.preventDefault();
    openKey(row.key, row.href);
  });

  document.addEventListener('snowdesk:date-changed', function (ev) {
    dateKey = /** @type {CustomEvent} */ (ev).detail?.date || null;
  });

  if (typeof MutationObserver === 'function') {
    var observer = new MutationObserver(sync);
    sheets().forEach(function (sheet) {
      observer.observe(sheet, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['hidden'],
      });
    });
  }
}());
