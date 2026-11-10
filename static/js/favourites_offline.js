/*
 * static/js/favourites_offline.js — Offline read cache for favourites
 * (SNOW-418).
 *
 * Caches each favourite detail card fetched (``favourites:card``) into the
 * ``data:favourites`` IndexedDB store (SNOW-375's ``db.js``, schema v2),
 * keyed by the favourite's uuid. When that HTMX request fails (offline, or
 * a 5xx from a flaky connection), this script repaints the same target
 * element from the cached record instead of leaving HTMX's own error state
 * in place.
 *
 * SNOW-1481 retired the roster half: the map sheet's pins list
 * (``favourites:list``) and its ``favourites-roster-cache`` sidecar are
 * gone, the sheet lists Places, and Places is online only, as Today is —
 * offline it says it couldn't be loaded rather than showing cached pins.
 *
 * This is the documented relaxation of the SW "safety data is
 * network-only" rule to "cached, with explicit staleness" — see
 * docs/offline-first.md §12.6. A cached rating past its
 * ``unsafe_after_seconds`` horizon (48h, mirroring
 * ``core.freshness.DEFAULT_UNSAFE_AFTER_SECONDS``) never renders as a
 * current-looking danger chip; it renders as explicitly EXPIRED instead.
 *
 * Write-through
 * --------------
 * On ``htmx:afterOnLoad`` this reads the ``favourite-card-cache``
 * ``json_script`` sidecar the server renders alongside the swapped HTML
 * (``favourites/views.py``), and upserts the record into
 * ``data:favourites``, merged onto any existing record for the same uuid
 * (the card's fields win).
 *
 * Offline read
 * ------------
 * On ``htmx:sendError`` / ``htmx:responseError`` (network failure or a
 * non-2xx response) this inspects the failed request's path, and for a
 * card request reads the cached record and renders a minimal offline card
 * via
 * ``renderOfflineCard`` — reusing the existing ``.danger-tile`` CSS +
 * design tokens rather than re-implementing the full card template.
 *
 * No exports — every entry point is wired to a DOM event at load time.
 */

(function () {
  'use strict';

  const STORE = 'data:favourites';
  const RESOURCE = 'favourites';
  const CARD_PATH_SUFFIX = '/card/';
  // SNOW-722: the ownership prefix. The suffix above is matched with
  // bare ``indexOf``, and this module's error handlers are bound to
  // ``document.body`` — so they see EVERY failed HTMX request on the page,
  // not just the favourites ones. ``/routes/partials/list/`` contains
  // ``/partials/list/``, so offline the routes panel's failed fetch was
  // caught here, its [data-routes-rows] wiped, and favourite cards painted
  // into the Routes panel under its "TRACKS" heading. Only paths under this
  // prefix belong to this module. ``config/urls.py`` mounts favourites at an
  // unprefixed ``favourites/`` and the project has no ``i18n_patterns``, so
  // no language segment can precede it — do not loosen this to a suffix.
  const OWNED_PATH_PREFIX = '/favourites/partials/';

  // SNOW-620: server-translated card copy, read back from the template
  // includes/_favourites_offline_strings.html renders. This module builds
  // the offline favourite card entirely in JS — it stands in for a server
  // partial that could not be fetched — so none of its copy has ever
  // passed through a template. The literals are the English fallback;
  // see static/js/i18n_strings.js.
  const STRINGS = self.pwaStrings.read('favourites-offline-strings-template', {
    altitude: '%(metres)s m altitude',
    'no-coverage': 'No bulletin coverage for this location.',
    'rating-expired': "Rating expired — reconnect to see today's danger level",
    'rating-as-of': 'as of %(time)s',
    'cached-as-of': 'Showing cached data — as of %(time)s',
    'no-longer-listed': 'No longer listed',
  });

  const interpolate = self.pwaStrings.interpolate;

  /**
   * True when ``window.pwaDb`` is present and the app is not in the
   * terminal Reset Required state. Every DB access in this file is
   * guarded by this check first.
   *
   * @returns {boolean}
   */
  function dbReady() {
    return (
      typeof window.pwaDb === 'object' && !window.pwaDb.isResetRequired()
    );
  }

  /**
   * Parse a ``json_script``-rendered sidecar by element id.
   *
   * @param {ParentNode} root
   * @param {string} id
   * @returns {any | null}
   */
  function _readJsonScript(root, id) {
    try {
      const el = root.querySelector ? root.querySelector(`#${id}`) : null;
      if (!el || !el.textContent) return null;
      return JSON.parse(el.textContent);
    } catch (_e) {
      return null;
    }
  }

  /**
   * Write-through: upsert the card sidecar found under ``root`` into
   * ``data:favourites``. Never throws — every failure is swallowed so a broken cache write
   * can't break the HTMX swap it rides on.
   *
   * @param {ParentNode} root
   * @returns {Promise<void>}
   */
  async function _syncFromDom(root) {
    if (!dbReady()) return;
    try {
      const card = _readJsonScript(root, 'favourite-card-cache');
      let wrote = false;

      if (card && card.uuid) {
        let merged = card;
        try {
          const existing = await window.pwaDb.get(STORE, card.uuid);
          if (existing) merged = { ...existing, ...card };
        } catch (_e) {
          // No existing record — fall back to the card payload as-is.
        }
        await window.pwaDb.put(STORE, merged);
        wrote = true;
      }

      if (wrote) {
        await window.pwaDb.put('meta:sync', {
          resource: RESOURCE,
          last_synced_at: new Date().toISOString(),
        });
      }
    } catch (_e) {
      // Non-fatal — the cache is best-effort.
    }
  }

  /**
   * Stamp a cached record's time: "HH:MM" when it is from today, day +
   * "HH:MM" otherwise, so yesterday's cache cannot read as today's
   * (SNOW-1139). The formatting is ``pwaCachedStampCore``'s, shared with
   * the reports and routes panels; '' on a parse failure, or when the core
   * is somehow absent.
   *
   * @param {string | null} isoValue
   * @returns {string}
   */
  function _formatStamp(isoValue) {
    const core = window.pwaCachedStampCore;
    return core ? core.formatStamp(isoValue) : '';
  }

  /**
   * Classify a cached record as expired — past its
   * ``unsafe_after_seconds`` horizon — using wall-clock ``Date.now()``.
   * Records with no rating (``record.rating`` null) or no horizon
   * (``unsafe_after_seconds`` null) never expire.
   *
   * @param {object} record
   * @returns {boolean}
   */
  function _isExpired(record) {
    if (!record.rating || record.unsafe_after_seconds == null) return false;
    const generated = Date.parse(record.generated_at);
    if (Number.isNaN(generated)) return false;
    return Date.now() - generated >= record.unsafe_after_seconds * 1000;
  }

  /**
   * Build the offline fallback card for one cached favourite record.
   * Built with ``createElement`` + ``textContent`` throughout — never
   * ``innerHTML`` with user-supplied data (the favourite's own name) —
   * per the "no mark_safe on user-supplied content" invariant.
   *
   * ``headingTag`` mirrors the server card's ``heading_tag``
   * (favourites/partials/_favourite_card.html): this card stands in for
   * that one, in the same slot, so it has to rank its title the same way
   * — otherwise a pin's outline level would depend on whether the request
   * reached the server. Callers below choose it from where they are
   * painting; see ``_renderFromCache``.
   *
   * @param {object} record
   * @param {string} [headingTag] — "h2" (default) or "h3".
   * @returns {HTMLElement}
   */
  function renderOfflineCard(record, headingTag) {
    const root = document.createElement('div');
    root.className = 'bg-card border border-border rounded-card p-6';
    root.setAttribute('data-testid', 'favourite-card');
    root.setAttribute('data-favourite-uuid', record.uuid);

    const title = document.createElement(headingTag || 'h2');
    title.className = 'text-lg font-semibold text-text-1';
    title.setAttribute('data-testid', 'favourite-card-title');
    title.textContent =
      record.name ||
      `${Number(record.latitude).toFixed(5)}, ${Number(record.longitude).toFixed(5)}`;
    root.appendChild(title);

    // SNOW-1404: a saved resort that was retired has no place card to show
    // — the panel's online row says so, and the cached one must too.
    if (record.retired) {
      const retired = document.createElement('p');
      retired.className = 'text-sm text-text-2 mt-0.5';
      retired.setAttribute('data-testid', 'favourite-card-retired');
      retired.textContent = STRINGS['no-longer-listed'];
      root.appendChild(retired);
      return root;
    }

    const altitude = document.createElement('p');
    altitude.className = 'text-sm text-text-2 mt-0.5';
    altitude.setAttribute('data-testid', 'favourite-card-altitude');
    altitude.textContent = interpolate(STRINGS.altitude, {
      metres: Math.round(record.elevation),
    });
    root.appendChild(altitude);

    const dangerSection = document.createElement('div');
    dangerSection.className = 'mt-4 pt-4 border-t border-border';

    if (!record.region) {
      const noCoverage = document.createElement('p');
      noCoverage.className = 'text-sm text-text-3';
      noCoverage.setAttribute('data-testid', 'favourite-card-no-coverage');
      noCoverage.textContent = STRINGS['no-coverage'];
      dangerSection.appendChild(noCoverage);
    } else if (_isExpired(record)) {
      const expired = document.createElement('p');
      expired.className = 'text-sm text-text-3';
      expired.setAttribute(
        'data-testid',
        'favourite-card-rating-expired',
      );
      expired.textContent = STRINGS['rating-expired'];
      dangerSection.appendChild(expired);
    } else if (record.rating) {
      const row = document.createElement('div');
      row.className = 'flex items-center gap-2 mt-2';

      const chip = document.createElement('span');
      chip.className = 'danger-tile';
      chip.setAttribute('data-level', record.rating.max_rating || '');
      chip.setAttribute('data-testid', 'favourite-card-rating-chip');
      chip.textContent = `${record.rating.digit || ''}${record.rating.max_subdivision || ''}`;
      row.appendChild(chip);

      const stamp = document.createElement('span');
      stamp.className = 'text-xs text-text-3 font-mono';
      stamp.textContent = interpolate(STRINGS['rating-as-of'], {
        time: _formatStamp(record.generated_at),
      });
      row.appendChild(stamp);

      dangerSection.appendChild(row);
    }
    root.appendChild(dangerSection);

    const asOf = document.createElement('p');
    asOf.className = 'text-xs text-text-3 font-mono mt-4';
    asOf.setAttribute('data-testid', 'favourite-card-cached-as-of');
    asOf.textContent = interpolate(STRINGS['cached-as-of'], {
      time: _formatStamp(record.generated_at),
    });
    root.appendChild(asOf);

    return root;
  }

  /**
   * Extract the request path HTMX attempted, from an
   * ``htmx:sendError`` / ``htmx:responseError`` event detail. htmx's own
   * event object carries this as ``detail.requestConfig.path`` (and
   * mirrors it at ``detail.pathInfo.requestPath``); ``detail.elt``'s
   * ``hx-get`` attribute is a last-resort fallback for event shapes that
   * omit both.
   *
   * @param {CustomEvent} evt
   * @returns {string}
   */
  function _requestPath(evt) {
    const detail = evt && evt.detail;
    if (!detail) return '';
    if (detail.requestConfig && detail.requestConfig.path) {
      return detail.requestConfig.path;
    }
    if (detail.pathInfo && detail.pathInfo.requestPath) {
      return detail.pathInfo.requestPath;
    }
    const elt = detail.elt;
    if (elt && typeof elt.getAttribute === 'function') {
      return elt.getAttribute('hx-get') || '';
    }
    return '';
  }

  /**
   * Extract the favourite uuid from a card request path
   * (``/favourites/partials/<uuid>/card/``).
   *
   * @param {string} path
   * @returns {string | null}
   */
  function _uuidFromCardPath(path) {
    const parts = path.split('/').filter(Boolean);
    const idx = parts.indexOf('card');
    return idx > 0 ? parts[idx - 1] : null;
  }

  /**
   * SNOW-722: does this request path belong to the favourites app?
   *
   * The failure handlers below are bound to ``document.body``, so they are
   * offered every failed HTMX request on the page. Anything that is not a
   * favourites partial must be left to whoever owns it — the routes panel
   * in particular has an offline cache of its own since SNOW-950
   * (``static/js/routes_offline.js``, painted by ``static/js/routes.js``),
   * and a favourites handler repainting over it would be the SNOW-722
   * fault again with cached rows instead of a failure line.
   *
   * Resolved through ``URL`` so a relative ``hx-get``, an absolute path and
   * a fully-qualified URL all reduce to a comparable pathname, and a query
   * string (``?variant=map``) can't defeat the prefix test.
   *
   * @param {string} path
   * @returns {boolean}
   */
  function _isFavouritesPath(path) {
    let pathname;
    try {
      pathname = new URL(path, window.location.origin).pathname;
    } catch (_e) {
      return false;
    }
    return pathname.indexOf(OWNED_PATH_PREFIX) === 0;
  }

  /**
   * Offline-read fallback: on a failed card request, repaint the target
   * element from the cached ``data:favourites`` record.
   * Leaves HTMX's own failure UI in place when the cache has nothing to
   * offer (not ready, or no matching record).
   *
   * @param {CustomEvent} evt
   * @returns {Promise<void>}
   */
  async function _renderFromCache(evt) {
    if (!dbReady()) return;
    const path = _requestPath(evt);
    if (!path) return;
    // SNOW-722: ownership guard — see _isFavouritesPath. Without it the bare
    // suffix match below would claim another app's card path as well.
    if (!_isFavouritesPath(path)) return;

    const detail = evt.detail || {};
    const target = detail.target || (detail.elt && detail.elt.parentNode);

    try {
      if (path.indexOf(CARD_PATH_SUFFIX) !== -1) {
        const uuid = _uuidFromCardPath(path);
        if (!uuid) return;
        const record = await window.pwaDb.get(STORE, uuid);
        if (!record) return;
        // The request's own target — the panel under the row whose chevron
        // asked for the card (SNOW-711 gave every row its own; there was
        // one #favourite-card-panel above the whole list before it). That
        // was /account/favourites/, which SNOW-803 removed: the map renders
        // its rows with ``hide_disclosure``, so nothing there has a chevron
        // to ask with — this branch is kept for the card endpoint's sake.
        //
        // h2, matching what the server would have sent for the same
        // request — apps.favourites.views.favourite_card passes
        // heading_tag="h2" for exactly this panel. Both were h3 while the
        // panel lived inside the account hub's <h2>-headed "Favourites"
        // section; SNOW-668 made the page's <h1> the only heading above it.
        if (!target) return;
        target.textContent = '';
        target.appendChild(renderOfflineCard(record, 'h2'));
      }
    } catch (_e) {
      // Non-fatal — leave HTMX's own failure state in place.
    }
  }

  document.body?.addEventListener('htmx:afterOnLoad', (evt) => {
    const target = (evt && evt.detail && evt.detail.target) || document;
    _syncFromDom(target).catch(() => {});
  });

  document.body?.addEventListener('htmx:sendError', (evt) => {
    _renderFromCache(evt).catch(() => {});
  });

  document.body?.addEventListener('htmx:responseError', (evt) => {
    _renderFromCache(evt).catch(() => {});
  });
})();
