/*
 * static/js/map_region_panel.js — the region chip, its pin roundel and the
 * pinned-regions list (SNOW-801, SNOW-814; the panel itself retired by
 * SNOW-1173).
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
 *   - THE CHIP REOPENS THE SHEET. It still names the selection, and a press
 *     opens the region sheet for it, or closes it when it is already open —
 *     the way back to a region whose sheet was closed. With nothing selected
 *     there is nothing for it to open, so it is disabled, for a visitor and
 *     a signed-in user alike; the pinned list it used to open onto is in
 *     search on every width now (below).
 *
 *   - THE PIN ROUNDEL (SNOW-814) — the star between the chip and the
 *     download control (public/partials/_map_region_pin_control.html). It
 *     lives with the pinned list because the roundel's state and the list
 *     are the SAME fact: "is this region in my list". One loader, one
 *     derivation, and they cannot drift. The region sheet's header proxies
 *     this roundel rather than owning a star of its own.
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
  const sheet = document.getElementById('region-sheet');

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
   * Mirror the region sheet's open state onto the chip's ARIA, so the
   * chevron (.region-readout[aria-expanded="true"] in map.css) and a
   * screen reader both say whether the surface the chip opens is showing.
   *
   * @returns {void}
   */
  const syncExpanded = () => {
    const open = !!sheet && !sheet.hasAttribute('hidden');
    chip.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  if (sheet && typeof MutationObserver === 'function') {
    new MutationObserver(syncExpanded).observe(sheet, {
      attributes: true,
      attributeFilter: ['hidden'],
    });
  }

  // SNOW-1173: the chip is inert only when there is nothing selected to
  // open. It used to stay pressable for a signed-in user with nothing
  // selected, because it opened onto their pinned regions; that list is in
  // search now, on every width.
  const syncEnabled = () => {
    chip.disabled = !currentRegionId;
  };

  chip.addEventListener('click', () => {
    const regionSheet = window.pwaRegionSheet;
    if (!regionSheet || !currentRegionId) return;
    if (regionSheet.isOpen()) regionSheet.close();
    else regionSheet.reopen();
  });

  document.addEventListener('snowdesk:region-selected', (ev) => {
    currentRegionId = ev.detail?.region_id || null;
    syncEnabled();
    syncPinControl();
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
  /** @type {Promise<void>} */
  let pinsReady = Promise.resolve();
  window.pwaPinnedRegions = Object.freeze({
    ids: () =>
      [...pinnedRows.querySelectorAll('[data-row-focus-region]')]
        .map((button) => button.getAttribute('data-row-focus-region'))
        .filter(Boolean),
    ready: () => pinsReady,
  });

  syncEnabled();
  syncExpanded();
  // Load the list at boot for a signed-in user, not on first use: the pin
  // roundel is in the header from the first paint and its state comes from
  // this list, so deferring the fetch would leave the star lying about
  // whichever region the page opened on.
  if (PIN_LIST_URL) pinsReady = loadPins().catch(() => {});
  syncPinControl();
}());
