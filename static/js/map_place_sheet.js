/*
 * static/js/map_place_sheet.js — the place sheet a resort or pin tap opens
 * (SNOW-1100; a resort's on every width since SNOW-1161).
 *
 * A tap on a resort pin or a saved pin opened a 320px popup anchored above
 * it (map.js's mountDetailPopup), which on a phone covered the ground around
 * the place the reader had asked about. On a phone the tap now opens
 * #place-sheet at PEEK — the region's danger tile, the place's name, the
 * region, and the place's actions — which leaves that ground in view;
 * dragging up reveals the day's weather (HALF) and the region's problems
 * and the links onward (FULL). The detents are SNOW-1097's, bound by
 * `window.MapSheet.attach` because the partial declares them.
 *
 * A RESORT OPENS HERE ON A DESKTOP TOO (SNOW-1161). There the sheet is the
 * inset panel `_overlay_sheet.html` draws at `sm` and up, with no detents.
 * The resort popup map.js anchored there described a resort a third way —
 * no danger, no weather, a third Save control — and was retired; see
 * docs/decisions/the-map-opens-a-resort-in-the-sheet.md. A saved pin keeps
 * its desktop popup, so for a pin this module still answers on a phone only.
 *
 * map.js decides which surface a tap opens and calls
 * `window.pwaPlaceSheet.open({kind, id, name, createdAt})`; this module
 * fetches the body (apps/public/place_sheet.py) for the day the map is
 * showing and paints it. A newer open drops an older answer.
 *
 * THE ACTIONS. A resort's Save star is favourites.js's own [data-resort-star]
 * handler, unchanged; this module turns the star at once (the create is
 * queued, possibly offline).
 *
 * A RESORT'S STAR IS PAINTED HERE (SNOW-1318). The body names nobody, so
 * one copy serves every reader and a download keeps it for offline; it
 * renders the star as a disabled outline beside a hidden sign-in link.
 * `paintStar` reads the reader's pinned resorts from map.js
 * (`window.pwaFavouritedResorts`, the favourites feed, offline from the
 * overlay cache): a reader who cannot save gets the link; a signed-in one
 * gets the star, filled or not, once the feed is read — until then it
 * stays the disabled outline. It is repainted whenever map.js announces
 * `snowdesk:favourited-resorts-changed` and on `snowdesk:favourites-changed`,
 * so a resort's body is no longer re-read when the favourites change; a
 * saved pin's still is. OFFLINE, a resort's sheet is the one a download
 * kept for that day ('public-partial'); a day it did not take says so.
 * If the feed cannot be read at all (network and overlay cache both
 * fail), the star is enabled unfilled rather than left disabled for ever.
 *
 * A saved pin's "…" menu carries the panel row's items: Share hands
 * the server-composed "<name> - <what3words link>" to window.pwaShare, as
 * the panel does; Rename edits the name in place through
 * window.pwaRowRenameCommit, as the panel does; Delete confirms and posts,
 * then closes the sheet. Each change announces `snowdesk:favourites-changed`,
 * which is what makes the map and the panel re-read the list.
 *
 * OFFLINE (SNOW-1313), a saved pin's sheet is the one a download kept for
 * the reader and that day: the request carries the map's day (`?d=`), and
 * the service worker answers it from the copy stamped for whoever is signed
 * in ('private-partial' in basemap_cache_core.js). A day the device does not
 * hold falls back to the pin's name and saved time, the popup's own
 * content, which the map holds without a request, and says the day is not
 * downloaded.
 *
 * "FULL FORECAST" OPENS THE MAP'S WEATHER SHEET (SNOW-1313), online and
 * offline alike: the link names the borrowed point's short id, and a click
 * opens `window.pwaWeatherDetail` for that point on the map's current day
 * rather than navigating — the same day the download dated the weather
 * sheet with, so offline it finds the saved one. The `href` is the no-JS
 * fallback.
 *
 * PAUSED DURING PLAYBACK (SNOW-1239). While the timeline plays the sheet
 * stays open under a "Playback in progress" band
 * (window.pwaSheetPlayback, sheet_playback_core.js), fetches nothing for
 * its frames, and loads once, for the day playback stopped on, when it
 * stops. A newer ask, and every close, aborts the one in flight.
 */

(function placeSheetInit() {
  'use strict';

  const sheet = document.getElementById('place-sheet');
  const mapEl = document.getElementById('map');
  const playback = window.pwaSheetPlayback;
  if (!sheet || !mapEl || !window.MapSheet || !playback) return;

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
    notDownloaded: "This day isn't downloaded for offline use.",
  });

  // SNOW-1313: the header the service worker's offline-miss 504 carries
  // (`OFFLINE_MISS_HEADER` in sw.js; map_region_sheet.js holds a third copy).
  const OFFLINE_MISS_HEADER = 'X-SW-Offline-Miss';

  // One request in flight: a newer open supersedes it, and a close by any
  // route (onTeardown) aborts it.
  const guard = playback.createGuard();
  // SNOW-1239: a reload the sheet skipped while the timeline played, to
  // make once when it stops.
  let pendingRefresh = false;

  const controller = window.MapSheet.attach(sheet, {
    onTeardown: () => {
      guard.invalidate();
      pendingRefresh = false;
      playback.markPaused(sheet, false);
    },
  });
  const toast = window.MapSheet.toast || function () {};

  /** @type {?{kind: string, id: string, name?: string, createdAt?: string}} */
  let place = null;
  /** @type {?string} */
  let dateKey = null;

  const isPhone = () =>
    typeof window.matchMedia !== 'function' || !window.matchMedia(DESKTOP_QUERY).matches;

  /**
   * Replace the sheet's body with a heading and an optional line, both
   * marked as PEEK, and an optional note under them.
   *
   * @param {string} title
   * @param {string} [line]
   * @param {string} [note] SNOW-1313: "this day isn't downloaded".
   * @returns {void}
   */
  const paintLines = (title, line, note) => {
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
    if (note) {
      const n = document.createElement('p');
      n.className = 'mt-2 text-sm text-text-2';
      n.setAttribute('data-testid', 'place-sheet-not-downloaded');
      n.textContent = note;
      wrap.appendChild(n);
    }
    sheet.replaceChildren(wrap);
  };

  /**
   * Paint a resort body's Save star from the reader's pinned resorts
   * (SNOW-1318).
   *
   * Without map.js's accessor the star stays as rendered, a disabled
   * outline. A reader who cannot save sees the sign-in link instead; a
   * signed-in one sees the star once the feed is read — filled with its
   * uuid when saved, filled and still disabled while a save is queued.
   * Unread, it asks for the feed, which announces itself when it lands.
   *
   * @returns {void}
   */
  const paintStar = () => {
    const resorts = window.pwaFavouritedResorts;
    const star = /** @type {?HTMLButtonElement} */ (sheet.querySelector('[data-resort-star]'));
    const signIn = /** @type {?HTMLElement} */ (sheet.querySelector('[data-resort-star-signin]'));
    if (!resorts || (!star && !signIn)) return;
    if (!resorts.eligible()) {
      if (star) star.remove();
      if (signIn) {
        signIn.hidden = false;
        // SNOW-903: the sheet is an HTMX fragment, so the server cannot name
        // the map url as `next` — the request it saw was the fragment's.
        // Bound here instead, to the map url as it is when tapped.
        window.snowdeskSigninCta?.bindNext?.(
          /** @type {HTMLAnchorElement} */ (signIn),
          signIn.dataset.signinUrl,
        );
      }
      return;
    }
    if (signIn) signIn.remove();
    if (!star) return;
    const state = resorts.stateFor(star.getAttribute('data-resort-slug') || '');
    if (!state) {
      star.disabled = true;
      // A feed that lands announces itself and repaints. One that cannot be
      // read (network and overlay cache both failed) leaves no state: the
      // star is enabled as an outline then, so a tap saves through
      // favourites.js, which re-asks — not a disabled outline for ever.
      Promise.resolve(resorts.load())
        .catch(() => {})
        .then(() => {
          if (!star.isConnected || resorts.stateFor(star.getAttribute('data-resort-slug') || '')) return;
          star.disabled = false;
        });
      return;
    }
    const favourited = !!state.favourited;
    const queued = favourited && !state.uuid;
    star.setAttribute('data-favourited', favourited ? 'true' : 'false');
    star.setAttribute('data-favourite-uuid', state.uuid || '');
    star.setAttribute('aria-pressed', favourited ? 'true' : 'false');
    const label = star.getAttribute(favourited ? 'data-label-remove' : 'data-label-save') || '';
    if (label) {
      star.setAttribute('aria-label', label);
      star.setAttribute('title', label);
    }
    star.querySelector('svg')?.setAttribute('fill', favourited ? 'currentColor' : 'none');
    if (queued) star.setAttribute('aria-busy', 'true');
    else star.removeAttribute('aria-busy');
    star.disabled = queued;
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
    const ask = guard.begin();
    const current = place;
    if (!sheet.querySelector('[data-testid="place-sheet-body"]')) {
      paintLines(current && current.name ? current.name : '', STRINGS.loading);
    }
    return fetch(url, {
      headers: { 'HX-Request': 'true' },
      credentials: 'same-origin',
      signal: ask.signal,
    })
      .then((resp) => {
        if (!resp.ok) {
          const err = new Error('place sheet ' + resp.status);
          // SNOW-1313: offline, and the device holds no sheet for this pin
          // on this day.
          err.notDownloaded = resp.headers?.get(OFFLINE_MISS_HEADER) === '1';
          throw err;
        }
        return resp.text();
      })
      .then((html) => {
        if (!ask.isCurrent() || !controller.isOpen()) return;
        sheet.innerHTML = html;
        paintStar();
      })
      .catch((err) => {
        if (!ask.isCurrent() || !controller.isOpen() || playback.isAbort(err)) return;
        // Offline, a saved pin still has what its popup showed: its name
        // and when it was saved, both on the feature the map holds.
        if (current && current.kind === 'favourite') {
          const saved = window.snowdeskMapFormat?.relativeTime?.(current.createdAt || '') || '';
          const note = err && err.notDownloaded ? STRINGS.notDownloaded : '';
          paintLines(current.name || '', saved || (note ? '' : STRINGS.failed), note);
          return;
        }
        // SNOW-1318: a resort's sheet is kept by a download for each day it
        // took; a day it did not take says so, under the resort's name.
        if (current && current.kind === 'resort' && err && err.notDownloaded) {
          paintLines(current.name || '', '', STRINGS.notDownloaded);
          return;
        }
        paintLines('', STRINGS.failed);
      });
  };

  /**
   * Whether this sheet answers for a kind of place at the current width: a
   * resort on every width (SNOW-1161), a saved pin on a phone only.
   *
   * @param {?{kind: string}} which
   * @returns {boolean}
   */
  const takes = (which) => !!which && (which.kind === 'resort' || isPhone());

  /**
   * Open the sheet for one place.
   *
   * `lngLat`, when map.js knows it, is the place's [lng, lat] for the leader
   * line from the sheet to it (sheet_leader.js, Hugo, 24 Oct 2026).
   *
   * @param {{kind: string, id: string, name?: string, createdAt?: string,
   *   lngLat?: Array<number>}} next
   * @returns {boolean} Whether the sheet took the tap.
   */
  const open = (next) => {
    if (!next || !next.id || !takes(next)) return false;
    const switching = controller.isOpen();
    const sameKind = !!place && place.kind === next.kind;
    place = next;
    document.dispatchEvent(new CustomEvent('snowdesk:sheet-leader-point'));
    if (playback.isPlaying()) {
      // SNOW-1239: opened during playback, the band alone, and the body
      // when the timeline stops.
      guard.invalidate();
      pendingRefresh = true;
      sheet.replaceChildren();
      playback.markPaused(sheet, true);
    } else {
      // FROM ONE PLACE TO THE NEXT THE SHEET STAYS UP (Hugo, 27 Oct 2026).
      // It used to empty to a loading line and grow again when the body
      // landed, so it bobbed between every two resorts. The old body stays,
      // dimmed, under the new place's name until the new one replaces it.
      if (switching) controller.pending({ keep: sameKind ? renameHeading(next) : null });
      load();
    }
    if (!controller.isOpen()) {
      // A resort opens at HALF — its name, the day, the bulletin and
      // today's weather — and a saved pin at PEEK (Hugo, 27 Oct 2026).
      sheet.setAttribute('data-detent-initial', next.kind === 'resort' ? 'half' : 'peek');
      controller.open({ awaiting: !playback.isPlaying() });
    }
    return true;
  };

  /**
   * Put the next place's name in the open body's heading at once, so the
   * sheet says where the reader tapped while the rest of it is fetched.
   *
   * @param {{name?: string}} next
   * @returns {?Element} The heading, or null when there is no name or none
   *   to write it in.
   */
  const renameHeading = (next) => {
    const heading = sheet.querySelector('[data-testid="place-sheet-body"] h2[data-row-label]');
    if (!heading || !next.name) return null;
    heading.textContent = next.name;
    heading.setAttribute('title', next.name);
    return heading;
  };

  /** @returns {void} */
  const close = () => {
    guard.invalidate();
    place = null;
    if (controller.isOpen()) controller.close();
  };

  // ---- the actions -------------------------------------------------------

  /**
   * The CSRF token for a write: the page's own cookie first, then the one
   * the body's delete form carries.
   *
   * SNOW-1313: the body may be a copy the service worker kept from a
   * download, and the token rendered into it then is stale once the
   * session's token rotates (a sign-in, even back into the same account).
   * The cookie is the session's current one.
   *
   * @returns {string}
   */
  const csrfToken = () => {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
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
    // queued — but only when that handler will queue it (on a device that
    // cannot, it toasts and saves nothing). The star stays disabled until
    // map.js reads the saved favourite back and `paintStar` gives it its
    // uuid (SNOW-1318), so a second tap cannot queue a second create.
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

    // SNOW-1313: "Full forecast" opens the borrowed point's weather sheet
    // on the map's own day, in place, rather than navigating to its page —
    // the request the download dated the saved sheet with, so it answers
    // offline too. Without the weather sheet's module the link navigates.
    // A modified click (a new tab) is the reader asking for the page.
    const forecast = target.closest?.('[data-forecast-short-id]');
    const modified = ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey;
    if (forecast && window.pwaWeatherDetail && !modified) {
      const shortId = forecast.getAttribute('data-forecast-short-id');
      if (shortId) {
        ev.preventDefault();
        window.pwaWeatherDetail.open(shortId, dateKey || undefined);
        return;
      }
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

  // A pin changes elsewhere: re-read — when the timeline stops, if it is
  // playing. A resort's body says nothing about the favourites (SNOW-1318),
  // so its star is repainted instead — now, and again when map.js has read
  // the change (`snowdesk:favourited-resorts-changed`, below).
  document.addEventListener('snowdesk:favourites-changed', () => {
    if (!controller.isOpen() || !place) return;
    if (place.kind === 'resort') {
      paintStar();
      return;
    }
    if (playback.isPlaying()) {
      pendingRefresh = true;
      return;
    }
    load();
  });

  // SNOW-1318: map.js has read the favourites (its feed landed, a refetch
  // after a save or a removal, or a star queued offline): repaint the star.
  document.addEventListener('snowdesk:favourited-resorts-changed', () => {
    if (!controller.isOpen() || !place || place.kind !== 'resort') return;
    paintStar();
  });

  document.addEventListener('snowdesk:date-changed', (ev) => {
    const detail = /** @type {CustomEvent} */ (ev).detail || {};
    dateKey = detail.date || null;
    if (!controller.isOpen()) return;
    // SNOW-1239: a timelapse frame during playback only records the day.
    if (!playback.shouldReload(detail.source, playback.isPlaying())) {
      pendingRefresh = true;
      guard.invalidate();
      playback.markPaused(sheet, true);
      return;
    }
    // A day picked by hand ends playback; it arrives before the stop, so
    // the stop that follows has nothing left to load.
    pendingRefresh = false;
    playback.markPaused(sheet, false);
    load();
  });

  // SNOW-1239: playback starting pauses the open sheet; stopping lifts the
  // band and loads the day it stopped on, once.
  document.addEventListener('snowdesk:timelapse-state', (ev) => {
    if (!controller.isOpen() || !place) return;
    if (/** @type {CustomEvent} */ (ev).detail?.playing) {
      guard.invalidate();
      pendingRefresh = true;
      playback.markPaused(sheet, true);
      return;
    }
    playback.markPaused(sheet, false);
    if (!pendingRefresh) return;
    pendingRefresh = false;
    load();
  });

  // A saved pin's sheet is the phone's; widening past `sm` hands it back
  // to the popup. A resort's stays open as the desktop panel.
  window.addEventListener('resize', () => {
    if (controller.isOpen() && !takes(place)) close();
  });

  window.pwaPlaceSheet = Object.freeze({
    open: open,
    close: close,
    /** @returns {?Array<number>} The open place's [lng, lat], or null. */
    point: () => (controller.isOpen() && place && Array.isArray(place.lngLat)
      ? place.lngLat
      : null),
    isOpen: () => controller.isOpen(),
    /** Rest the phone sheet at a detent (SNOW-1231's walkthrough). */
    detent: (name) => controller.detent(name),
  });
}());
