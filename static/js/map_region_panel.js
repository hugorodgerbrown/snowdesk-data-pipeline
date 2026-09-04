/*
 * static/js/map_region_panel.js — the region + date surface (SNOW-801).
 *
 * The #region-readout chip at the map's top-left is a disclosure button;
 * this module is what it discloses. The panel answers "what is this region,
 * on this day" in one place: the danger chip and geographic breadcrumb the
 * map popup used to carry, the bulletin link, and the resorts inside the
 * region.
 *
 * WHY A PANEL ON THE CHIP RATHER THAN A MARK ON THE MAP. Three map-anchored
 * treatments were tried and rejected — a chevron roundel, the EAWS danger
 * pictogram, and an (i) roundel, each on the region's centroid. The problem
 * was never the mark: a second affordance on the map led to a restatement of
 * what the chip already said (name, danger level, a way to the bulletin), so
 * nothing put on the map for it could feel earned. Hanging the surface off
 * the chip means the affordance already exists, is already discoverable, and
 * the panel has room to carry what the chip cannot.
 *
 * The view-bulletin roundel that used to sit beside the chip is gone: this
 * panel carries an "Open bulletin for <date>" link, so the roundel was a
 * second control to a single destination. The download roundel stays — it
 * has a destination of its own.
 *
 * Content comes from /api/region/<id>/summary/ — the same server-rendered,
 * autoescaped HTML the MapLibre popup used, so there is one definition of
 * what a region summary says — plus /api/resorts-by-region/ for the resort
 * list. Both are already fetched by the map for other reasons and are
 * cacheable, so opening the panel is usually free.
 *
 * THIS MODULE ALSO OWNS THE PIN ROUNDEL (SNOW-814) — the star between the
 * chip and the download control (public/partials/_map_region_pin_control.html).
 * It sits here rather than in a module of its own because the roundel's
 * state and the pinned list below are the SAME fact: "is this region in my
 * list". Two owners would be two answers, which is exactly what the pill it
 * replaced produced — a server-rendered "★ Pinned" beside a list that had
 * been reloaded since. One loader, one derivation, and they cannot drift.
 *
 * PINNED REGIONS LIVE HERE (SNOW-814). The panel's last section is the
 * user's own pinned regions, loaded over HTMX from favourites:region_list.
 * They were rows in the pins sheet from SNOW-802 until this ticket, beside
 * dropped pins and saved resorts, and none of that sheet's affordances
 * reached them — no map layer to switch on, no rename, no forecast card, no
 * coordinate to fly to. The chip that names the selected region is where a
 * list of regions belongs: the pin control is already in this panel, so
 * pinning a region and finding the ones you pinned are one surface.
 *
 * It also gives the chip something to disclose with NOTHING selected, which
 * it did not have — it was simply disabled, a control that looked pressable
 * and was not. A signed-in user now opens it onto their pinned regions; a
 * visitor still gets an inert chip, because for them the panel would be
 * empty.
 *
 * Pressing a pinned region's name SELECTS it — the hash, which is map.js's
 * own deep-link route into ``selectFeature`` — and then frames it through
 * ``window.pwaMapFocus.region``. Selecting rather than only framing is the
 * difference between a switcher and a bookmark list: a press that flew the
 * map to Zermatt while the chip above still read "Martigny-Verbier" would be
 * the panel contradicting its own header.
 *
 * NOT YET COVERED BY TESTS. There is no tests/js suite for this module, and
 * that is a gap rather than a decision — its logic (the render cache key,
 * the fetch sequencing, the open/close state) is exactly the kind jsdom can
 * hold, so it belongs in tests/js rather than tests/e2e. Tracked on SNOW-801.
 *
 * The content stops at the resorts list. Neighbouring regions' ratings and
 * the week's trend are the obvious next two; the ratings cache already holds
 * what the trend would need.
 */

(function regionPanelInit() {
  'use strict';

  const chip = document.getElementById('region-readout');
  const panel = document.getElementById('region-panel');
  const mapEl = document.getElementById('map');
  const ribbon = document.getElementById('season-ribbon');
  if (!chip || !panel || !mapEl) return;

  const SUMMARY_URL_TEMPLATE = mapEl.dataset.regionSummaryUrl || '';
  const RESORTS_URL = mapEl.dataset.resortsByRegionUrl || '/api/resorts-by-region/';
  // SNOW-814: absent for a visitor — there is no list to load, and no
  // pinned-regions section is rendered. See the partial's own note.
  const PIN_LIST_URL = panel.dataset.regionPinListUrl || '';
  // The toggle endpoint with the codebase's 'XX-0000' region-id token in it,
  // the same shape as the summary URL above.
  const PIN_TOGGLE_URL_TEMPLATE = panel.dataset.regionPinToggleUrl || '';
  const pinControl = document.getElementById('map-region-pin-control');
  const OVERLAY_NAME = 'region-panel';

  // The region and date the panel is currently showing, so a re-open with
  // nothing changed does not refetch, and a change while open repaints.
  let currentRegionId = null;
  let currentDate = null;
  let renderedKey = null;
  let resortsByRegion = null;
  // Invalidates an in-flight fetch when a newer one starts, mirroring
  // openRegionPopup's summarySeq guard in map.js.
  let seq = 0;

  const isOpen = () => !panel.hidden;

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

  /**
   * Close the panel and return the chip to its collapsed state.
   *
   * @returns {void}
   */
  const close = () => {
    if (!isOpen()) return;
    panel.hidden = true;
    chip.setAttribute('aria-expanded', 'false');
    ribbon?.classList.remove('is-open');
  };

  // Server-translated strings. `makemessages` does not scan JavaScript, so a
  // literal here would ship as English to every locale — the i18n-lint rule.
  // The fallbacks are not decoration: a page that renders this module without
  // the template must still put words on screen.
  const STRINGS = self.pwaStrings.read('region-panel-strings-template', {
    resorts: 'Resorts in this region',
    unavailable: 'Region details are unavailable offline.',
    pinned: 'Pinned regions',
    // Hyphenated, because the key IS the template's `data-string` value and
    // the two are compared literally (tests/test_js_strings_are_translatable).
    'pinned-failed': "Your pinned regions couldn't be loaded.",
    'pin-region': 'Pin this region',
    'unpin-region': 'Unpin this region',
    'pin-failed': "That region couldn't be pinned. Try again.",
  });

  /**
   * Build one titled section of the panel.
   *
   * @param {string} eyebrow The section's uppercase label.
   * @returns {HTMLDivElement} The section, with its eyebrow already in it.
   */
  const buildSection = (eyebrow) => {
    const section = document.createElement('div');
    section.className = 'region-panel-section';
    const label = document.createElement('p');
    label.className = 'region-panel-eyebrow';
    label.textContent = eyebrow;
    section.appendChild(label);
    return section;
  };

  // The pinned-regions section is built ONCE and moved between renders
  // rather than rebuilt with the rest of the panel. Its content does not
  // depend on the selected region or the displayed date — the two things
  // `render` keys on — so rebuilding it on a date scrub would refetch a list
  // that had not changed, and would throw away a row the user was mid-way
  // through removing.
  const pinnedSection = PIN_LIST_URL ? buildSection(STRINGS.pinned) : null;
  const pinnedRows = document.createElement('div');
  // A real attribute, because window.pwaRowRemoved.watch takes a SELECTOR:
  // its listeners are delegated from the document and outlive any element
  // reference this module holds.
  pinnedRows.setAttribute('data-region-pin-rows', '');
  if (pinnedSection) pinnedSection.appendChild(pinnedRows);
  // Whether the rows currently in `pinnedRows` are still true. A pin toggle
  // anywhere on the page invalidates them; the next open reloads.
  let pinsLoaded = false;

  /**
   * Whether the loaded list holds a row for ``regionId``.
   *
   * The list IS the state. Reading it rather than tracking a flag is what
   * keeps the roundel and the rows from disagreeing: an unpin from a row,
   * a pin from the roundel and a reload all change one thing.
   *
   * @param {?string} regionId An EAWS micro-region id, or null.
   * @returns {boolean} True when the region is pinned.
   */
  const isPinned = (regionId) => {
    if (!regionId) return false;
    // Compared as attribute VALUES rather than built into a selector: a
    // region id in a selector string needs CSS.escape, which jsdom does not
    // implement, so the tests/js suite this module needs would be testing a
    // different code path from the browser's.
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
   * Mark the pinned row for the region the panel is currently describing.
   *
   * The selected region is usually IN this list — pinning it is what the
   * panel's own control above does — so without a mark the same region is
   * named twice on one surface with nothing to tell the two apart. Marking
   * rather than hiding: a row that vanished when you selected it would make
   * the list reorder itself under the cursor, and "which of my regions am I
   * looking at" is a question the list should answer.
   *
   * The press stays live on the marked row: it recentres the map on the
   * region, which is worth having after the user has panned away with the
   * panel open. It is only not a change of subject.
   *
   * `aria-current="true"` rather than a class alone, so the answer is
   * available to a screen reader too.
   *
   * @returns {void}
   */
  const markCurrent = () => {
    pinnedRows.querySelectorAll('[data-row-focus-region]').forEach((button) => {
      const isCurrent = !!currentRegionId
        && button.getAttribute('data-row-focus-region') === currentRegionId;
      button.closest('li')?.classList.toggle('is-current', isCurrent);
      if (isCurrent) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
    });
  };

  /**
   * (Re)load the pinned-regions rows from favourites:region_list.
   *
   * Over HTMX rather than `fetch` + innerHTML, for the reason the pins
   * sheet loads its own list that way: each row arrives carrying a Remove
   * form, and htmx.ajax is what processes those into live requests. It is
   * also what keeps the endpoint behind `@require_htmx` (invariant 4).
   *
   * @returns {Promise<void>} Settles once the rows are in the document.
   */
  const loadPins = () => {
    if (!PIN_LIST_URL || typeof htmx === 'undefined') return Promise.resolve();
    pinsLoaded = true;
    // `htmx.ajax` resolves once the swap has settled. The completion is taken
    // from the promise rather than from an `htmx:afterSwap` listener on the
    // container: this call passes no source element, so htmx raises its
    // lifecycle events against document.body, and a listener bound to the
    // target never hears them.
    return htmx
      .ajax('GET', PIN_LIST_URL, { target: pinnedRows, swap: 'innerHTML' })
      .then(() => {
        markCurrent();
        syncPinControl();
      });
  };

  // A failed load must not leave the container empty. Empty is the SERVER's
  // way of saying "you have pinned nothing" (the list partial's own empty
  // state), and a request that merely failed has not established that — the
  // same distinction the pins sheet draws, and the reason its error line and
  // its empty state are two different sentences.
  //
  // Delegated from the document, and filtered on the target, for the reason
  // row_removed.js gives: htmx raises the lifecycle events for an `htmx.ajax`
  // call with no source element against document.body, so a listener bound to
  // the container never hears them.
  for (const name of ['htmx:responseError', 'htmx:sendError']) {
    document.addEventListener(name, (ev) => {
      if (ev.detail?.target !== pinnedRows) return;
      pinnedRows.textContent = '';
      const p = document.createElement('p');
      p.className = 'region-panel-empty';
      p.textContent = STRINGS['pinned-failed'];
      pinnedRows.appendChild(p);
      // Not loaded after all — the next open tries again rather than
      // leaving the failure on screen for the rest of the session.
      pinsLoaded = false;
    });
  }

  /**
   * Put the pinned-regions section into the panel, loading it if stale.
   *
   * The load happens HERE rather than in `open` because htmx swaps into an
   * element by walking the document: a target still detached from the tree
   * — which the section is until this append — has nowhere for the response
   * to land.
   *
   * @returns {void}
   */
  const attachPinned = () => {
    if (!pinnedSection) return;
    panel.appendChild(pinnedSection);
    if (!pinsLoaded) loadPins();
    else markCurrent();
  };

  /**
   * Render the panel body for one region and date.
   *
   * @param {string} regionId The EAWS region id.
   * @param {?string} dateKey The displayed date (YYYY-MM-DD), or null.
   * @returns {Promise<void>}
   */
  const render = async (regionId, dateKey) => {
    const key = `${regionId}|${dateKey || ''}`;
    if (key === renderedKey) return;

    // SNOW-814: nothing selected. The panel is then the pinned list alone —
    // no summary to fetch, no resorts to list — and it is reached only by a
    // signed-in user, since the chip is inert without a list to show.
    if (!regionId) {
      seq += 1;
      panel.textContent = '';
      attachPinned();
      renderedKey = key;
      return;
    }

    const mine = ++seq;
    let summaryHtml = '';
    try {
      let url = SUMMARY_URL_TEMPLATE.replace('XX-0000', encodeURIComponent(regionId));
      if (dateKey) url += '?d=' + encodeURIComponent(dateKey);
      const resp = await fetch(url, { headers: { Accept: 'application/json' } });
      if (mine !== seq) return;
      if (resp.ok) summaryHtml = (await resp.json()).html || '';
    } catch {
      // Offline, or the endpoint is unreachable. The panel still opens and
      // says so rather than hanging on an empty box.
    }
    if (mine !== seq) return;

    if (resortsByRegion === null) {
      try {
        const resp = await fetch(RESORTS_URL, { headers: { Accept: 'application/json' } });
        resortsByRegion = resp.ok ? await resp.json() : {};
      } catch {
        resortsByRegion = {};
      }
    }
    if (mine !== seq) return;

    panel.textContent = '';

    if (summaryHtml) {
      // Server-rendered by Django templates with autoescaping on — the same
      // HTML map.js hands to Popup.setHTML, and safe on the same grounds.
      const section = document.createElement('div');
      section.className = 'region-panel-section';
      section.innerHTML = summaryHtml;
      // The summary's own header is the danger chip and the region name —
      // exactly what the ribbon above is already showing. Dropping it is
      // what stops the surface saying the region's name twice. A real seam
      // in the server template (public/_region_tooltip.html), not a guess at
      // the markup: everything else in there (breadcrumb, bulletin link,
      // editorial) is content the ribbon has no room for.
      section.querySelector('.region-tooltip-header')?.remove();
      panel.appendChild(section);
    } else {
      const p = document.createElement('p');
      p.className = 'region-panel-empty';
      p.textContent = STRINGS.unavailable;
      panel.appendChild(p);
    }

    const resorts = (resortsByRegion && resortsByRegion[regionId]) || [];
    if (resorts.length) {
      const section = buildSection(STRINGS.resorts);
      const list = document.createElement('ul');
      list.className = 'region-panel-resorts';
      resorts.forEach((name) => {
        const li = document.createElement('li');
        li.textContent = name;
        list.appendChild(li);
      });
      section.appendChild(list);
      panel.appendChild(section);
    }

    // SNOW-814: last, and deliberately so. The panel is opened FROM the
    // selected region's name, so what that region is comes first; the list
    // of other regions is where you go when this one is not the one you
    // wanted. With nothing selected it is the whole panel (see the early
    // return above) and the question does not arise.
    attachPinned();

    renderedKey = key;
  };

  /**
   * Open the panel for the currently selected region.
   *
   * @returns {void}
   */
  const open = () => {
    // SNOW-814: a region is no longer required. With none selected the panel
    // is the pinned list, which is content in its own right; with neither a
    // region nor a list there is nothing to disclose and `syncEnabled` has
    // already made the chip inert.
    if (!currentRegionId && !pinnedSection) return;
    // Close every other map overlay first, so the two are never both on
    // screen for a frame. The registry owns the matrix; this surface only
    // states that it is opening.
    window.pwaMapOverlays?.opening(OVERLAY_NAME);
    panel.hidden = false;
    chip.setAttribute('aria-expanded', 'true');
    ribbon?.classList.add('is-open');
    render(currentRegionId, currentDate);
    // `render` short-circuits when nothing it keys on has changed, and a
    // re-open after a selection made while closed is exactly that case — so
    // the mark is refreshed here rather than only on the paths that repaint.
    markCurrent();
  };

  chip.addEventListener('click', (ev) => {
    // Without this the opening click reads as a click-away to the document
    // handler below and the panel closes on the same gesture that opened it.
    ev.stopPropagation();
    if (isOpen()) close(); else open();
  });

  // Outside click and Escape. Both only ever close THIS surface; the region
  // selection, the ribbon and the hash are untouched.
  //
  // A click on the MAP is not "outside". It arrives before the
  // snowdesk:region-selected it causes, so treating it as a dismissal
  // closed the panel on the very gesture that picked the region it should
  // have been updating. The map drives the panel through the selection
  // event instead: selecting a region repaints it, deselecting closes it.
  document.addEventListener('click', (ev) => {
    if (!isOpen()) return;
    if (panel.contains(ev.target)) return;
    if (mapEl.contains(ev.target)) return;
    close();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && isOpen()) {
      close();
      chip.focus();
    }
  });

  // SNOW-814: the chip is inert only when there is genuinely nothing behind
  // it — no region selected AND no pinned list to fall back on, which is a
  // visitor's state. A signed-in user's chip stays pressable with nothing
  // selected, because their pinned regions are what it opens onto. Before
  // this it was disabled whenever no region was selected, which made the
  // page's most prominent control dead at first paint.
  const syncEnabled = () => {
    chip.disabled = !currentRegionId && !pinnedSection;
  };

  document.addEventListener('snowdesk:region-selected', (ev) => {
    currentRegionId = ev.detail?.region_id || null;
    renderedKey = null;
    syncEnabled();
    syncPinControl();
    // Deselecting no longer closes a panel that still has something to say.
    // It repaints down to the pinned list instead — the same surface the
    // chip would open onto from here, so the two states cannot disagree.
    if (!currentRegionId && !pinnedSection) {
      close();
      return;
    }
    if (isOpen()) render(currentRegionId, currentDate);
  });

  document.addEventListener('snowdesk:date-changed', (ev) => {
    currentDate = ev.detail?.date || null;
    renderedKey = null;
    if (isOpen() && currentRegionId) render(currentRegionId, currentDate);
  });

  // SNOW-814: pressing a pinned region's name SELECTS it and frames it.
  //
  // Selection goes through the hash because that is map.js's own public way
  // in — `selectFeature` is closed over by the map IIFE, and the deep link
  // (`/#CH-4115`) is the contract it already honours, hashchange and all. So
  // the row press produces exactly the state a shared link would, including
  // the history entry, rather than a second private path into the same
  // machinery.
  //
  // Framing is a separate call rather than a consequence: `selectFeature`
  // only moves the camera when the map's autozoom preference is on, and
  // somebody who pressed a region's name has asked to be taken there
  // whatever that preference says — the rule every panel row follows
  // (static/js/row_focus.js).
  //
  // The panel stays OPEN, unlike the sheets' rows, which close on a focus
  // press. They are full-width docks on a phone and would hide the map they
  // just flew; this panel is a small surface at the top-left, the map is
  // visible around it, and it repaints to the region just selected — which
  // is the answer to "what did I just pick".
  panel.addEventListener('click', (ev) => {
    const button = ev.target.closest?.('[data-row-focus-region]');
    if (!button) return;
    const regionId = button.getAttribute('data-row-focus-region');
    if (!regionId) return;
    ev.preventDefault();
    if (regionId !== currentRegionId) window.location.hash = '#' + regionId;
    window.pwaMapFocus?.region?.(regionId);
  });

  // A pin toggled anywhere — this panel's own control, or the region popup's
  // — makes the list stale. Marking rather than reloading: the next open
  // fetches it, and a toggle made while the panel is open reloads at once so
  // the section cannot show a region the user has just unpinned.
  document.addEventListener('snowdesk:region-pin-changed', () => {
    pinsLoaded = false;
    loadPins();
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
        pinsLoaded = false;
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

  // A row unpinned by its own star empties one <li>; the empty state is a
  // server-side clause only a fresh response carries, so re-read the list.
  // The same hook, and the same reason, as the pins sheet's.
  //
  // Re-reading is also what moves the roundel: `loadPins` syncs it from the
  // rows that come back, so unpinning the selected region from its row
  // hollows the star in the header. Two controls for one fact, and one
  // loader deciding what that fact is.
  window.pwaRowRemoved?.watch('[data-region-pin-rows]', () => {
    pinsLoaded = false;
    loadPins();
  });

  window.pwaMapOverlays?.register(OVERLAY_NAME, { isOpen, close });
  syncEnabled();
  // Load the list at boot for a signed-in user, not on first open: the pin
  // roundel is in the header from the first paint and its state comes from
  // this list, so deferring the fetch would leave the star lying about
  // whichever region the page opened on.
  if (PIN_LIST_URL) loadPins();
  syncPinControl();
}());
