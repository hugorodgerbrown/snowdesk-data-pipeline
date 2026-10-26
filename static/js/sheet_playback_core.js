// @ts-check
/*
 * static/js/sheet_playback_core.js — what an object sheet does while the
 * timeline plays (SNOW-1239).
 *
 * The timelapse announces a `snowdesk:date-changed` every 200 ms frame. The
 * weather, place and region sheets each reloaded on every one, so a sheet
 * left open during playback fired a server-rendered request per frame and
 * cancelled none of them. Now the sheet PAUSES: it stays open, says
 * "Playback in progress" in a band over its dimmed body, asks for nothing,
 * and loads once — for the day playback stopped on — when it stops.
 *
 * The three sheet modules (map_weather_detail.js, map_place_sheet.js,
 * map_region_sheet.js) share these pieces rather than each writing them:
 *
 *   - ``createGuard`` — one request in flight per sheet. ``begin`` aborts
 *     the previous request and hands out a signal for the next;
 *     ``invalidate`` aborts it without starting another (a close, a pause),
 *     so a late answer can never paint.
 *   - ``shouldReload`` — a change of day reloads the sheet unless it is a
 *     timelapse frame during playback.
 *   - ``isPlaying`` — the map state's playback flag, false when the map
 *     state is absent.
 *   - ``isAbort`` — whether a rejection is the guard's own abort, which a
 *     sheet treats as silence rather than "couldn't be loaded".
 *   - ``markPaused`` — the band and the dim. Both live ON the sheet element
 *     (``data-sheet-paused`` and one band child), not on a wrapper, because
 *     every sheet paints by replacing its children and a wrapper would go
 *     with them; a module re-applies it after any paint made while paused.
 *     The dim is one data-attribute rule in static/css/map.css.
 *
 * The band is cloned from ``#sheet-playback-band-template`` (map.html), so
 * its copy is translated; the English fallback is for a page that somehow
 * renders without it. When the sheet holds no other ``data-sheet-peek``
 * element — a sheet first opened during playback — the band is the peek,
 * so map_sheet.js measures a real row rather than its fallback height.
 */
(function () {
  'use strict';

  const TEMPLATE_ID = 'sheet-playback-band-template';
  const BAND_ATTR = 'data-sheet-playback-band';
  const PEEK_ATTR = 'data-sheet-peek';

  /**
   * One request in flight at a time, cancellable.
   *
   * Tolerates a runtime with no ``AbortController``: the token alone then
   * keeps a late answer from painting, and the request simply runs out.
   *
   * @returns {{
   *   begin: function(): {signal: (AbortSignal|undefined), isCurrent: function(): boolean},
   *   invalidate: function(): void,
   * }}
   */
  function createGuard() {
    let token = 0;
    /** @type {?AbortController} */
    let controller = null;

    /** Abort the request in flight, if there is one. */
    function abort() {
      if (controller) controller.abort();
      controller = null;
    }

    return {
      /**
       * Start a request, superseding the one before it.
       *
       * @returns {{signal: (AbortSignal|undefined), isCurrent: function(): boolean}}
       */
      begin() {
        abort();
        token += 1;
        const mine = token;
        controller = typeof AbortController === 'function' ? new AbortController() : null;
        return {
          signal: controller ? controller.signal : undefined,
          isCurrent: () => mine === token,
        };
      },
      /** Abort the request in flight and start none. */
      invalidate() {
        token += 1;
        abort();
      },
    };
  }

  /**
   * Whether a change of day should reload an open sheet.
   *
   * @param {unknown} source The event's ``detail.source``.
   * @param {boolean} playing Whether the timeline is playing.
   * @returns {boolean} False only for a timelapse frame during playback.
   */
  function shouldReload(source, playing) {
    return !(source === 'timelapse' && playing);
  }

  /**
   * Whether the timeline is playing.
   *
   * @returns {boolean}
   */
  function isPlaying() {
    return window.snowdeskMapState?.isPlaying === true;
  }

  /**
   * Whether a rejection is an abort the guard asked for.
   *
   * @param {unknown} err
   * @returns {boolean}
   */
  function isAbort(err) {
    return !!err && /** @type {{name?: unknown}} */ (err).name === 'AbortError';
  }

  /**
   * A fresh band: the template's, or a plain line when the page has none.
   *
   * @returns {HTMLElement}
   */
  function buildBand() {
    const template = /** @type {HTMLTemplateElement|null} */ (document.getElementById(TEMPLATE_ID));
    const source = template && template.content ? template.content.firstElementChild : null;
    if (source) {
      const band = /** @type {HTMLElement} */ (source.cloneNode(true));
      band.setAttribute(BAND_ATTR, '');
      return band;
    }
    const strings = window.pwaStrings
      ? window.pwaStrings.read(TEMPLATE_ID, { band: 'Playback in progress' })
      : { band: 'Playback in progress' };
    const band = document.createElement('p');
    band.setAttribute(BAND_ATTR, '');
    band.className = 'px-2 py-2 text-xs font-mono uppercase tracking-widest text-text-3';
    band.textContent = strings.band;
    return band;
  }

  /**
   * Show or clear the paused state on one sheet.
   *
   * Idempotent, so a module calls it after every paint and on every frame:
   * an existing band is kept (only its peek mark follows the content), so
   * playback does not churn the sheet's children 5 times a second.
   *
   * @param {?HTMLElement} sheetEl The sheet container.
   * @param {boolean} on Whether the sheet is paused.
   * @returns {void}
   */
  function markPaused(sheetEl, on) {
    if (!sheetEl) return;
    const existing = sheetEl.querySelector(':scope > [' + BAND_ATTR + ']');
    if (!on) {
      if (existing) existing.remove();
      sheetEl.removeAttribute('data-sheet-paused');
      sheetEl.removeAttribute('aria-busy');
      return;
    }
    sheetEl.setAttribute('data-sheet-paused', '');
    sheetEl.setAttribute('aria-busy', 'true');
    const band = existing || buildBand();
    // The band is the peek only when nothing else is: a body's own peek
    // row stays what the sheet rests on.
    band.removeAttribute(PEEK_ATTR);
    if (!sheetEl.querySelector('[' + PEEK_ATTR + ']')) band.setAttribute(PEEK_ATTR, '');
    if (existing) return;
    // After the drag handle, which map_sheet.js keeps as the first child.
    const handle = sheetEl.querySelector(':scope > [data-sheet-handle]');
    sheetEl.insertBefore(band, handle ? handle.nextSibling : sheetEl.firstChild);
  }

  window.pwaSheetPlayback = Object.freeze({
    createGuard: createGuard,
    shouldReload: shouldReload,
    isPlaying: isPlaying,
    isAbort: isAbort,
    markPaused: markPaused,
  });
})();
