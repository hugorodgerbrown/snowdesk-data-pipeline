/*
 * static/js/alerts_optin.js — the settings page's "This device" row for
 * bulletin alerts (SNOW-1325, SNOW-1326).
 *
 * Reads the device with alerts_optin_core.js's pushSupport() and shows the
 * one control that can work here:
 *
 *   - available, not subscribed: "Turn on notifications" asks permission,
 *     subscribes with the page's VAPID key and registers the endpoint at
 *     /account/push/register/.
 *   - available, subscribed: the status line says so, and "Send a test"
 *     posts /account/alerts/test/ so the person sees one land.
 *   - needs-home-screen / denied / unsupported: the how-to line for that
 *     case, rendered server-side in the row's strings <template>.
 *
 * Every visible string comes from #alerts-optin-strings-template via
 * window.pwaStrings.read(), with the English as the fallback.
 *
 * Markup contract (accounts/partials/_alert_settings.html):
 *   [data-alerts-optin]          the row
 *   [data-alerts-optin-status]   the status line
 *   [data-alerts-optin-enable]   the Turn on button (hidden until offered)
 *   [data-alerts-optin-test]     the Send a test button (hidden until subscribed)
 */

(function () {
  'use strict';

  /** @type {any} */
  var core = null;

  /** @returns {string} the csrftoken cookie Django set. */
  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  /** @returns {Object} the environment pushSupport() reads. */
  function readEnv() {
    var ua = navigator.userAgent || '';
    var isAppleMobile =
      /iPhone|iPad|iPod/.test(ua) ||
      // iPadOS 13+ reports a desktop Safari UA; touch points give it away.
      (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    var isStandalone =
      (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
      /** @type {any} */ (navigator).standalone === true;
    var hasNotification = typeof Notification !== 'undefined';
    return {
      hasServiceWorker: 'serviceWorker' in navigator,
      hasPushManager: 'PushManager' in window,
      hasNotification: hasNotification,
      permission: hasNotification ? Notification.permission : '',
      isAppleMobile: isAppleMobile,
      isStandalone: isStandalone,
      hasDeclarative: hasNotification && 'declarativePush' in Notification,
    };
  }

  function init() {
    core = self.pwaAlertsOptinCore;
    var row = document.querySelector('[data-alerts-optin]');
    if (!row || !core || row.hasAttribute('data-alerts-optin-bound')) return;
    row.setAttribute('data-alerts-optin-bound', '');
    var status = row.querySelector('[data-alerts-optin-status]');
    var enable = row.querySelector('[data-alerts-optin-enable]');
    var test = row.querySelector('[data-alerts-optin-test]');
    var S = self.pwaStrings
      ? self.pwaStrings.read('alerts-optin-strings-template', {
          available: 'Notifications are off on this device.',
          subscribed: 'This device gets alerts.',
          'needs-home-screen':
            'On iPhone and iPad, add Snowdesk to your Home Screen (Share, then Add to Home Screen), open it from there and turn notifications on.',
          denied:
            'Notifications are blocked for Snowdesk. Allow them in this browser’s site settings, then reload.',
          unsupported: 'This browser cannot show notifications. Alerts will come by email.',
          failed: 'That did not work. Try again.',
          'test-sent': 'A test alert is on its way.',
          'test-no-device': 'This device is not registered for alerts. Turn notifications off and on again.',
          'test-failed': 'The test alert could not be sent.',
        })
      : {};

    /** @param {string} key */
    function say(key) {
      if (status) status.textContent = S[key] || '';
    }

    var env = readEnv();
    var support = core.pushSupport(env);
    if (support !== 'available') {
      say(support);
      return;
    }

    navigator.serviceWorker.ready
      .then(function (reg) {
        return reg.pushManager.getSubscription();
      })
      .then(function (sub) {
        // An existing browser subscription may belong to another account,
        // or its registration may have failed: register it again (the
        // endpoint is upserted onto this account) before claiming it.
        return sub ? register(sub, env) : false;
      })
      .then(function (registered) {
        if (registered) {
          say('subscribed');
          if (test) test.hidden = false;
        } else {
          say('available');
          if (enable) enable.hidden = false;
        }
      })
      .catch(function () {
        say('available');
        if (enable) enable.hidden = false;
      });

    if (enable) {
      enable.addEventListener('click', function () {
        enable.setAttribute('disabled', '');
        subscribe(env)
          .then(function (ok) {
            if (ok) {
              enable.hidden = true;
              if (test) test.hidden = false;
              say('subscribed');
            } else {
              say(Notification.permission === 'denied' ? 'denied' : 'failed');
            }
          })
          .catch(function () {
            say('failed');
          })
          .finally(function () {
            enable.removeAttribute('disabled');
          });
      });
    }

    if (test) {
      test.addEventListener('click', function () {
        fetch('/account/alerts/test/', {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken() },
          credentials: 'same-origin',
        })
          .then(function (resp) {
            if (!resp.ok) return 'failed';
            return resp.json().then(core.testOutcome);
          })
          .then(say)
          .catch(function () {
            say('failed');
          });
      });
    }
  }

  /**
   * Ask permission, subscribe and register the endpoint.
   *
   * @param {Object} env
   * @returns {Promise<boolean>}
   */
  function subscribe(env) {
    return Notification.requestPermission().then(function (perm) {
      if (perm !== 'granted') return false;
      var meta = document.querySelector('meta[name="vapid-public-key"]');
      var key = meta ? meta.getAttribute('content') || '' : '';
      if (!key) return false;
      return navigator.serviceWorker.ready
        .then(function (reg) {
          return reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: core.urlBase64ToUint8Array(key),
          });
        })
        .then(function (sub) {
          return register(sub, env);
        });
    });
  }

  /**
   * Register a browser subscription against the signed-in account.
   *
   * @param {PushSubscription} sub
   * @param {Object} env
   * @returns {Promise<boolean>}
   */
  function register(sub, env) {
    var body = Object.assign({}, sub.toJSON(), { mechanism: core.mechanismFor(env) });
    return fetch('/account/push/register/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    }).then(function (resp) {
      if (resp.ok && self.pwaDb) {
        // The launch-time reverify loop (push_demo.js) keys off this.
        self.pwaDb
          .put('meta:app', { key: 'push.subscribed_before', value: true })
          .catch(function () {});
      }
      return resp.ok;
    });
  }

  // Deferred: wait for DOMContentLoaded, which fires after every deferred
  // script — i18n_strings.js among them — has run.
  if (document.readyState === 'complete') {
    init();
  } else {
    document.addEventListener('DOMContentLoaded', init);
  }
  // The settings partial is swapped by HTMX when a mode changes; bind again.
  document.addEventListener('htmx:afterSwap', init);
})();
