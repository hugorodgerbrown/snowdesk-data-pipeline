/*
 * static/js/map_region_panel.js — the region label in search, the pin
 * roundel and the pinned-regions list (SNOW-801, SNOW-814; the panel itself
 * and the visible chip retired by SNOW-1173).
 *
 * The file keeps its SNOW-801 name, though the panel it was named for is
 * gone. Until SNOW-1173 the #region-readout chip at the map's top-left
 * disclosed a dropdown, #region-panel, carrying the region's summary, its
 * resorts, an "Open bulletin for <date>" link and the user's pinned regions.
 * On a phone the chip was already hidden and every selection opened the
 * region sheet (static/js/map_region_sheet.js), which carries the same facts
 * in full; on a desktop the dropdown was a second, smaller copy of that
 * sheet, and a region tap opened nothing while a resort or a weather tap
 * opened the inset panel. Hugo, 21 Oct: "Weather and Resort both open the
 * overlay on desktop, but Region still opens the small dropdown top left."
 *
 * So the sheet now opens on every width, and what is left here is:
 *
 *   - THE CHIP IS FOLDED INTO SEARCH. With the dropdown gone the chip was a
 *     label, so on a desktop it moved into the search box, which moved to
 *     the top-left where the chip was (Hugo, 21 Oct: "If the region chip is
 *     essentially a label, can we combine it into the search box"). The
 *     ribbon that held the chip, its star and its download is not drawn on
 *     any width now — the region sheet's header carries the star and the
 *     download — but it stays in the DOM: map.js and map_season_ribbon.js
 *     still write the selection's name and the day's swatch into
 *     #region-readout, and this module mirrors those into the search box's
 *     #search-region-label, so the two writers did not need a second
 *     target. A press on the label opens search (map.js), whose empty field
 *     lists the pinned regions; picking the region already selected reopens
 *     its sheet.
 *
 *   - THE PIN ROUNDEL (SNOW-814) — the star in the (undrawn) ribbon header
 *     (public/partials/_map_region_pin_control.html), which the region
 *     sheet's header proxies on every width. It lives with the pinned list
 *     because the roundel's state and the list are the SAME fact: "is this
 *     region in my list". One loader, one derivation, and they cannot
 *     drift.
 *
 *   - THE PINNED-REGIONS LIST, loaded over HTMX from favourites:region_list
 *     into a hidden host in the ribbon (#region-pin-rows) and published as
 *     window.pwaPinnedRegions, which search lists before anything is typed.
 *     That was the phone's arrangement from 16 Oct (search replaced the chip
 *     there); SNOW-1173 made it every width's, so a desktop finds its pinned
 *     regions where a phone does, and picking one selects it and opens the
 *     sheet like any other selection.
 */

(function regionPanelInit() {
  'use strict';

  const chip = document.getElementById('region-readout');
  const pinnedRows = document.getElementById('region-pin-rows');
  if (!chip || !pinnedRows) return;

  // SNOW-814: absent for a visitor — there is no list to load.
  const PIN_LIST_URL = pinnedRows.dataset.regionPinListUrl || '';
  // The toggle endpoint with the codebase's 'XX-0000' region-id token in it.
  const PIN_TOGGLE_URL_TEMPLATE = pinnedRows.dataset.regionPinToggleUrl || '';
  const pinControl = document.getElementById('map-region-pin-control');
  const label = document.getElementById('search-region-label');

  /** @type {?string} The region the chip names, or null. */
  let currentRegionId = null;

  /**
   * Read Django's CSRF cookie.
   *
   * A local copy rather than a shared helper because this is the only
   * write this module makes; static/js/favourites.js keeps its own for the
   * same reason.
   *
   * @returns {string} The token, or '' when the cookie is absent.
   */
  const readCsrfToken = () => {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : '';
  };

  // Server-translated strings. `makemessages` does not scan JavaScript, so a
  // literal here would ship as English to every locale — the i18n-lint rule.
  const STRINGS = self.pwaStrings.read('region-panel-strings-template', {
    'pin-region': 'Pin this region',
    'unpin-region': 'Unpin this region',
    'pin-failed': "That region couldn't be pinned. Try again.",
  });

  /**
   * Whether the loaded list holds a row for ``regionId``.
   *
   * The list IS the state. Reading it rather than tracking a flag is what
   * keeps the roundel and the list from disagreeing: an unpin, a pin and a
   * reload all change one thing.
   *
   * @param {?string} regionId An EAWS micro-region id, or null.
   * @returns {boolean} True when the region is pinned.
   */
  const isPinned = (regionId) => {
    if (!regionId) return false;
    // Compared as attribute VALUES rather than built into a selector: a
    // region id in a selector string needs CSS.escape, which jsdom does not
    // implement.
    return [...pinnedRows.querySelectorAll('[data-row-focus-region]')].some(
      (button) => button.getAttribute('data-row-focus-region') === regionId,
    );
  };

  /**
   * Put the pin roundel into the state the current selection implies.
   *
   * Three states, and the disabled one is honest rather than hidden: with
   * no region selected there is nothing to pin, so the control says so and
   * refuses the press — the same treatment the download roundel beside it
   * gives the same situation.
   *
   * A visitor's control is an <a> to the sign-in page rendered in its
   * 'signin' state, which nothing here touches.
   *
   * @returns {void}
   */
  const syncPinControl = () => {
    if (!pinControl || pinControl.dataset.pinState === 'signin') return;
    if (!currentRegionId) {
      pinControl.dataset.pinState = 'no-region';
      pinControl.setAttribute('aria-disabled', 'true');
      pinControl.setAttribute('aria-pressed', 'false');
      return;
    }
    const pinned = isPinned(currentRegionId);
    pinControl.dataset.pinState = pinned ? 'pinned' : 'idle';
    pinControl.setAttribute('aria-disabled', 'false');
    pinControl.setAttribute('aria-pressed', pinned ? 'true' : 'false');
    const label = pinned ? STRINGS['unpin-region'] : STRINGS['pin-region'];
    pinControl.setAttribute('aria-label', label);
    pinControl.setAttribute('title', label);
  };

  /**
   * (Re)load the pinned-regions rows from favourites:region_list.
   *
   * Over HTMX rather than `fetch` + innerHTML because the endpoint is
   * behind `@require_htmx` (invariant 4), and its rows are the server's own
   * (includes/_ugc_panel_row.html), so the ids read off them are the list
   * the user would see anywhere else.
   *
   * @returns {Promise<void>} Settles once the rows are in the document.
   */
  const loadPins = () => {
    if (!PIN_LIST_URL || typeof htmx === 'undefined') return Promise.resolve();
    // `htmx.ajax` resolves once the swap has settled.
    return htmx
      .ajax('GET', PIN_LIST_URL, { target: pinnedRows, swap: 'innerHTML' })
      .then(() => {
        syncPinControl();
      });
  };

  /**
   * Copy what the chip says onto search's region label: the selection's
   * name and the day's danger swatch, or the search prompt with nothing
   * selected.
   *
   * The chip stays the one place map.js and map_season_ribbon.js write to
   * (see the header); this follows it rather than repeating their logic.
   *
   * @returns {void}
   */
  const mirrorLabel = () => {
    if (!label) return;
    const name = label.querySelector('[data-search-region-name]');
    const swatch = /** @type {HTMLElement|null} */ (
      label.querySelector('[data-search-region-swatch]')
    );
    const hasRegion = chip.classList.contains('has-region');
    const leaf = chip.querySelector('.region-readout-leaf')?.textContent || '';
    const chipSwatch = /** @type {HTMLElement|null} */ (
      chip.querySelector('.region-readout-swatch')
    );
    label.classList.toggle('has-region', hasRegion);
    if (name) name.textContent = hasRegion ? leaf : label.dataset.prompt || '';
    if (swatch) {
      swatch.hidden = !hasRegion;
      swatch.style.background = hasRegion && chipSwatch ? chipSwatch.style.background : '';
    }
  };
  if (label && typeof MutationObserver === 'function') {
    new MutationObserver(mirrorLabel).observe(chip, {
      attributes: true,
      attributeFilter: ['class', 'style'],
      characterData: true,
      childList: true,
      subtree: true,
    });
  }

  document.addEventListener('snowdesk:region-selected', (ev) => {
    currentRegionId = ev.detail?.region_id || null;
    syncPinControl();
    // The region sheet opens with this selection and carries the star, so
    // a list that failed to load is retried now rather than left wrong.
    ensurePins();
  });

  // The pin roundel. Online-only and outside the mutation queue, like the
  // pill it replaced: a region pin has no coordinate, so there is nothing to
  // draw optimistically and nothing for a replay to land in.
  //
  // The roundel flips from the RESPONSE and the list reloads behind it. The
  // response is the authority either way — it is the write that just
  // happened — and taking it directly is what makes the control answer on
  // the same frame as the press rather than a request later.
  pinControl?.addEventListener('click', (ev) => {
    if (pinControl.tagName === 'A') return;
    ev.preventDefault();
    if (!currentRegionId || pinControl.dataset.pinBusy === 'true') return;
    if (!PIN_TOGGLE_URL_TEMPLATE) return;
    pinControl.dataset.pinBusy = 'true';
    fetch(PIN_TOGGLE_URL_TEMPLATE.replace('XX-0000', encodeURIComponent(currentRegionId)), {
      method: 'POST',
      headers: {
        // favourite_region_toggle is @require_htmx; this is a plain fetch.
        'HX-Request': 'true',
        'X-CSRFToken': readCsrfToken(),
      },
    })
      .then((resp) => {
        if (!resp.ok) throw new Error('region pin ' + resp.status);
        return resp.json();
      })
      .then((data) => {
        delete pinControl.dataset.pinBusy;
        pinControl.dataset.pinState = data.pinned ? 'pinned' : 'idle';
        pinControl.setAttribute('aria-pressed', data.pinned ? 'true' : 'false');
        const label = data.pinned ? STRINGS['unpin-region'] : STRINGS['pin-region'];
        pinControl.setAttribute('aria-label', label);
        pinControl.setAttribute('title', label);
        window.pwaTelemetry?.emit('map.region.pin_toggled', { pinned: !!data.pinned });
        loadPins();
      })
      .catch(() => {
        delete pinControl.dataset.pinBusy;
        // Put the control back to what the list still says, so a failed
        // toggle cannot leave a star claiming a pin that was never written.
        syncPinControl();
        window.MapSheet?.toast?.(STRINGS['pin-failed']);
      });
  });

  // Search lists the pinned regions before anything is typed. It reads the
  // rows this module loaded rather than fetching its own copy, so the list
  // in search and the pin roundel are one fact. `ids` is in list order;
  // `ready` settles once the first load has.
  //
  // A failed load is retried on the next ask. Nothing else ever reloads the
  // list once boot is past — the chip's panel retried on each open until
  // SNOW-1173 retired it — so a transient failure at boot would otherwise
  // leave search with no pinned regions all session, and the star offering
  // to pin a region that is pinned (whose toggle would unpin it).
  /** @type {Promise<void>} */
  let pinsReady = Promise.resolve();
  // True once a load has come back 2xx; htmx resolves its promise for an
  // error response too, so the request's own outcome is what says.
  let pinsLoaded = false;
  let pinsLoading = false;
  pinnedRows.addEventListener('htmx:afterRequest', (ev) => {
    pinsLoaded = pinsLoaded || !!ev.detail?.successful;
  });
  const ensurePins = () => {
    if (!PIN_LIST_URL || pinsLoaded || pinsLoading) return pinsReady;
    pinsLoading = true;
    pinsReady = loadPins()
      .catch(() => {})
      .finally(() => {
        pinsLoading = false;
      });
    return pinsReady;
  };
  window.pwaPinnedRegions = Object.freeze({
    ids: () =>
      [...pinnedRows.querySelectorAll('[data-row-focus-region]')]
        .map((button) => button.getAttribute('data-row-focus-region'))
        .filter(Boolean),
    ready: ensurePins,
  });

  mirrorLabel();
  // Load the list at boot for a signed-in user, not on first use: the pin
  // roundel is in the header from the first paint and its state comes from
  // this list, so deferring the fetch would leave the star lying about
  // whichever region the page opened on.
  ensurePins();
  syncPinControl();
}());
