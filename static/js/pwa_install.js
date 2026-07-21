/*
 * static/js/pwa_install.js — Install-prompt orchestration (SNOW-379).
 *
 * Spec §3.2, §9.1.1, §9.1.2. Delays the install nudge until the user
 * has actually experienced the app, then routes them through the right
 * flow for their platform. Push notifications, badges, and reliable
 * storage on iOS all depend on home-screen install — so the prompt is
 * not a growth hack, it's a prerequisite for the rest of the offline-
 * first experience.
 *
 * Meaningful-action threshold
 * ---------------------------
 * Either satisfies the threshold — checked whenever engagement is
 * observed, so the nudge fires at the earlier of the two:
 *
 *   * ``regions_viewed >= 2``  — distinct MicroRegion IDs seen in the
 *                                URL path. Detected by matching the
 *                                bulletin URL pattern.
 *   * ``time_engaged >= 30s``  — cumulative foreground time. Ticked by
 *                                a 30-second setTimeout scheduled on
 *                                every ``visibilitychange`` → visible.
 *
 * Platform routing
 * ----------------
 *   * Chromium / Edge (Android + desktop): capture ``beforeinstallprompt``,
 *     defer, reveal the ``#pwa-install-banner`` when the threshold hits.
 *     "Install" calls the deferred ``prompt()`` and awaits the user's
 *     choice.
 *   * iOS Safari (feature-detected via ``'standalone' in navigator`` +
 *     UA sniff): no ``beforeinstallprompt`` on iOS, so reveal the
 *     ``#pwa-install-ios`` visual guide instead — an animated
 *     Share → Add to Home Screen illustration.
 *
 * State (via localStorage)
 * ------------------------
 *   pwa.install.regions_viewed  — JSON array of region_id strings
 *   pwa.install.time_engaged_ms — cumulative foreground ms
 *   pwa.install.dismissed_at    — ms timestamp of last dismiss; the
 *                                 nudge is suppressed for 30 days
 *   pwa.install.installed_at    — ms timestamp of confirmed install
 *
 * When SNOW-375 lands, migrate these keys into the IndexedDB
 * ``meta:app`` object store; the keys are read/written through the
 * ``storage`` helpers below so the migration is a one-liner.
 *
 * Persistent storage
 * ------------------
 * Once installed AND (on iOS) notification permission is granted,
 * ``navigator.storage.persist()`` is called to request that the OS keep
 * our IndexedDB / Cache Storage across storage-pressure evictions. The
 * result is logged as ``pwa.storage.persisted``.
 *
 * Telemetry (SNOW-384)
 * --------------------
 * Wired to ``window.pwaTelemetry?.emit(event, properties)``
 * (``static/js/telemetry.js``, SNOW-385) via the local ``emitTelemetry``
 * helper, which always stamps ``properties.platform`` (``'ios'`` |
 * ``'android'`` | ``'desktop'``, UA-sniffed):
 *   pwa.install.prompted                — our banner was revealed
 *   pwa.install.accepted                — user accepted the native prompt
 *   pwa.install.dismissed               — user dismissed our banner (30-day cool-off)
 *   pwa.install.completed               — ``appinstalled`` fired / standalone launch detected
 *   pwa.install.notifications_granted   — iOS install completed with Notification.permission
 *                                          already 'granted' (see onInstalled())
 *   pwa.storage.persisted               — still console.info only; not in
 *                                          analytics.schema.ALLOWED_EVENTS,
 *                                          out of SNOW-384 scope.
 */

(function () {
  'use strict';

  const REGIONS_KEY = 'pwa.install.regions_viewed';
  const TIME_KEY = 'pwa.install.time_engaged_ms';
  const DISMISS_KEY = 'pwa.install.dismissed_at';
  const INSTALLED_KEY = 'pwa.install.installed_at';

  const REGIONS_THRESHOLD = 2;
  const TIME_THRESHOLD_MS = 30 * 1000;
  const DISMISS_COOLOFF_MS = 30 * 24 * 60 * 60 * 1000;
  const ENGAGEMENT_TICK_MS = 5 * 1000;

  // Bulletin URL pattern the region tracker looks for. Snowdesk routes
  // are ``/{country}/{region_id}/…`` where region_id includes the
  // country prefix (``ch-3232``, ``at-07-01``). The regex is generous
  // on the trailing path so it matches both bulletin pages and any
  // future region-scoped surfaces.
  const REGION_PATH_RE = /^\/[a-z]{2}\/([a-z]{2}-[a-z0-9-]+)(\/|$)/i;

  // The ``beforeinstallprompt`` event, deferred until the nudge fires.
  let deferredPrompt = null;

  /**
   * Robust localStorage getter — Safari private mode throws.
   * @param {string} k
   * @returns {string | null}
   */
  function readItem(k) {
    try {
      return localStorage.getItem(k);
    } catch (_err) {
      return null;
    }
  }

  /**
   * Robust localStorage setter — swallows quota / private-mode errors.
   * @param {string} k
   * @param {string} v
   */
  function writeItem(k, v) {
    try {
      localStorage.setItem(k, v);
    } catch (_err) {
      // Ignore.
    }
  }

  /**
   * True when the app is running as a home-screen installed shell,
   * either standard PWA display-mode or iOS Safari's legacy attribute.
   * @returns {boolean}
   */
  function isStandalone() {
    try {
      return (
        window.matchMedia('(display-mode: standalone)').matches ||
        navigator.standalone === true
      );
    } catch (_err) {
      return false;
    }
  }

  /**
   * Feature-detect iOS Safari. ``'standalone' in navigator`` is
   * exclusive to iOS Safari; combined with a UA regex we avoid false
   * positives from desktop Safari devtools emulation.
   * @returns {boolean}
   */
  function isIOS() {
    try {
      return (
        'standalone' in navigator &&
        /iP(hone|ad|od)/.test(navigator.userAgent) &&
        !('MSStream' in window)
      );
    } catch (_err) {
      return false;
    }
  }

  /**
   * Coarse platform bucket for the install-funnel telemetry properties
   * (SNOW-384): ``'ios'`` | ``'android'`` | ``'desktop'``. UA-sniffed —
   * good enough for a dashboard breakdown, not used for any behavioural
   * branch (that's ``isIOS()`` / the deferredPrompt presence).
   * @returns {'ios' | 'android' | 'desktop'}
   */
  function installPlatform() {
    try {
      if (isIOS()) return 'ios';
      if (/Android/.test(navigator.userAgent)) return 'android';
    } catch (_err) {
      // Fall through to desktop.
    }
    return 'desktop';
  }

  /**
   * Best-effort telemetry emit — never breaks the install flow.
   * @param {string} event
   * @param {object} [properties]
   */
  function emitTelemetry(event, properties) {
    try {
      window.pwaTelemetry?.emit(event, { platform: installPlatform(), ...properties });
    } catch (_err) {
      // Ignore.
    }
  }

  /**
   * Read the deduped set of region_ids the user has visited.
   * @returns {string[]}
   */
  function loadRegionsViewed() {
    const raw = readItem(REGIONS_KEY);
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter((s) => typeof s === 'string') : [];
    } catch (_err) {
      return [];
    }
  }

  /**
   * Push a region_id onto the visited list (deduped). Returns the new
   * length so callers can check the threshold in one call.
   * @param {string} regionId
   * @returns {number}
   */
  function recordRegionVisit(regionId) {
    const list = loadRegionsViewed();
    if (list.includes(regionId)) return list.length;
    list.push(regionId);
    writeItem(REGIONS_KEY, JSON.stringify(list));
    return list.length;
  }

  /**
   * Increment the cumulative engaged-time counter.
   * @param {number} deltaMs
   * @returns {number}
   */
  function addEngagement(deltaMs) {
    const current = Number(readItem(TIME_KEY)) || 0;
    const next = current + Math.max(0, deltaMs);
    writeItem(TIME_KEY, String(next));
    return next;
  }

  /**
   * True when the meaningful-action threshold has been met.
   * @returns {boolean}
   */
  function meetsThreshold() {
    if (loadRegionsViewed().length >= REGIONS_THRESHOLD) return true;
    if ((Number(readItem(TIME_KEY)) || 0) >= TIME_THRESHOLD_MS) return true;
    return false;
  }

  /**
   * True when the user recently dismissed the prompt — suppress nudges
   * for the 30-day cool-off period. Timestamps outside the window are
   * treated as if unset.
   * @returns {boolean}
   */
  function isInCoolOff() {
    const at = Number(readItem(DISMISS_KEY));
    if (!Number.isFinite(at) || at <= 0) return false;
    return Date.now() - at < DISMISS_COOLOFF_MS;
  }

  /**
   * Detect the region_id on the current URL and record it. No-op when
   * the URL doesn't match the bulletin pattern.
   */
  function trackCurrentRegion() {
    try {
      const match = REGION_PATH_RE.exec(window.location.pathname);
      if (!match) return;
      recordRegionVisit(match[1].toLowerCase());
    } catch (_err) {
      // Ignore.
    }
  }

  /**
   * Reveal the appropriate nudge for the current platform, gated on
   * threshold + cool-off + install state.
   */
  function maybeReveal() {
    if (isStandalone()) return; // Already installed.
    if (isInCoolOff()) return; // Recently dismissed.
    if (!meetsThreshold()) return; // Not enough engagement yet.
    if (isIOS()) {
      revealBanner('pwa-install-ios');
      return;
    }
    if (deferredPrompt) revealBanner('pwa-install-banner');
  }

  /**
   * Reveal one of the two banner partials. Idempotent — safe to call
   * repeatedly.
   * @param {string} id
   */
  function revealBanner(id) {
    const el = document.getElementById(id);
    if (!el || !el.classList.contains('hidden')) return;
    el.classList.remove('hidden');
    emitTelemetry('pwa.install.prompted', { banner: id });
  }

  /**
   * Hide the given banner and stamp the dismiss timestamp so the
   * 30-day cool-off applies.
   * @param {string} id
   */
  function dismissBanner(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('hidden');
    writeItem(DISMISS_KEY, String(Date.now()));
    emitTelemetry('pwa.install.dismissed', { banner: id });
  }

  /**
   * Bind the visible controls on the two banners.
   */
  function bindBanners() {
    const android = document.getElementById('pwa-install-banner');
    if (android) {
      document
        .getElementById('pwa-install-accept')
        ?.addEventListener('click', async () => {
          if (!deferredPrompt) {
            dismissBanner('pwa-install-banner');
            return;
          }
          try {
            deferredPrompt.prompt();
            const choice = await deferredPrompt.userChoice;
            if (choice && choice.outcome === 'accepted') {
              emitTelemetry('pwa.install.accepted');
            } else {
              dismissBanner('pwa-install-banner');
            }
          } catch (_err) {
            dismissBanner('pwa-install-banner');
          } finally {
            deferredPrompt = null;
          }
        });
    }
    // SNOW-486: the "×" on both banners is [data-action="dismiss"] inside
    // a [data-overlay] element, so static/js/overlays.js's shared delegated
    // handler now owns the actual hide (classList.add('hidden')) — this
    // module only needs to run the cool-off-stamp + telemetry side effect
    // that dismissBanner() previously did inline. The accept-button
    // failure/rejection paths above still call dismissBanner() directly
    // (they need the hide too, and there's no click on a data-overlay
    // descendant to delegate from there).
    document.addEventListener('overlay:dismissed', (event) => {
      const el = event.detail && event.detail.overlay;
      if (!el) return;
      if (el.id !== 'pwa-install-banner' && el.id !== 'pwa-install-ios') return;
      writeItem(DISMISS_KEY, String(Date.now()));
      emitTelemetry('pwa.install.dismissed', { banner: el.id });
    });
  }

  /**
   * Bind the engagement tick — every 5 seconds while the page is
   * visible, add that much to the counter and re-check the threshold.
   */
  function bindEngagementTick() {
    let intervalId = null;
    function start() {
      if (intervalId != null) return;
      intervalId = setInterval(() => {
        addEngagement(ENGAGEMENT_TICK_MS);
        maybeReveal();
      }, ENGAGEMENT_TICK_MS);
    }
    function stop() {
      if (intervalId == null) return;
      clearInterval(intervalId);
      intervalId = null;
    }
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stop();
      else start();
    });
    if (!document.hidden) start();
  }

  /**
   * Ask the browser to make our storage persistent. Called once we can
   * plausibly know the user is committed (installed + notification-
   * permission-granted on iOS).
   */
  async function requestPersistentStorage() {
    try {
      if (!('storage' in navigator) || typeof navigator.storage.persist !== 'function') {
        return;
      }
      const persisted = await navigator.storage.persist();
      try {
        console.info('pwa.storage.persisted', { result: persisted });
      } catch (_err) {
        // Ignore.
      }
    } catch (_err) {
      // Ignore.
    }
  }

  /**
   * Called after install detection. Records timestamp, requests
   * persistent storage.
   */
  async function onInstalled() {
    writeItem(INSTALLED_KEY, String(Date.now()));
    emitTelemetry('pwa.install.completed');
    // On iOS, storage.persist() only reliably returns true once
    // notification permission has been granted. Elsewhere the browser
    // may auto-grant based on install state.
    if (isIOS()) {
      try {
        if (
          'Notification' in window &&
          Notification.permission === 'granted'
        ) {
          // SNOW-384: this is the only notification-permission check in
          // this file — pwa_install.js never itself calls
          // Notification.requestPermission() (that lives in the staff
          // push demo, static/js/push_demo.js, a separate flow). This
          // observes permission already granted by the time install
          // completes, which is the funnel signal the dashboard wants.
          emitTelemetry('pwa.install.notifications_granted');
          await requestPersistentStorage();
        }
      } catch (_err) {
        // Ignore.
      }
    } else {
      await requestPersistentStorage();
    }
  }

  // Capture the browser's install prompt so we can defer it.
  window.addEventListener('beforeinstallprompt', (evt) => {
    evt.preventDefault();
    deferredPrompt = evt;
    maybeReveal();
  });

  window.addEventListener('appinstalled', () => {
    onInstalled().catch(() => {});
  });

  function ready() {
    // Standalone launch — user already installed; fire the post-install
    // one-time work if we haven't recorded it yet.
    if (isStandalone() && !readItem(INSTALLED_KEY)) {
      onInstalled().catch(() => {});
    }
    trackCurrentRegion();
    bindBanners();
    bindEngagementTick();
    maybeReveal();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
