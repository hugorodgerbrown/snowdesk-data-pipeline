/*
 * static/js/favourites_offline.js — Offline read cache for favourites
 * (SNOW-418).
 *
 * Caches every favourite the manage page fetches — the roster
 * (``favourites:list``) and each opened detail card (``favourites:card``)
 * — into the ``data:favourites`` IndexedDB store (SNOW-375's ``db.js``,
 * schema v2), keyed by the favourite's uuid. When either HTMX request
 * fails (offline, or a 5xx from a flaky connection), this script repaints
 * the same target element from the cached record instead of leaving
 * HTMX's own error state in place.
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
 * On ``htmx:afterOnLoad`` this reads the ``favourites-roster-cache`` /
 * ``favourite-card-cache`` ``json_script`` sidecars the server renders
 * alongside the swapped HTML (``favourites/views.py``), and upserts each
 * record into ``data:favourites``. A card update merges onto any existing
 * roster record for the same uuid (the card is the more detailed source,
 * so its fields win).
 *
 * The roster sidecar carries the user's whole current list, so the same
 * pass also drops any cached record the roster omits. Deleting a
 * favourite is an online-only ``hx-post`` with no cache-eviction step of
 * its own, so this reconcile is what keeps a deleted pin out of the
 * offline roster below.
 *
 * Offline read
 * ------------
 * On ``htmx:sendError`` / ``htmx:responseError`` (network failure or a
 * non-2xx response) this inspects the failed request's path to decide
 * whether it was the roster or a single card, reads the cached
 * record(s), and renders a minimal offline card via
 * ``renderOfflineCard`` — reusing the existing ``.danger-tile`` CSS +
 * design tokens rather than re-implementing the full card template.
 *
 * No exports — every entry point is wired to a DOM event at load time.
 */

(function () {
  'use strict';

  const STORE = 'data:favourites';
  const RESOURCE = 'favourites';
  const LIST_PATH_SUFFIX = '/partials/list/';
  const CARD_PATH_SUFFIX = '/card/';

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
    'weather-empty':
      "Point forecast coming soon — weather for this exact location and altitude isn't available yet.",
    'cached-as-of': 'Showing cached data — as of %(time)s',
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
   * Delete every cached record whose uuid is absent from ``keep``.
   *
   * ``db.js`` exposes single-key operations only, so this is a second pass
   * over the store rather than part of the roster's write transaction.
   * Both passes are idempotent and every later roster swap repeats the
   * reconcile, so a failure between them self-corrects.
   *
   * @param {Set<string>} keep - uuids present in the roster payload.
   * @returns {Promise<void>}
   */
  async function _evictAbsent(keep) {
    const cached = await window.pwaDb.getAll(STORE);
    for (const record of cached) {
      if (record && record.uuid && !keep.has(record.uuid)) {
        await window.pwaDb.delete(STORE, record.uuid);
      }
    }
  }

  /**
   * Write-through: upsert the roster + card sidecars found under ``root``
   * into ``data:favourites``, and reconcile the store against the roster.
   * Never throws — every failure is swallowed so a broken cache write
   * can't break the HTMX swap it rides on.
   *
   * @param {ParentNode} root
   * @returns {Promise<void>}
   */
  async function _syncFromDom(root) {
    if (!dbReady()) return;
    try {
      const roster = _readJsonScript(root, 'favourites-roster-cache');
      const card = _readJsonScript(root, 'favourite-card-cache');
      let wrote = false;

      if (Array.isArray(roster)) {
        const rosterUuids = new Set();
        for (const record of roster) {
          if (record && record.uuid) {
            rosterUuids.add(record.uuid);
            await window.pwaDb.put(STORE, record);
            wrote = true;
          }
        }
        // The roster is the user's full list, so anything cached and not
        // in it has been deleted since the last swap. A card-only swap
        // never reaches here — it says nothing about which favourites
        // exist, so it must not evict.
        await _evictAbsent(rosterUuids);
      }

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
   * Format an ISO timestamp as "HH:MM" (24h, zero-padded), mirroring
   * ``pwa_offline.js``'s ``formatShort`` without pulling in a full date +
   * day/month string. Returns '' on parse failure.
   *
   * @param {string | null} isoValue
   * @returns {string}
   */
  function _formatHHMM(isoValue) {
    if (!isoValue) return '';
    const d = new Date(isoValue);
    if (Number.isNaN(d.valueOf())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /**
   * Classify a cached record as expired — past its
   * ``unsafe_after_seconds`` horizon — using wall-clock ``Date.now()``.
   * Records with no rating (``record.rating`` null) or no horizon
   * (``unsafe_after_seconds`` null, e.g. weather-only) never expire.
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
   * @param {object} record
   * @returns {HTMLElement}
   */
  function renderOfflineCard(record) {
    const root = document.createElement('div');
    root.className = 'bg-card border border-border rounded-card p-6';
    root.setAttribute('data-testid', 'favourite-card');
    root.setAttribute('data-favourite-uuid', record.uuid);

    const title = document.createElement('h2');
    title.className = 'text-lg font-semibold text-text-1';
    title.setAttribute('data-testid', 'favourite-card-title');
    title.textContent =
      record.name ||
      `${Number(record.latitude).toFixed(5)}, ${Number(record.longitude).toFixed(5)}`;
    root.appendChild(title);

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
        time: _formatHHMM(record.generated_at),
      });
      row.appendChild(stamp);

      dangerSection.appendChild(row);
    }
    root.appendChild(dangerSection);

    const weatherSection = document.createElement('div');
    weatherSection.className = 'mt-4 pt-4 border-t border-border';
    const weatherEmpty = document.createElement('p');
    weatherEmpty.className = 'text-sm text-text-3';
    weatherEmpty.setAttribute('data-testid', 'favourite-card-weather-empty');
    weatherEmpty.textContent = STRINGS['weather-empty'];
    weatherSection.appendChild(weatherEmpty);
    root.appendChild(weatherSection);

    const asOf = document.createElement('p');
    asOf.className = 'text-xs text-text-3 font-mono mt-4';
    asOf.setAttribute('data-testid', 'favourite-card-cached-as-of');
    asOf.textContent = interpolate(STRINGS['cached-as-of'], {
      time: _formatHHMM(record.generated_at),
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
   * Offline-read fallback: on a failed roster or card request, repaint
   * the target element from the cached ``data:favourites`` record(s).
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

    const detail = evt.detail || {};
    const target = detail.target || (detail.elt && detail.elt.parentNode);

    try {
      if (path.indexOf(LIST_PATH_SUFFIX) !== -1) {
        const records = await window.pwaDb.getAll(STORE);
        if (!records || records.length === 0 || !target) return;
        target.textContent = '';
        records.forEach((record) => target.appendChild(renderOfflineCard(record)));
        return;
      }

      if (path.indexOf(CARD_PATH_SUFFIX) !== -1) {
        const uuid = _uuidFromCardPath(path);
        if (!uuid) return;
        const record = await window.pwaDb.get(STORE, uuid);
        if (!record) return;
        // The request's own target: on /account/ that is the panel under
        // the row whose chevron asked for the card (SNOW-711 gave every
        // row its own; there was one #favourite-card-panel above the whole
        // list before it), and on the detail page the element HTMX aimed
        // at. Either way it is where the card that failed was going.
        if (!target) return;
        target.textContent = '';
        target.appendChild(renderOfflineCard(record));
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
