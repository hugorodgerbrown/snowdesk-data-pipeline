/*
 * static/js/pwa_version_check.js — Client-side version-check gate (SNOW-374).
 *
 * Spec §3.4, §3.9, §12.10 (non-negotiable). Layers a forced-update gate on
 * top of the soft ``#sw-update-banner`` (SNOW-331 / sw_register.js). The
 * server is the source of truth via two headers stamped on every response by
 * ``core.middleware.AppVersionHeaderMiddleware`` (SNOW-369):
 *
 *   X-App-Version      — the build the server is serving right now.
 *   X-App-Min-Version  — the minimum build the server will accept from a
 *                        client. Empty string ("") means "no floor
 *                        enforced" and is a no-op.
 *
 * Contract
 * --------
 * The client's "current build" is baked into the page it was delivered on
 * (``<meta name="pwa-app-version">``). On every response:
 *
 *   1. ``X-App-Min-Version`` is non-empty AND != current build →
 *      **Update Required** state.  The blocking modal opens, the SW is
 *      unregistered, Cache Storage is cleared, and the page reloads when
 *      the user clicks "Reload now". No dismiss control.
 *
 *   2. ``X-App-Version`` != current build (and no min-version bite) →
 *      reveal the existing soft ``#sw-update-banner`` and stamp
 *      ``localStorage['pwa.update.first_shown_at']`` if unset. This is the
 *      same visible affordance as the SW-update flow.
 *
 * 24h escalation (§3.9): the soft banner sticks — but if it has been
 * showing for more than 24h without acceptance, the very next cold launch
 * shows the blocking modal instead. This runs once at page load, not on
 * every response, so mid-session escalation is not annoying.
 *
 * Sources checked
 * ---------------
 * * ``window.fetch`` — wrapped so every JS-issued request participates.
 * * ``htmx:afterOnLoad`` — HTMX uses XMLHttpRequest, not fetch, so its
 *   responses are inspected via the DOM event.
 *
 * Failure modes
 * -------------
 * * A header comparison against a git-SHA ``current`` produces "different"
 *   for any change, which is the safest behaviour: unknown → escalate.
 *   CalVer versions compare correctly as strings because YYYY.MM.DD sorts
 *   lexically.
 * * If the ``<meta>`` tag is missing (a stale template), the wrapper is a
 *   no-op — safer than firing spurious update prompts when we don't know
 *   what version the shell was delivered on.
 */

(function () {
  'use strict';

  const CURRENT_BUILD = readMeta('pwa-app-version');
  if (!CURRENT_BUILD) return; // Meta tag absent — bail safely.

  const CURRENT_MIN = readMeta('pwa-app-min-version') || '';
  const FIRST_SHOWN_KEY = 'pwa.update.first_shown_at';
  const ESCALATION_MS = 24 * 60 * 60 * 1000;

  // Latch — once set, we've triggered a forced update and further
  // responses should not re-run the flow.
  let forcedUpdateTriggered = false;

  /**
   * Read a ``content`` value from a ``<meta name="…">`` tag or return ``""``
   * when the tag is missing.
   *
   * @param {string} name
   * @returns {string}
   */
  function readMeta(name) {
    const el = document.querySelector(`meta[name="${name}"]`);
    return el ? (el.getAttribute('content') || '').trim() : '';
  }

  /**
   * Reveal the blocking modal. Idempotent — safe to call twice.
   */
  function showBlockingModal() {
    const modal = document.getElementById('pwa-update-modal');
    if (!modal) return;
    modal.classList.remove('hidden');
    // Prevent the underlying page from scrolling while the modal owns
    // focus. Restored on reload; we never intentionally hide the modal
    // once shown.
    document.documentElement.style.overflow = 'hidden';
  }

  /**
   * Reveal the existing soft update banner. Delegates to whatever
   * sw_register.js has wired up — we do not manage the banner's DOM
   * ourselves so the two flows stay in sync. The public partial reveals
   * via ``hidden`` class toggle; the admin fallback (data-fallback="1",
   * inline-styled) uses ``display: flex`` instead — mirror the same fork
   * sw_register.js uses in ``showUpdateBanner``.
   */
  function showSoftBanner() {
    const banner = document.getElementById('sw-update-banner');
    if (!banner) return;
    if (banner.dataset.fallback === '1') {
      banner.style.display = 'flex';
    } else {
      banner.classList.remove('hidden');
    }
    try {
      if (!localStorage.getItem(FIRST_SHOWN_KEY)) {
        localStorage.setItem(FIRST_SHOWN_KEY, String(Date.now()));
      }
    } catch (_err) {
      // Safari private mode / storage-quota — silently continue.
    }
  }

  /**
   * The nuclear-option recovery path: unregister every SW, delete every
   * Cache Storage entry, then reload. Called from the modal's "Reload
   * now" button so the user sees a single deterministic outcome.
   */
  async function resetAndReload() {
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.unregister()));
      }
    } catch (_err) {
      // Non-fatal — reload anyway.
    }
    try {
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
    } catch (_err) {
      // Non-fatal — reload anyway.
    }
    try {
      localStorage.removeItem(FIRST_SHOWN_KEY);
    } catch (_err) {
      // Ignore.
    }
    window.location.reload();
  }

  /**
   * Compare two version strings for inequality. CalVer sorts lexically
   * ("2026.07.15" < "2026.07.16"); git-SHA comparisons always return
   * "different", which we treat as "the server has moved on".
   *
   * @param {string} a
   * @param {string} b
   * @returns {boolean}
   */
  function differs(a, b) {
    return String(a || '').trim() !== String(b || '').trim();
  }

  /**
   * Consume the two version headers from any completed response and
   * decide which of the three outcomes applies:
   *
   *   * forced-update → open the modal, wipe local state, wait for
   *     user click.
   *   * soft-update   → reveal the sticky banner.
   *   * fresh         → no-op.
   *
   * @param {(name: string) => string | null} getHeader
   */
  function inspectHeaders(getHeader) {
    if (forcedUpdateTriggered) return;
    const serverMin = (getHeader('X-App-Min-Version') || '').trim();
    const serverVer = (getHeader('X-App-Version') || '').trim();

    // Min-version verdict wins. A non-empty min-version that does not
    // match the shell we were delivered on is a forced-update signal;
    // spec §3.4 is deliberate that this must not be dismissable.
    if (serverMin && differs(serverMin, CURRENT_BUILD)) {
      forcedUpdateTriggered = true;
      showBlockingModal();
      // Best-effort: wipe local caches / SW immediately so a page-visible
      // hang doesn't leave the user in a half-broken state. The reload
      // is user-initiated (click), because a synchronous reload here
      // would be confusing.
      resetAndReload().catch(() => {});
      return;
    }

    if (serverVer && differs(serverVer, CURRENT_BUILD)) {
      showSoftBanner();
    }
  }

  /**
   * Wrap ``window.fetch`` so every JS-issued request participates in the
   * version check. The wrapper is a passthrough — the original response
   * is always returned unchanged; the version check runs as a
   * side-effect.
   */
  function wrapFetch() {
    if (typeof window.fetch !== 'function') return;
    const original = window.fetch.bind(window);
    window.fetch = async function (...args) {
      const response = await original(...args);
      try {
        inspectHeaders((name) => response.headers.get(name));
      } catch (_err) {
        // Never let the version check break the caller's fetch chain.
      }
      return response;
    };
  }

  /**
   * Hook the HTMX post-response event so XHR-driven traffic (the bulk of
   * Snowdesk's mutations) also participates.
   */
  function wrapHtmx() {
    document.body?.addEventListener('htmx:afterOnLoad', (evt) => {
      try {
        const xhr = evt?.detail?.xhr;
        if (!xhr || typeof xhr.getResponseHeader !== 'function') return;
        inspectHeaders((name) => xhr.getResponseHeader(name));
      } catch (_err) {
        // Ignore.
      }
    });
  }

  /**
   * Cold-launch step. If the soft banner has been showing for >24h,
   * upgrade to the blocking modal on this launch. Runs once at page load
   * — not on every response — so a user who is actively browsing does
   * not get an escalation mid-session.
   */
  function maybeEscalateOnColdLaunch() {
    try {
      const raw = localStorage.getItem(FIRST_SHOWN_KEY);
      if (!raw) return;
      const shownAt = Number(raw);
      if (!Number.isFinite(shownAt)) return;
      if (Date.now() - shownAt < ESCALATION_MS) return;
      forcedUpdateTriggered = true;
      showBlockingModal();
    } catch (_err) {
      // No storage access — nothing to escalate.
    }
  }

  /**
   * Bind the modal's Reload button. Idempotent.
   */
  function bindModal() {
    const btn = document.getElementById('pwa-update-modal-reload');
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      resetAndReload().catch(() => window.location.reload());
    });
  }

  // Kick off. The pwa-app-version meta tag being present is our signal
  // that the shell knows about this contract.
  bindModal();
  wrapFetch();
  wrapHtmx();
  maybeEscalateOnColdLaunch();
})();
