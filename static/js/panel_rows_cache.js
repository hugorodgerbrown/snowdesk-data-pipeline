/*
 * static/js/panel_rows_cache.js — instant re-open for the map's UGC panels
 * (SNOW-879).
 *
 * The favourites, routes and observations roundels each open a sheet whose
 * rows are loaded over HTMX from that app's list endpoint. All three did it
 * the same way and all three did it on EVERY open, from cold: the panel
 * appeared, showed "Loading your favourites…", and the rows arrived a round
 * trip later. Nothing was kept between opens, and nothing was fetched before
 * the first one — so the fifth open of a list that had not changed cost
 * exactly as much as the first.
 *
 * This module is the one place that changes, so the three panels keep the
 * loaders they already have rather than each growing a cache of its own
 * (the shape ``map_region_panel.js`` already uses for its pinned list, and
 * the reason it is the one panel that did not have this problem).
 *
 * Stale-while-revalidate
 * ----------------------
 * ``load()`` paints the cached markup into the panel synchronously and THEN
 * issues the same ``htmx.ajax`` the panel used to issue on its own. So the
 * rows are on screen on the frame the roundel is pressed, and the server
 * still gets the last word a moment later. What the user sees is never more
 * than one open out of date, and a favourite's danger chip — the one part of
 * these rows that changes without the user touching anything — is refreshed
 * on every open.
 *
 * HTMX STAYS THE TRANSPORT. Everything hanging off these requests keeps
 * working because none of it is bypassed: each panel's own
 * ``htmx:responseError`` / ``htmx:sendError`` handlers still draw its error
 * line, ``favourites_offline.js`` still reads the roster sidecar out of the
 * swapped DOM on ``htmx:afterOnLoad`` and writes it through to IndexedDB,
 * and the endpoints stay behind ``@require_htmx`` (invariant 4).
 *
 * The one thing this module does to those requests is SUPPRESS THE SWAP when
 * the response is byte-identical to what was just painted from cache — via
 * ``htmx:beforeSwap``'s mutable ``shouldSwap`` (htmx 2.0.4). That is not an
 * optimisation, it is a correctness fix: re-swapping identical rows would
 * destroy and rebuild every node in the panel a moment after the user could
 * first touch them, throwing away an inline rename they had already started
 * and resetting the scroll region under them. Comparing the raw
 * ``serverResponse`` strings rather than the parsed ``innerHTML`` is what
 * makes the comparison trustworthy — the DOM round-trip normalises
 * whitespace and attribute order, so two identical responses would not
 * always compare equal after it.
 *
 * The warm
 * --------
 * ``warm()`` fills the cache before any panel is opened, so even the FIRST
 * open of a session is instant. It is a plain ``fetch`` carrying
 * ``HX-Request`` rather than an ``htmx.ajax`` — the same move
 * ``map_region_panel.js`` makes against ``favourite_region_toggle`` — because
 * a warm has no target to swap into and must not fire the lifecycle events
 * the panels bind to. A warm that fails is simply not cached; the panel then
 * behaves exactly as it did before this module existed.
 *
 * Nothing here is persisted. The cache is per page load, in memory, and
 * therefore cannot outlive a sign-out or leak across an account switch —
 * the hazard ``map_overlay_offline_cache.js`` has to carry a ``principal``
 * to defend against, and one this module avoids rather than solves.
 *
 * No exports beyond ``window.pwaPanelRows``.
 */

(function panelRowsCacheInit() {
  'use strict';

  /**
   * Cached raw response bodies by panel key ('favourites', 'routes',
   * 'observations'). The value is the server's response text verbatim.
   *
   * @type {Map<string, string>}
   */
  const cache = new Map();

  /**
   * Per rows container: which panel it belongs to, and the exact response
   * body currently PAINTED into it (null when nothing was).
   *
   * ``painted`` is what the swap suppression below compares against, and it
   * has to be this rather than the cache: a warm that lands between a panel
   * opening and its own request coming back would otherwise fill the cache
   * with a body identical to the response, suppress the swap, and leave the
   * panel on "Loading…" for good — a request answered, cached, and never
   * shown. Suppressing only what is demonstrably already on screen cannot
   * make that mistake.
   *
   * Weak because every open clones a NEW container out of the sheet's
   * <template> and drops the last one on the floor.
   *
   * @type {WeakMap<Element, {key: string, painted: ?string}>}
   */
  const owners = new WeakMap();

  /**
   * Paint cached markup into a rows container.
   *
   * ``htmx.process`` is not optional: these rows arrive carrying their own
   * Remove forms, and without it they would be inert markup for the moment
   * before the revalidation lands — a delete button that does nothing is
   * worse than one that is not there yet.
   *
   * @param {Element} target The rows container.
   * @param {string} body The response body to paint.
   * @returns {boolean} True when it was painted.
   */
  function paint(target, body) {
    if (typeof body !== 'string') return false;
    try {
      target.innerHTML = body;
      htmx.process(target);
      return true;
    } catch (_e) {
      // A malformed cached body must not take the panel down with it — the
      // htmx.ajax below is about to replace this content anyway.
      return false;
    }
  }

  /**
   * Load a panel's rows: cached markup now, the server's answer next.
   *
   * @param {string} key The panel key ('favourites', 'routes', 'observations').
   * @param {string} url The list endpoint, used VERBATIM — every caller's
   *   URL is written by the server and carries query parameters that
   *   rebuilding it here would drop.
   * @param {Element} target The rows container to swap into.
   * @param {{cached?: boolean}} [options] ``cached: true`` to paint from
   *   cache first. Pass it for an OPEN; leave it off for a re-read that
   *   follows a mutation, where the cache is known to be behind and
   *   painting it would flash a row the user has just deleted back onto
   *   the screen.
   * @returns {Promise<void>|undefined} htmx.ajax's promise, or undefined
   *   when there was nothing to do.
   */
  function load(key, url, target, options) {
    if (!key || !url || !target || typeof htmx === 'undefined') return undefined;
    const state = { key: key, painted: null };
    owners.set(target, state);
    if (options && options.cached) {
      const body = cache.get(key);
      if (paint(target, body)) state.painted = body;
    } else {
      cache.delete(key);
    }
    return htmx.ajax('GET', url, { target: target, swap: 'innerHTML' });
  }

  /**
   * Run `fn` once the browser is otherwise idle.
   *
   * A warm exists to make a LATER interaction fast; competing with the map's
   * own first paint to do it would trade the delay the user complained about
   * for one they would notice more. `requestIdleCallback` is absent on
   * Safari before 17, hence the timeout fallback.
   *
   * @param {Function} fn The work to defer.
   * @returns {void}
   */
  function onIdle(fn) {
    if (typeof requestIdleCallback === 'function') requestIdleCallback(fn);
    else setTimeout(fn, 1200);
  }

  /**
   * Fill the cache before any panel has been opened, so even the first open
   * of a session paints instantly. Deferred to idle; safe to call at module
   * init.
   *
   * Best effort throughout: a failed or non-2xx warm is not cached and is
   * not retried, leaving the panel's own load to report the failure in the
   * place the user can see it.
   *
   * @param {string} key The panel key.
   * @param {string} url The list endpoint.
   * @returns {void}
   */
  function warm(key, url) {
    if (!key || !url || cache.has(key)) return;
    onIdle(function () {
      // Re-checked inside the callback: a user quick enough to open the
      // panel before the browser went idle has already filled this entry,
      // and warming over it would be a request for something we hold.
      if (cache.has(key)) return;
      fetch(url, {
        credentials: 'same-origin',
        // These endpoints are @require_htmx and this is not an htmx request
        // — the same plain-fetch-with-the-header move map_region_panel.js
        // makes against favourite_region_toggle.
        headers: { 'HX-Request': 'true' },
      })
        .then(function (resp) {
          return resp.ok ? resp.text() : null;
        })
        .then(function (body) {
          if (typeof body === 'string' && !cache.has(key)) cache.set(key, body);
        })
        .catch(function () {
          // Offline, or the endpoint is unreachable. Nothing cached, nothing
          // shown — the panel opens exactly as it did before SNOW-879.
        });
    });
  }

  /**
   * Drop a panel's cached rows.
   *
   * @param {string} key The panel key.
   * @returns {void}
   */
  function invalidate(key) {
    cache.delete(key);
  }

  // Delegated from document.body because htmx raises the lifecycle events
  // for an `htmx.ajax` call with no source element against document.body —
  // the same reason row_removed.js and each panel's error handlers are bound
  // there rather than to the container.
  document.body?.addEventListener('htmx:beforeSwap', function (ev) {
    const detail = ev.detail;
    if (!detail || !detail.target) return;
    const state = owners.get(detail.target);
    if (!state) return;
    // An error response is not an answer about the rows — htmx has already
    // decided not to swap it, and each panel draws its own error line from
    // the matching htmx:responseError. Caching it would serve the failure
    // back as content on the next open.
    if (detail.isError || !detail.shouldSwap) return;
    const body = detail.serverResponse;
    if (typeof body !== 'string') return;
    cache.set(state.key, body);
    if (state.painted === body) {
      // Byte-identical to what this container is ALREADY showing. See this
      // module's header: suppressing the swap protects an in-progress inline
      // rename and the panel's scroll position from a rebuild that would
      // change nothing.
      detail.shouldSwap = false;
      return;
    }
    // htmx is about to put this on screen, so it becomes what a later
    // revalidation of this same container compares against.
    state.painted = body;
  });

  Object.defineProperty(window, 'pwaPanelRows', {
    value: Object.freeze({ load: load, warm: warm, invalidate: invalidate }),
    writable: false,
    configurable: false,
  });
}());
