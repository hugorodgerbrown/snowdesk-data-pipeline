/*
 * static/js/map_place_sheet.js — the place sheet a resort or pin tap opens
 * on a phone (SNOW-1100).
 *
 * A tap on a resort pin or a saved pin opened a 320px popup anchored above
 * it (map.js's mountDetailPopup), which on a phone covered the ground around
 * the place the reader had asked about. On a phone the tap now opens
 * #place-sheet at PEEK — the region's danger tile, the place's name, the
 * region, and the place's actions — which leaves that ground in view;
 * dragging up reveals the day's weather and the region's problems (HALF)
 * and the week, a resort's details and the links onward (FULL). The detents
 * are SNOW-1097's, bound by `window.MapSheet.attach` because the partial
 * declares them. At `sm` and up map.js keeps the popup and never calls in.
 *
 * map.js decides which surface a tap opens and calls
 * `window.pwaPlaceSheet.open({kind, id, name, createdAt})`; this module
 * fetches the body (apps/public/place_sheet.py) for the day the map is
 * showing and paints it. A newer open drops an older answer.
 *
 * THE ACTIONS. A resort's Save star is favourites.js's own [data-resort-star]
 * handler, unchanged; this module turns the star at once (the create is
 * queued, possibly offline) and repaints the body when the favourites
 * change. A saved pin's "…" menu carries the panel row's items: Share hands
 * the server-composed "<name> - <what3words link>" to window.pwaShare, as
 * the panel does; Rename edits the name in place through
 * window.pwaRowRenameCommit, as the panel does; Delete confirms and posts,
 * then closes the sheet. Each change announces `snowdesk:favourites-changed`,
 * which is what makes the map and the panel re-read the list.
 *
 * OFFLINE, a saved pin's sheet falls back to its name and saved time, the
 * popup's own content, which the map holds without a request.
 */

(function placeSheetInit() {
  'use strict';

  const sheet = document.getElementById('place-sheet');
  const mapEl = document.getElementById('map');
  if (!sheet || !mapEl || !window.MapSheet) return;

  const RESORT_URL = mapEl.dataset.resortSheetUrl || '';
  const FAVOURITE_URL = mapEl.dataset.favouriteSheetUrl || '';
  const NIL_UUID = '00000000-0000-0000-0000-000000000000';
  const DESKTOP_QUERY = '(min-width: 640px)';

  const STRINGS = self.pwaStrings.read('place-sheet-strings-template', {
    loading: 'Loading place details…',
    failed: 'Place details are unavailable offline.',
    'share-copied': 'Copied.',
    'share-failed': 'Could not share this pin.',
    'rename-failed': 'Could not rename this pin — please try again.',
    'delete-failed': 'Could not delete this pin — please try again.',
  });

  const controller = window.MapSheet.attach(sheet);
  const toast = window.MapSheet.toast || function () {};

  /** @type {?{kind: string, id: string, name?: string, createdAt?: string}} */
  let place = null;
  /** @type {?string} */
  let dateKey = null;
  let seq = 0;

  const isPhone = () =>
    typeof window.matchMedia !== 'function' || !window.matchMedia(DESKTOP_QUERY).matches;

  /**
   * Replace the sheet's body with a heading and an optional line, both
   * marked as PEEK.
   *
   * @param {string} title
   * @param {string} [line]
   * @returns {void}
   */
  const paintLines = (title, line) => {
    const wrap = document.createElement('div');
    wrap.setAttribute('data-sheet-peek', '');
    wrap.className = 'px-2';
    const h = document.createElement('p');
    h.className = title ? 'truncate text-base font-semibold text-text-1' : 'text-sm text-text-2';
    h.textContent = title || line || '';
    wrap.appendChild(h);
    if (title && line) {
      const p = document.createElement('p');
      p.className = 'text-meta text-text-2';
      p.textContent = line;
      wrap.appendChild(p);
    }
    sheet.replaceChildren(wrap);
  };

  /** @returns {string} The body's URL for the open place, or "". */
  const urlFor = () => {
    if (!place) return '';
    let url = '';
    if (place.kind === 'resort' && RESORT_URL) {
      url = RESORT_URL.replace('__SLUG__', encodeURIComponent(place.id));
    } else if (place.kind === 'favourite' && FAVOURITE_URL) {
      url = FAVOURITE_URL.replace(NIL_UUID, encodeURIComponent(place.id));
    }
    if (url && dateKey) url += '?d=' + encodeURIComponent(dateKey);
    return url;
  };

  /**
   * Fetch and paint the body for the open place. Over a plain fetch rather
   * than htmx.ajax so a response that lost the race to a newer tap can be
   * dropped before it paints; the body is server-rendered with autoescaping
   * on, the grounds every sheet on the map injects HTML on.
   *
   * @returns {Promise<void>}
   */
  const load = () => {
    const url = urlFor();
    if (!url) return Promise.resolve();
    const mine = ++seq;
    const current = place;
    if (!sheet.querySelector('[data-testid="place-sheet-body"]')) {
      paintLines(current && current.name ? current.name : '', STRINGS.loading);
    }
    return fetch(url, { headers: { 'HX-Request': 'true' }, credentials: 'same-origin' })
      .then((resp) => {
        if (!resp.ok) throw new Error('place sheet ' + resp.status);
        return resp.text();
      })
      .then((html) => {
        if (mine !== seq || !controller.isOpen()) return;
        sheet.innerHTML = html;
      })
      .catch(() => {
        if (mine !== seq || !controller.isOpen()) return;
        // Offline, a saved pin still has what its popup showed: its name
        // and when it was saved, both on the feature the map holds.
        if (current && current.kind === 'favourite') {
          const saved = window.snowdeskMapFormat?.relativeTime?.(current.createdAt || '') || '';
          paintLines(current.name || '', saved || STRINGS.failed);
          return;
        }
        paintLines('', STRINGS.failed);
      });
  };

  /**
   * Open the sheet for one place. Called by map.js on a phone only.
   *
   * @param {{kind: string, id: string, name?: string, createdAt?: string}} next
   * @returns {boolean} Whether the sheet took the tap.
   */
  const open = (next) => {
    if (!next || !next.id || !isPhone()) return false;
    place = next;
    // Another place's body must not show under the new one's loading line.
    if (controller.isOpen()) sheet.replaceChildren();
    load();
    if (!controller.isOpen()) controller.open();
    return true;
  };

  /** @returns {void} */
  const close = () => {
    seq += 1;
    place = null;
    if (controller.isOpen()) controller.close();
  };

  // ---- the actions -------------------------------------------------------

  /** @returns {string} The CSRF token the body's delete form carries. */
  const csrfToken = () => {
    const input = sheet.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? /** @type {HTMLInputElement} */ (input).value : '';
  };

  const announceChanged = () => {
    document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
  };

  sheet.addEventListener('click', (ev) => {
    const target = /** @type {HTMLElement} */ (ev.target);

    // A resort's star: favourites.js owns the write (a document listener,
    // which runs after this one). Turn the star now, since the create is
    // queued and the body cannot be re-read until it lands — but only when
    // that handler will queue it (on a device that cannot, it toasts and
    // saves nothing). The star stays disabled until the drained queue's
    // `snowdesk:favourites-changed` repaints the body, so a second tap
    // cannot queue a second create.
    const star = /** @type {?HTMLButtonElement} */ (target.closest?.('[data-resort-star]'));
    if (star && star.getAttribute('data-favourited') !== 'true') {
      if (!window.MapSheet.canQueueMutations?.()) return;
      star.setAttribute('aria-pressed', 'true');
      star.setAttribute('aria-busy', 'true');
      star.querySelector('svg')?.setAttribute('fill', 'currentColor');
      // After this click has reached favourites.js's document listener.
      setTimeout(() => { star.disabled = true; }, 0);
      return;
    }

    const share = target.closest?.('[data-favourite-share]');
    if (share) {
      const text = share.getAttribute('data-favourite-share-text');
      if (!text || !window.pwaShare) return;
      window.pwaTelemetry?.emit('map.favourite.shared', {});
      window.pwaShare
        .shareOrCopy(text, undefined, 'text')
        .then((outcome) => {
          if (outcome === 'copied') toast(STRINGS['share-copied']);
          else if (outcome === 'failed') toast(STRINGS['share-failed']);
        })
        .catch(() => toast(STRINGS['share-failed']));
      return;
    }

    if (target.closest?.('[data-row-rename]') && window.pwaRowRenameCommit) {
      const holder = document.querySelector('[data-favourite-rename-url-template]');
      window.pwaRowRenameCommit.handleClick(ev, {
        uuidAttribute: 'data-favourite-rename',
        urlTemplate: holder ? holder.getAttribute('data-favourite-rename-url-template') : '',
        csrfToken: csrfToken,
        // The announcement re-reads the body (the listener below).
        onCommitted: announceChanged,
        onFailed: () => toast(STRINGS['rename-failed']),
      });
    }
  });

  // Delete is the row menu's htmx form, but htmx never processed this body
  // (it arrives over fetch), so the submit is taken here: confirm with the
  // form's own question, post, then close — the pin is gone.
  sheet.addEventListener('submit', (ev) => {
    const form = /** @type {HTMLFormElement} */ (ev.target);
    if (!form.matches || !form.matches('[data-row-remove]')) return;
    ev.preventDefault();
    const question = form.getAttribute('hx-confirm');
    if (question && !window.confirm(question)) return;
    fetch(form.getAttribute('hx-post') || '', {
      method: 'POST',
      headers: { 'HX-Request': 'true', 'X-CSRFToken': csrfToken() },
      credentials: 'same-origin',
    })
      .then((resp) => {
        if (!resp.ok) throw new Error('delete failed');
        window.pwaTelemetry?.emit('map.favourite.deleted', {});
        announceChanged();
        close();
      })
      .catch(() => toast(STRINGS['delete-failed']));
  });

  // A queued resort save lands, or a pin changes elsewhere: re-read.
  document.addEventListener('snowdesk:favourites-changed', () => {
    if (controller.isOpen() && place) load();
  });
  // favourites.js closes the resort popup after a star press; the sheet
  // stays, and repaints when the change lands (above).

  document.addEventListener('snowdesk:date-changed', (ev) => {
    dateKey = /** @type {CustomEvent} */ (ev).detail?.date || null;
    if (controller.isOpen()) load();
  });

  window.addEventListener('resize', () => {
    if (controller.isOpen() && !isPhone()) close();
  });

  window.pwaPlaceSheet = Object.freeze({
    open: open,
    close: close,
    isOpen: () => controller.isOpen(),
  });
}());
