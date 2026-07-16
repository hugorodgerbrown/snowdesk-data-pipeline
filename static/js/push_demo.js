/*
 * static/js/push_demo.js — Client side of the Web Push staff demo.
 *
 * Wires three buttons (#push-enable, #push-disable, #push-test) on the
 * /_push-demo/ page to the PushManager + the register/unregister/test
 * endpoints.
 *
 * Reads the VAPID public key from the meta tag `<meta name="vapid-public-key">`
 * that the template emits. The key is URL-safe-base64 (RFC 7515) and must be
 * fed to PushManager.subscribe as a Uint8Array.
 *
 * Every fetch to /subscribe/push/* carries the CSRF token read from the
 * csrftoken cookie (Django's default CSRF middleware accepts it via the
 * X-CSRFToken header) and credentials: 'same-origin' so the session cookie
 * is included for the staff_member_required check.
 *
 * SNOW-380 additions:
 *   - Every register POST now carries a "mechanism" field ("declarative"
 *     when the browser supports Apple's Declarative Web Push, "sw"
 *     otherwise) so the server can shape the outgoing payload correctly.
 *   - On a successful register, `push.subscribed_before` is written to
 *     the `meta:app` IndexedDB store (SNOW-375). `reverifyPushSubscription`
 *     reads that flag at page load: if it's true but the browser reports
 *     no live subscription (permission revoked, SW evicted, endpoint
 *     expired — spec §8), it silently resubscribes and re-registers
 *     without prompting the user again, and emits
 *     `pwa.push.subscription_lost` either way so loss is visible in
 *     telemetry even when recovery fails.
 */

'use strict';

(function () {
  const $ = (sel) => document.querySelector(sel);
  const log = (msg) => {
    const out = $('#push-log');
    if (!out) return;
    const stamp = new Date().toISOString().slice(11, 19);
    out.textContent = `[${stamp}] ${msg}\n` + out.textContent;
  };

  /** Read the csrftoken cookie value set by Django's CsrfViewMiddleware. */
  function getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  function urlBase64ToUint8Array(b64) {
    const padding = '='.repeat((4 - (b64.length % 4)) % 4);
    const base64 = (b64 + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  /**
   * Feature-detect Apple's Declarative Web Push (iOS 18.4+, WebKit).
   * Declarative subscriptions get a fixed JSON payload shape the OS
   * renders directly, without running the service worker's `push`
   * handler — see `subscriptions/push_service.py::_build_wire_payload`.
   */
  function _supportsDeclarativePush() {
    return typeof Notification !== 'undefined' && 'declarativePush' in Notification;
  }

  async function getRegistration() {
    const reg = await navigator.serviceWorker.ready;
    return reg;
  }

  async function currentSubscription() {
    const reg = await getRegistration();
    return reg.pushManager.getSubscription();
  }

  /** POST a PushSubscriptionJSON + mechanism to the register endpoint. */
  async function _postRegister(subJson, mechanism) {
    return fetch('/subscribe/push/register/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      credentials: 'same-origin',
      body: JSON.stringify({ ...subJson, mechanism }),
    });
  }

  async function refreshState() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      $('#push-state').textContent = 'unsupported in this browser';
      return;
    }
    $('#push-permission').textContent = Notification.permission;
    const sub = await currentSubscription();
    $('#push-state').textContent = sub ? 'subscribed' : 'not subscribed';
    $('#push-endpoint').textContent = sub ? sub.endpoint : '—';
  }

  async function enablePush() {
    log('requesting permission…');
    const perm = await Notification.requestPermission();
    log(`permission: ${perm}`);
    if (perm !== 'granted') return refreshState();

    const reg = await getRegistration();
    const key = document
      .querySelector('meta[name="vapid-public-key"]')
      .getAttribute('content');
    log('subscribing to push manager…');
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
    const mechanism = _supportsDeclarativePush() ? 'declarative' : 'sw';
    log(`POST /subscribe/push/register/ (mechanism=${mechanism})`);
    const resp = await _postRegister(sub.toJSON(), mechanism);
    log(`register → ${resp.status}`);
    if (resp.ok) {
      try {
        await window.pwaDb?.put('meta:app', {
          key: 'push.subscribed_before',
          value: true,
        });
      } catch (_e) {
        // Non-fatal — reverifyPushSubscription just won't have a flag to
        // act on next launch; the current subscription is still live.
      }
    }
    await refreshState();
  }

  /**
   * Launch-time re-verification loop (SNOW-380, spec §8).
   *
   * If this device previously subscribed (`push.subscribed_before` is
   * true in `meta:app`) but the browser now reports no live subscription,
   * the subscription was silently lost — permission revoked, the service
   * worker was evicted, or the push service expired the endpoint. Rather
   * than surface a UI prompt on every page load, we attempt one silent
   * resubscribe (no `Notification.requestPermission()` call — permission
   * was already granted once, and re-prompting on every visit would be
   * intrusive). `pwa.push.subscription_lost` fires whichever way this
   * goes, with a `reason` explaining the outcome, so the loss is visible
   * in telemetry even when recovery itself fails.
   */
  async function reverifyPushSubscription() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    if (typeof window.pwaDb !== 'object') return;

    let subscribedBefore = false;
    try {
      const entry = await window.pwaDb.get('meta:app', 'push.subscribed_before');
      subscribedBefore = !!(entry && entry.value);
    } catch (_e) {
      // Can't read meta:app (Reset Required, quota, etc.) — nothing to
      // reconcile against this load.
      return;
    }
    if (!subscribedBefore) return;

    let sub;
    try {
      sub = await currentSubscription();
    } catch (_e) {
      window.pwaTelemetry?.emit('pwa.push.subscription_lost', {
        reason: 'get_subscription_failed',
      });
      return;
    }
    if (sub) return; // still subscribed — nothing to do

    const keyMeta = document.querySelector('meta[name="vapid-public-key"]');
    const key = keyMeta ? keyMeta.getAttribute('content') : '';
    if (!key) {
      window.pwaTelemetry?.emit('pwa.push.subscription_lost', {
        reason: 'missing_vapid_key',
      });
      return;
    }

    let newSub;
    try {
      const reg = await getRegistration();
      newSub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(key),
      });
    } catch (_e) {
      // Most likely permission was revoked at the OS level — silent
      // resubscribe can't recover from that; the user must re-enable
      // manually via the "Enable push" button.
      window.pwaTelemetry?.emit('pwa.push.subscription_lost', {
        reason: 'resubscribe_failed',
      });
      return;
    }

    try {
      const mechanism = _supportsDeclarativePush() ? 'declarative' : 'sw';
      const resp = await _postRegister(newSub.toJSON(), mechanism);
      if (!resp.ok) {
        window.pwaTelemetry?.emit('pwa.push.subscription_lost', {
          reason: `register_failed_${resp.status}`,
        });
        return;
      }
    } catch (_e) {
      window.pwaTelemetry?.emit('pwa.push.subscription_lost', {
        reason: 'register_failed',
      });
      return;
    }

    // Recovered — still worth recording that a loss happened at all.
    window.pwaTelemetry?.emit('pwa.push.subscription_lost', {
      reason: 'reverify_missing',
    });
  }

  async function disablePush() {
    const sub = await currentSubscription();
    if (!sub) return refreshState();
    log('unsubscribing from push manager…');
    // WHY: sub.unsubscribe() must only ever run from this explicit,
    // user-initiated "Disable push" click. Never call it from a logout
    // hook or any other automated flow (spec §8.2.5) — signing out is
    // not the same as opting out of push, and an automatic unsubscribe
    // would silently break notifications for a user who just wanted a
    // fresh session on the same device.
    await sub.unsubscribe();
    log('POST /subscribe/push/unregister/');
    const resp = await fetch('/subscribe/push/unregister/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      credentials: 'same-origin',
      body: JSON.stringify({ endpoint: sub.endpoint }),
    });
    log(`unregister → ${resp.status}`);
    await refreshState();
  }

  async function sendTestPush() {
    const sub = await currentSubscription();
    if (!sub) {
      log('no local subscription — enable first or fire from server');
      return;
    }
    const body = {
      endpoint: sub.endpoint,
      title: $('#push-title').value || 'Snowdesk',
      body: $('#push-body').value || 'Test push from /_push-demo/',
      url: $('#push-url').value || '/',
    };
    log(`POST /subscribe/push/test/ → ${body.title}`);
    const resp = await fetch('/subscribe/push/test/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    log(`test → ${resp.status} ${JSON.stringify(data)}`);
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('#push-enable')?.addEventListener('click', enablePush);
    $('#push-disable')?.addEventListener('click', disablePush);
    $('#push-test')?.addEventListener('click', sendTestPush);
    refreshState();
    reverifyPushSubscription();
  });
})();
