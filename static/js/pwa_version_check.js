/*
 * static/js/pwa_version_check.js — Client-side version-check gate (SNOW-374).
 *
 * Spec §3.4, §12.10 (non-negotiable). Layers a forced-update gate on top of
 * the soft ``#sw-update-banner`` (SNOW-331 / sw_register.js). The server is
 * the source of truth via one header stamped on every response by
 * ``core.middleware.AppVersionHeaderMiddleware`` (SNOW-369):
 *
 *   X-App-Version — the build the server is serving right now.
 *
 * Contract
 * --------
 * The client's "current build" is baked into the page it was delivered on
 * (``<meta name="pwa-app-version">``). When ``X-App-Version`` on any
 * response differs from it, one authoritative ``/api/version`` round trip
 * decides between two outcomes:
 *
 *   1. body ``update_required: true`` → **Update Required** state. The
 *      blocking modal opens and stays open; nothing is wiped and nothing
 *      reloads until the user clicks "Reload now". No dismiss control.
 *
 *   2. body ``current`` differs from the shell's build → reveal the
 *      existing soft ``#sw-update-banner``. This is the same visible
 *      affordance as the SW-update flow.
 *
 * The client performs NO version arithmetic (SNOW-609)
 * ----------------------------------------------------
 * This module used to compare the shell's build against a server-declared
 * ``min_supported`` floor with ``differs()`` — string inequality, not
 * ordering. ``APP_VERSION`` resolves to a git SHA, and SHAs have no order,
 * so every client compared as "below the floor" and any real floor value
 * would have swept every client, newest included, into the forced-update
 * path. A floor is not expressible on either side of the wire, so the
 * concept is gone: the server names the specific builds it refuses to keep
 * serving (``settings.APP_BLOCKED_VERSIONS``) and returns the verdict as a
 * single boolean. ``differs()`` survives only as the soft-banner hint —
 * "not the same string" is all that check ever needed.
 *
 * Note the practical consequence: the verdict is only read when an
 * ``X-App-Version`` drift schedules the round trip, so blocking the build
 * the server is itself serving does nothing. That matches how a block is
 * actually issued — deploy the replacement first, then list the build being
 * retired.
 *
 * Header drift is a HINT, not a verdict (staging stuck-banner fix)
 * ----------------------------------------------------------------
 * The version header rides on every response — including responses the
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
 * Sources checked
 * ---------------
 * * ``window.fetch`` — wrapped so every JS-issued request participates.
 * * ``htmx:afterOnLoad`` — HTMX uses XMLHttpRequest, not fetch, so its
 *   responses are inspected via the DOM event.
 *
 * Failure modes
 * -------------
 * * An unreachable ``/api/version`` reveals nothing. "Cannot confirm" must
 *   never read as "confirmed" — a blocking modal on unverifiable evidence
 *   would strand offline users.
 * * If the ``<meta>`` tag is missing (a stale template), the wrapper is a
 *   no-op — safer than firing spurious update prompts when we don't know
 *   what version the shell was delivered on.
 */

(function () {
  'use strict';

  const CURRENT_BUILD = readMeta('pwa-app-version');
  if (!CURRENT_BUILD) return; // Meta tag absent — bail safely.

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
  // the caller already gates on forcedUpdateTriggered before calling, so
  // this is defence in depth rather than a load-bearing guard.
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
   * @param {'blocked_build'} trigger What caused the forced update.
   *   Stamped on the telemetry event for dashboard breakdown. SNOW-609
   *   left exactly one value: the server listed this build in
   *   ``APP_BLOCKED_VERSIONS``. The previous ``'min_version'`` and
   *   ``'escalation'`` triggers are both gone.
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
   * inline-styled) uses ``display: flex`` instead.
   *
   * SNOW-623: that fork is ``sw_register.js``'s, reached through
   * ``window.pwaUpdateBanner``. This file used to carry its own copy, under
   * a docstring telling the reader to "mirror" the other one — which is
   * how two owners of one element stay in step only as long as somebody
   * remembers. The dev-bypass suppression is checked there too, so the
   * banner cannot be revealed by either path under the bypass.
   *
   * SNOW-609: nothing else stays here. This function used to also stamp
   * ``localStorage['pwa.update.first_shown_at']``, the clock behind the
   * 24h escalation to the blocking modal. That escalation is gone — it
   * blocked the app on elapsed time rather than on any server statement
   * that the build was unacceptable — and the stamp had no other reader,
   * so revealing the banner is now the whole job.
   */
  function showSoftBanner() {
    if (!window.pwaUpdateBanner) return;
    window.pwaUpdateBanner.reveal();
  }

  /**
   * Drop the SW shell caches, then reload — the forced update's whole
   * effect on local state.
   *
   * SNOW-615 routed this through ``window.pwaResetLocalData(true)``, the
   * six-step everything-goes wipe, to settle a mismatch between this
   * function's name and what it did. SNOW-609 settles the same mismatch
   * the other way for this path, and the name changed with it: a code
   * update must not destroy user data. The full wipe also takes
   * IndexedDB — the mutation queue, the offline favourites roster, the
   * cached ratings — and every pinned basemap bucket, which can be
   * hundreds of megabytes the user deliberately downloaded. None of that
   * is code, and none of it is why the build is blocked.
   *
   * ``sw_register.js:657-661`` already made exactly this trade for the
   * kill switch: caches are left alone there because ops flipping a
   * temporary switch should not cost a user their downloads. The
   * forced-update path now agrees with its neighbour. If you are here to
   * "fix" this back to a full wipe, that is the argument to answer first.
   *
   * ``window.pwaResetLocalData`` (pwa_reset.js) remains the
   * everything-goes path, reachable from the manage page and the offline
   * page, and it asks for confirmation first.
   *
   * Falls back to a plain reload when the global is absent (``sw_register.js``
   * returns early on a browser with no ``navigator.serviceWorker``) —
   * this runs on a page that may already be part-broken, so the reload is
   * the floor rather than the plan.
   *
   * @returns {Promise<void>}
   */
  async function clearShellAndReload() {
    if (typeof window.pwaClearShellCachesAndReload !== 'function') {
      window.location.reload();
      return;
    }
    await window.pwaClearShellCachesAndReload();
  }

  /**
   * Compare two version strings for inequality.
   *
   * The soft-banner hint only, since SNOW-609 — "the server is serving a
   * different string than the one this shell was delivered on" is the
   * whole question there, and inequality answers it exactly. It is NOT
   * used to decide whether a build is acceptable; that is
   * ``update_required``, which the server computes.
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
   * @returns {Promise<{current: string, update_required: boolean} | null>}
   *   The trimmed ``current`` and the server's forced-update verdict, or
   *   ``null`` when the endpoint is unreachable / non-2xx — "cannot
   *   confirm" must never be treated as "confirmed".
   */
  async function fetchAuthoritativeVersion() {
    if (!pristineFetch) return null;
    try {
      const res = await pristineFetch(VERSION_ENDPOINT, { cache: 'no-store' });
      if (!res || !res.ok) return null;
      const json = await res.json();
      return {
        current: String(json.current || '').trim(),
        // Strict ``=== true``: a server that predates SNOW-609 omits the
        // field entirely, and an absent verdict must read as "not
        // blocked" rather than as truthiness on ``undefined``.
        update_required: json.update_required === true,
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
   *   * forced-update → open the modal and leave it open.
   *   * soft-update   → reveal the sticky banner.
   *   * fresh         → memoise the stale header value; no-op.
   *
   * Single-flight: concurrent observations share one round trip.
   *
   * @param {string} observed The drifting header value that prompted this
   *   verification, memoised under whichever verdict comes back.
   * @returns {Promise<void>}
   */
  function verifyObservedDrift(observed) {
    if (verifyInFlight) return verifyInFlight;
    verifyInFlight = fetchAuthoritativeVersion()
      .then((verdict) => {
        if (!verdict || forcedUpdateTriggered) return;

        // The server's verdict wins. Spec §3.4 is deliberate that this
        // must not be dismissable — and SNOW-609 is deliberate that it
        // must not act on its own either: nothing is cleared and nothing
        // reloads until the user clicks "Reload now" (``bindModal``).
        // The old auto-reset raced the reveal, so the copy explaining
        // what was about to happen was torn down before it could be
        // read.
        if (verdict.update_required) {
          forcedUpdateTriggered = true;
          showBlockingModal('blocked_build');
          return;
        }

        if (verdict.current && differs(verdict.current, CURRENT_BUILD)) {
          driftConfirmed.add(observed);
          showSoftBanner();
          return;
        }

        // Server says we ARE current — the observed header was replayed
        // from a pre-deploy cache entry. Remember it so the same cached
        // responses don't re-trigger the round trip.
        staleConfirmed.add(observed);
      })
      .finally(() => {
        verifyInFlight = null;
      });
    return verifyInFlight;
  }

  /**
   * Consume the version header from any completed response. A drift
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
    // SNOW-609: ``X-App-Min-Version`` was the other header read here. The
    // server no longer sends it, and there is no floor left to compare
    // against.
    const serverVer = (getHeader('X-App-Version') || '').trim();
    if (!serverVer || !differs(serverVer, CURRENT_BUILD)) return;
    if (staleConfirmed.has(serverVer)) return;
    if (driftConfirmed.has(serverVer)) {
      // Already verified as a real drift — keep the sticky banner visible
      // without another round trip.
      showSoftBanner();
      return;
    }
    verifyObservedDrift(serverVer);
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
   * Bind the modal's Reload button. Idempotent.
   *
   * SNOW-609: this is now the only caller of ``clearShellAndReload``. It
   * was dead code before — the forced-update branch reset and reloaded
   * under the open modal, so the page was gone before the button could be
   * clicked.
   */
  function bindModal() {
    const btn = document.getElementById('pwa-update-modal-reload');
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      clearShellAndReload().catch(() => window.location.reload());
    });
  }

  // Kick off. The pwa-app-version meta tag being present is our signal
  // that the shell knows about this contract.
  bindModal();
  wrapFetch();
  wrapHtmx();
})();
