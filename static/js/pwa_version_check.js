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
 * Header drift is a HINT, not a verdict (staging stuck-banner fix)
 * ----------------------------------------------------------------
 * The version headers ride on every response — including responses the
 * browser HTTP cache or the service worker's stale-while-revalidate cache
 * replays from before a deploy (``/api/ratings/`` and the geo feeds carry
 * ``Cache-Control: public, max-age=300``/``3600``). Right after a deploy
 * those replayed responses still carry the PREVIOUS build's
 * ``X-App-Version``, which looks exactly like a real drift. Acting on the
 * header directly showed a phantom "Update available" banner that the
 * Reload button could never clear — clearing Cache Storage does not touch
 * the browser HTTP cache, so the stale header came straight back after
 * the reload.
 *
 * So an observed drift now only *schedules a verification*: one
 * authoritative ``fetch('/api/version', {cache: 'no-store'})`` (via the
 * pristine pre-wrap fetch, so it cannot recurse into this check), acting
 * on the response *body* — which is by definition as fresh as its own
 * headers. Only a confirmed drift reveals the banner or modal; a
 * confirmed-clean check memoises the stale header value so replays of the
 * same cached response don't re-trigger the round trip.
 *
 * 24h escalation (§3.9): the soft banner sticks — but if it has been
 * showing for more than 24h without acceptance, the very next cold launch
 * shows the blocking modal instead. This runs once at page load, not on
 * every response, so mid-session escalation is not annoying. The stamp is
 * verified against ``/api/version`` before the modal blocks anything — a
 * stamp planted by a stale-cache false positive is cleared, not escalated.
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
  const VERSION_ENDPOINT = '/api/version';

  // The pristine fetch, captured BEFORE wrapFetch() replaces window.fetch.
  // Verification requests go out through this so they can never recurse
  // back into inspectHeaders (and never re-observe their own headers).
  const pristineFetch =
    typeof window.fetch === 'function' ? window.fetch.bind(window) : null;

  // Latch — once set, we've triggered a forced update and further
  // responses should not re-run the flow.
  let forcedUpdateTriggered = false;
  // Header values already resolved by a verification round trip, so a
  // replay of the same cached response doesn't fetch /api/version again:
  // ``staleConfirmed`` — the server said we're current, the header was a
  // cache artefact; ``driftConfirmed`` — the server confirmed a real
  // drift (the soft banner is already showing, and it is sticky).
  const staleConfirmed = new Set();
  const driftConfirmed = new Set();
  // Single-flight guard for the verification fetch.
  let verifyInFlight = null;
  // SNOW-384: separate latch so pwa.forced_update.triggered fires exactly
  // once regardless of which caller reaches showBlockingModal() first —
  // both existing callers already gate on forcedUpdateTriggered before
  // calling, so this is defence in depth rather than a load-bearing guard.
  let modalTelemetryEmitted = false;

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
   *
   * @param {'min_version' | 'escalation'} trigger What caused the forced
   *   update: an immediate server min-version mismatch, or the 24h soft-
   *   banner escalation on cold launch. Stamped on the telemetry event
   *   for dashboard breakdown.
   */
  function showBlockingModal(trigger) {
    const modal = document.getElementById('pwa-update-modal');
    if (!modal) return;
    modal.classList.remove('hidden');
    // Prevent the underlying page from scrolling while the modal owns
    // focus. Restored on reload; we never intentionally hide the modal
    // once shown.
    document.documentElement.style.overflow = 'hidden';
    // SNOW-384: pwa.forced_update.triggered is critical (telemetry.js
    // CRITICAL_EVENTS) — sendBeacon fires immediately.
    if (!modalTelemetryEmitted) {
      modalTelemetryEmitted = true;
      try {
        window.pwaTelemetry?.emit('pwa.forced_update.triggered', {
          trigger: trigger || 'unknown',
        });
      } catch (_err) {
        // Ignore — telemetry must never block the forced-update flow.
      }
    }
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
    // SNOW-585: only present when settings.SW_DEV_SHELL_BYPASS is on (base.html;
    // always false in production). The bypass already serves fresh shell
    // assets on the very next reload, so a banner asking the developer to
    // reload would be misleading. See
    // docs/decisions/dev-bypasses-the-shell-cache.md.
    if (readMeta('pwa-dev-shell-bypass') === '1') return;
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
   * The full local-data wipe, then a reload.
   *
   * SNOW-615: delegates to ``window.pwaResetLocalData`` (static/js/pwa_reset.js)
   * rather than reimplementing it. This file used to carry its own copy,
   * whose docstring called it "the nuclear-option recovery path" while
   * leaving IndexedDB — the mutation queue, the offline favourites roster,
   * the cached ratings — entirely intact. `pwa_reset.js`'s own header
   * already claimed this module called it; now it does.
   *
   * Falls back to a plain reload when `pwa_reset.js` has not loaded — this
   * runs on a page that may already be part-broken, so the reload is the
   * floor rather than the plan.
   *
   * @returns {Promise<void>}
   */
  async function resetAndReload() {
    try {
      localStorage.removeItem(FIRST_SHOWN_KEY);
    } catch (_err) {
      // Ignore — this key is a soft-banner throttle, not state the reset
      // depends on.
    }
    if (typeof window.pwaResetLocalData !== 'function') {
      window.location.reload();
      return;
    }
    await window.pwaResetLocalData(true);
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
   * Fetch the authoritative version verdict from ``/api/version``,
   * bypassing every cache layer (``cache: 'no-store'`` skips the browser
   * HTTP cache; the SW classifies the path as network-only so Cache
   * Storage never sees it). Uses the pristine pre-wrap fetch so the
   * request cannot recurse into ``inspectHeaders``.
   *
   * @returns {Promise<{current: string, min_supported: string} | null>}
   *   The trimmed body fields, or ``null`` when the endpoint is
   *   unreachable / non-2xx — "cannot confirm" must never be treated as
   *   "confirmed drift".
   */
  async function fetchAuthoritativeVersion() {
    if (!pristineFetch) return null;
    try {
      const res = await pristineFetch(VERSION_ENDPOINT, { cache: 'no-store' });
      if (!res || !res.ok) return null;
      const json = await res.json();
      return {
        current: String(json.current || '').trim(),
        min_supported: String(json.min_supported || '').trim(),
      };
    } catch (_err) {
      return null;
    }
  }

  /**
   * Resolve an observed header drift into one of the three outcomes,
   * using the authoritative body rather than the (possibly cache-stale)
   * header that triggered us:
   *
   *   * forced-update → open the modal, wipe local state, wait for
   *     user click.
   *   * soft-update   → reveal the sticky banner.
   *   * fresh         → memoise the stale header values; no-op.
   *
   * Single-flight: concurrent observations share one round trip.
   *
   * @param {string[]} observed The drifting header values that prompted
   *   this verification, memoised under whichever verdict comes back.
   * @returns {Promise<void>}
   */
  function verifyObservedDrift(observed) {
    if (verifyInFlight) return verifyInFlight;
    verifyInFlight = fetchAuthoritativeVersion()
      .then((verdict) => {
        if (!verdict || forcedUpdateTriggered) return;

        // Min-version verdict wins. A non-empty min-version that does not
        // match the shell we were delivered on is a forced-update signal;
        // spec §3.4 is deliberate that this must not be dismissable.
        if (
          verdict.min_supported &&
          differs(verdict.min_supported, CURRENT_BUILD)
        ) {
          forcedUpdateTriggered = true;
          showBlockingModal('min_version');
          // Best-effort: wipe local caches / SW immediately so a
          // page-visible hang doesn't leave the user in a half-broken
          // state. The reload is user-initiated (click), because a
          // synchronous reload here would be confusing.
          resetAndReload().catch(() => {});
          return;
        }

        if (verdict.current && differs(verdict.current, CURRENT_BUILD)) {
          observed.forEach((value) => driftConfirmed.add(value));
          showSoftBanner();
          return;
        }

        // Server says we ARE current — the observed headers were replayed
        // from a pre-deploy cache entry. Remember them so the same cached
        // responses don't re-trigger the round trip, and clear any
        // first-shown stamp so a phantom banner from a pre-fix session
        // can't later escalate to the blocking modal.
        observed.forEach((value) => staleConfirmed.add(value));
        try {
          localStorage.removeItem(FIRST_SHOWN_KEY);
        } catch (_err) {
          // Ignore.
        }
      })
      .finally(() => {
        verifyInFlight = null;
      });
    return verifyInFlight;
  }

  /**
   * Consume the two version headers from any completed response. A drift
   * against the shell's build is only a HINT that an update might exist —
   * the response may have been replayed from the browser HTTP cache or
   * the SW's stale-while-revalidate cache with pre-deploy headers — so
   * the outcome is decided by ``verifyObservedDrift`` against the
   * authoritative ``/api/version`` body, never by the header alone.
   *
   * @param {(name: string) => string | null} getHeader
   */
  function inspectHeaders(getHeader) {
    if (forcedUpdateTriggered) return;
    const serverMin = (getHeader('X-App-Min-Version') || '').trim();
    const serverVer = (getHeader('X-App-Version') || '').trim();

    const observed = [];
    for (const value of [serverMin, serverVer]) {
      if (!value || !differs(value, CURRENT_BUILD)) continue;
      if (staleConfirmed.has(value)) continue;
      if (driftConfirmed.has(value)) {
        // Already verified as a real drift — keep the sticky banner
        // visible without another round trip.
        showSoftBanner();
        continue;
      }
      observed.push(value);
    }
    if (observed.length) verifyObservedDrift(observed);
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
   *
   * The stamp alone is not trusted: it may have been planted by a
   * stale-cache false positive (see the header-drift section of the
   * module comment). The server confirms the drift via ``/api/version``
   * before the modal blocks anything; a stamp the server disowns is
   * cleared instead. An unreachable endpoint escalates nothing — a
   * blocking modal on unverifiable evidence would strand offline users.
   */
  function maybeEscalateOnColdLaunch() {
    try {
      const raw = localStorage.getItem(FIRST_SHOWN_KEY);
      if (!raw) return;
      const shownAt = Number(raw);
      if (!Number.isFinite(shownAt)) return;
      if (Date.now() - shownAt < ESCALATION_MS) return;
    } catch (_err) {
      // No storage access — nothing to escalate.
      return;
    }
    fetchAuthoritativeVersion().then((verdict) => {
      if (!verdict || forcedUpdateTriggered) return;
      const drifted =
        (verdict.current && differs(verdict.current, CURRENT_BUILD)) ||
        (verdict.min_supported &&
          differs(verdict.min_supported, CURRENT_BUILD));
      if (drifted) {
        forcedUpdateTriggered = true;
        showBlockingModal('escalation');
        return;
      }
      // The server says this shell IS current — the stamp is a leftover
      // false positive. Clear it so it never escalates again.
      try {
        localStorage.removeItem(FIRST_SHOWN_KEY);
      } catch (_err) {
        // Ignore.
      }
    });
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
