/*
 * static/js/pwa_offline.js — Connection-state + freshness UI (SNOW-377).
 *
 * Ships spec §3.5, §10.1, §10.2, §10.4, §10.7 non-negotiables:
 *
 *   (1) Persistent offline banner — top-anchored, revealed when
 *       ``navigator.onLine === false`` or when a fetch just failed,
 *       hidden on ``online``. Shows the freshness of the most recent
 *       successful response so the user can judge whether the cached
 *       data is trustworthy right now.
 *   (2) Freshness update — every ``X-Data-Generated-At`` header seen
 *       (fetch or HTMX) updates the banner's timestamp so the user sees
 *       the same number the page-level indicator did on last refresh.
 *   (3) Network-required buttons — any element carrying
 *       ``data-network-required`` is set ``disabled`` when offline and
 *       re-enabled when back online. Non-button elements get an
 *       ``aria-disabled="true"`` + ``pointer-events: none`` fallback.
 *
 * Deferred to SNOW-375 / follow-ups:
 *   * IndexedDB-backed persistence of the last-seen timestamp (currently
 *     resets on hard reload — acceptable, the next successful response
 *     re-populates it within seconds).
 *   * Pull-to-refresh explicit-network path.
 *   * "Updated HH:MM" post-refresh toast.
 */

(function () {
  'use strict';

  const BANNER_ID = 'pwa-offline-banner';
  const NETWORK_ATTR = 'data-network-required';

  // In-memory ledger of the most recent freshness data. Reset per page
  // load — the first response repopulates it, which for HTMX-heavy pages
  // happens within the first render tick.
  let lastGeneratedAt = null;

  /**
   * Format a Date / ISO string as "HH:MM DD/MM" without pulling Intl.
   * Falls back to an empty string on parse failure so we don't render
   * "Invalid Date" in the UI.
   *
   * @param {string | Date | null} value
   * @returns {string}
   */
  function formatShort(value) {
    if (!value) return '';
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.valueOf())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())} ${pad(d.getDate())}/${pad(d.getMonth() + 1)}`;
  }

  /**
   * Reveal / hide the offline banner and refresh its freshness suffix.
   * Idempotent — safe to call on every online/offline transition.
   *
   * @param {boolean} online
   */
  function renderBanner(online) {
    const banner = document.getElementById(BANNER_ID);
    if (!banner) return;
    if (online) {
      banner.classList.add('hidden');
      return;
    }
    banner.classList.remove('hidden');
    const label = banner.querySelector('[data-role="offline-freshness"]');
    if (!label) return;
    const stamp = formatShort(lastGeneratedAt);
    label.textContent = stamp ? `Last updated ${stamp}` : '';
  }

  /**
   * Toggle the disabled state of every element carrying
   * ``data-network-required``. Direct matches use the native
   * ``disabled`` property where available; matched containers (forms)
   * cascade the disabled state to their submit-capable children so a
   * ``<form data-network-required>`` blocks its Submit / Add / Delete
   * buttons without having to tag each one individually.
   *
   * Non-disable-able elements (``<a>``, ``<div>``) fall back to
   * ``aria-disabled="true"`` + ``pointer-events: none``.
   *
   * @param {boolean} online
   */
  function syncNetworkRequired(online) {
    const nodes = document.querySelectorAll(`[${NETWORK_ATTR}]`);
    nodes.forEach((node) => applyDisabled(node, !online));
    document
      .querySelectorAll(
        `[${NETWORK_ATTR}] button, [${NETWORK_ATTR}] input[type="submit"], [${NETWORK_ATTR}] input[type="button"]`,
      )
      .forEach((child) => applyDisabled(child, !online));
  }

  /**
   * Apply / remove the disabled state on a single element without
   * clobbering a pre-existing ``disabled`` attribute set for other
   * reasons (form validity, etc.). We tag our own mutations with
   * ``data-was-disabled-offline`` so the reverse transition only
   * un-disables what we disabled.
   *
   * @param {Element} node
   * @param {boolean} disabled
   */
  function applyDisabled(node, disabled) {
    if (disabled) {
      if ('disabled' in node && !node.disabled) {
        node.disabled = true;
        node.setAttribute('data-was-disabled-offline', '1');
      }
      node.setAttribute('aria-disabled', 'true');
      node.style.pointerEvents = 'none';
    } else {
      if (
        'disabled' in node &&
        node.getAttribute('data-was-disabled-offline') === '1'
      ) {
        node.disabled = false;
      }
      node.removeAttribute('aria-disabled');
      node.style.removeProperty('pointer-events');
      node.removeAttribute('data-was-disabled-offline');
    }
  }

  /**
   * Called whenever a response header set is available (fetch or
   * HTMX). Extracts ``X-Data-Generated-At`` and stores it for the
   * banner's next render.
   *
   * @param {(name: string) => string | null} getHeader
   */
  function absorbFreshness(getHeader) {
    const value = getHeader('X-Data-Generated-At');
    if (!value) return;
    const parsed = new Date(value);
    if (Number.isNaN(parsed.valueOf())) return;
    lastGeneratedAt = parsed;
    // If the banner is already open, refresh its label live so the user
    // sees the age tick down when a stale cached response arrives.
    if (!navigator.onLine) renderBanner(false);
  }

  /**
   * Wrap ``window.fetch`` so every response participates in the
   * freshness ledger and every network failure flips the banner.
   */
  function wrapFetch() {
    if (typeof window.fetch !== 'function') return;
    const original = window.fetch.bind(window);
    window.fetch = async function (...args) {
      try {
        const response = await original(...args);
        try {
          absorbFreshness((name) => response.headers.get(name));
        } catch (_err) {
          // Never break the caller on a header quirk.
        }
        return response;
      } catch (err) {
        // Network failure — reveal the banner. Rethrow so callers can
        // still handle the failure themselves.
        renderBanner(false);
        throw err;
      }
    };
  }

  /**
   * Hook the HTMX post-response event so XHR-driven traffic also feeds
   * the freshness ledger, and the ``htmx:sendError`` event so a failed
   * mutation surfaces the banner immediately.
   */
  function wrapHtmx() {
    document.body?.addEventListener('htmx:afterOnLoad', (evt) => {
      const xhr = evt?.detail?.xhr;
      if (!xhr || typeof xhr.getResponseHeader !== 'function') return;
      try {
        absorbFreshness((name) => xhr.getResponseHeader(name));
      } catch (_err) {
        // Ignore.
      }
    });
    document.body?.addEventListener('htmx:sendError', () => {
      renderBanner(false);
    });
  }

  /**
   * Bind ``online`` / ``offline`` events on window so the banner and
   * network-required elements track the connection state without
   * requiring a page reload.
   */
  function bindConnectionEvents() {
    window.addEventListener('online', () => {
      renderBanner(true);
      syncNetworkRequired(true);
    });
    window.addEventListener('offline', () => {
      renderBanner(false);
      syncNetworkRequired(false);
    });
  }

  // Prime the initial state. If the page loaded while offline (unlikely
  // via the browser — offline navigations normally show the browser's
  // own error page — but possible via the SW cache), we want the banner
  // up immediately, not on the first failed fetch.
  bindConnectionEvents();
  wrapFetch();
  wrapHtmx();
  renderBanner(navigator.onLine);
  syncNetworkRequired(navigator.onLine);
})();
