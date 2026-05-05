/*
 * static/js/pwa_install.js — Custom PWA install affordance (SNOW-118).
 *
 * Two flavours of install banner share one fixed slot at the bottom of
 * the viewport:
 *
 *   - ``#pwa-install-banner`` (Chromium) — revealed only after the
 *     browser has decided the page is installable and fired
 *     ``beforeinstallprompt``. The default mini-infobar is suppressed
 *     so the timing of the prompt is under our control.
 *
 *   - ``#pwa-install-ios-banner`` (iOS Safari) — revealed only when the
 *     user-agent is iOS Safari running in browser mode. iOS ignores
 *     ``beforeinstallprompt`` entirely, so the only path to install is
 *     a manual "Tap Share → Add to Home Screen" instruction.
 *
 * Both banners auto-hide once any of:
 *
 *   - ``display-mode: standalone`` matches (already installed and
 *     launched from the home screen).
 *   - ``navigator.standalone === true`` (iOS Safari running as installed PWA).
 *   - ``localStorage['snowdesk.pwa.installed']`` is set (the
 *     ``appinstalled`` event has fired in this profile already).
 *   - The user has dismissed the banner this tab session.
 *
 * Errors are logged but never surfaced — the site remains usable
 * without the install affordance, and we don't want to block rendering
 * if the deferred prompt machinery throws.
 *
 * i18n: every user-visible string lives in the banner template under
 * ``{% trans %}``; this script only toggles visibility.
 */

(function () {
  'use strict';

  const INSTALLED_KEY = 'snowdesk.pwa.installed';
  const DISMISSED_KEY = 'snowdesk.pwa.dismissed';

  /**
   * Best-effort detection of iOS Safari running in browser mode (i.e.
   * not already a home-screen PWA). The Safari user-agent string is
   * the cleanest signal still available — there is no feature-detect
   * for "is this WebKit-on-iOS in a browser tab".
   *
   * Excludes Chrome / Firefox / Edge on iOS (they all use WebKit but
   * carry their own UA tokens and behave differently around install).
   */
  function _isIosSafariBrowser() {
    const ua = navigator.userAgent || '';
    const isIos = /iPad|iPhone|iPod/.test(ua) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    if (!isIos) return false;
    const isSafari = /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
    if (!isSafari) return false;
    // navigator.standalone is true when launched from the home screen.
    return navigator.standalone !== true;
  }

  function _isStandalone() {
    if (window.matchMedia?.('(display-mode: standalone)').matches) return true;
    if (navigator.standalone === true) return true;
    return false;
  }

  function _isInstalledPersistently() {
    try {
      return localStorage.getItem(INSTALLED_KEY) === '1';
    } catch (_err) {
      return false;
    }
  }

  function _isDismissedThisSession() {
    try {
      return sessionStorage.getItem(DISMISSED_KEY) === '1';
    } catch (_err) {
      return false;
    }
  }

  function _setInstalled() {
    try {
      localStorage.setItem(INSTALLED_KEY, '1');
    } catch (_err) {
      // Ignore — quota errors here aren't recoverable and the banner
      // would have hidden via display-mode anyway.
    }
  }

  function _setDismissed() {
    try {
      sessionStorage.setItem(DISMISSED_KEY, '1');
    } catch (_err) {
      // Ignore; dismissed state lasts for the rest of the page lifetime
      // via the in-memory ``hidden`` class even without persistence.
    }
  }

  /**
   * Toggle visibility on a banner that uses Tailwind's hidden/flex
   * utilities. The HTML5 ``hidden`` attribute would lose to the
   * ``flex`` utility in the cascade.
   */
  function _show(banner, displayClass) {
    if (!banner) return;
    banner.classList.remove('hidden');
    banner.classList.add(displayClass);
  }

  function _hide(banner, displayClass) {
    if (!banner) return;
    banner.classList.remove(displayClass);
    banner.classList.add('hidden');
  }

  const chromeBanner = document.getElementById('pwa-install-banner');
  const iosBanner = document.getElementById('pwa-install-ios-banner');

  // If neither banner is in the DOM (e.g. an isolated test template),
  // nothing to do.
  if (!chromeBanner && !iosBanner) return;

  // If already installed / standalone / dismissed this session, don't
  // attach any listeners. Both banners stay ``hidden`` per their
  // class default.
  if (_isStandalone() || _isInstalledPersistently() || _isDismissedThisSession()) {
    return;
  }

  /** Chromium path: capture the deferred prompt and reveal the banner. */
  let deferredPrompt = null;

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event;
    _show(chromeBanner, 'flex');
  });

  if (chromeBanner) {
    chromeBanner
      .querySelector('[data-action="install"]')
      ?.addEventListener('click', async () => {
        if (!deferredPrompt) return;
        try {
          deferredPrompt.prompt();
          const { outcome } = await deferredPrompt.userChoice;
          if (outcome === 'accepted') {
            _setInstalled();
          }
        } catch (err) {
          console.error('[pwa] install prompt failed:', err);
        } finally {
          // Per spec, the deferred prompt can only be used once.
          deferredPrompt = null;
          _hide(chromeBanner, 'flex');
        }
      });
    chromeBanner
      .querySelector('[data-action="dismiss"]')
      ?.addEventListener('click', () => {
        _setDismissed();
        _hide(chromeBanner, 'flex');
      });
  }

  /** Cross-platform: ``appinstalled`` fires after a successful install
   * from any path (browser-driven, our custom prompt, or the iOS Add
   * to Home Screen flow as observed in some webviews). */
  window.addEventListener('appinstalled', () => {
    _setInstalled();
    _hide(chromeBanner, 'flex');
    _hide(iosBanner, 'flex');
  });

  /** iOS path: reveal the instructional banner once the DOM is ready. */
  if (iosBanner && _isIosSafariBrowser()) {
    _show(iosBanner, 'flex');
    iosBanner
      .querySelector('[data-action="dismiss-ios"]')
      ?.addEventListener('click', () => {
        _setDismissed();
        _hide(iosBanner, 'flex');
      });
  }
})();
