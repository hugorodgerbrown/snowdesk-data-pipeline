// @ts-check
/*
 * static/js/alerts_optin_core.js — can this device take a bulletin alert,
 * and if not, why not (SNOW-1325, SNOW-1326).
 *
 * The settings page's "This device" row asks one question before it offers
 * a button: what would happen if the person pressed it? Four answers, and
 * each one gets different words:
 *
 *   available          the browser can subscribe; offer "Turn on".
 *   needs-home-screen  an iPhone or iPad in Safari. Web Push works there
 *                      only once Snowdesk is added to the Home Screen and
 *                      opened from it (iOS 16.4+), so the row explains that
 *                      instead of offering a button that cannot work.
 *   denied             permission was refused; only the browser's own
 *                      site settings can undo that, so say where.
 *   unsupported        no Push API at all; email is the way.
 *
 * Pure: every input is passed in, so it is tested without a browser
 * (tests/js/test_alerts_optin_core.js). static/js/alerts_optin.js reads
 * the real environment and calls it.
 *
 * Exports (frozen `self.pwaAlertsOptinCore`):
 *   pushSupport(env)          → 'available' | 'needs-home-screen' | 'denied' | 'unsupported'
 *   mechanismFor(env)         → 'DECLARATIVE' | 'SW'
 *   urlBase64ToUint8Array(s)  → Uint8Array (the VAPID key for subscribe())
 */

(function () {
  'use strict';

  /**
   * @typedef {Object} PushEnv
   * @property {boolean} hasServiceWorker  'serviceWorker' in navigator
   * @property {boolean} hasPushManager    'PushManager' in window
   * @property {boolean} hasNotification   typeof Notification !== 'undefined'
   * @property {string}  permission        Notification.permission, or ''
   * @property {boolean} isAppleMobile     an iPhone, iPod or iPad (incl. iPadOS desktop UA)
   * @property {boolean} isStandalone      launched from the Home Screen
   * @property {boolean} [hasDeclarative]  'declarativePush' in Notification
   */

  /**
   * @param {PushEnv} env
   * @returns {'available' | 'needs-home-screen' | 'denied' | 'unsupported'}
   */
  function pushSupport(env) {
    // Order matters: Safari on iOS outside the Home Screen exposes a
    // service worker but no PushManager, and that is a fixable state,
    // not an unsupported one.
    if (env.isAppleMobile && !env.isStandalone) return 'needs-home-screen';
    if (!env.hasServiceWorker || !env.hasPushManager || !env.hasNotification) {
      return 'unsupported';
    }
    if (env.permission === 'denied') return 'denied';
    return 'available';
  }

  /**
   * @param {PushEnv} env
   * @returns {'DECLARATIVE' | 'SW'}
   */
  function mechanismFor(env) {
    return env.hasDeclarative ? 'DECLARATIVE' : 'SW';
  }

  /**
   * @param {string} b64
   * @returns {Uint8Array}
   */
  function urlBase64ToUint8Array(b64) {
    var padding = '='.repeat((4 - (b64.length % 4)) % 4);
    var base64 = (b64 + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = atob(base64);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  self.pwaAlertsOptinCore = Object.freeze({
    pushSupport: pushSupport,
    mechanismFor: mechanismFor,
    urlBase64ToUint8Array: urlBase64ToUint8Array,
  });
})();
