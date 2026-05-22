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

  async function getRegistration() {
    const reg = await navigator.serviceWorker.ready;
    return reg;
  }

  async function currentSubscription() {
    const reg = await getRegistration();
    return reg.pushManager.getSubscription();
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
    log('POST /subscribe/push/register/');
    const resp = await fetch('/subscribe/push/register/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      credentials: 'same-origin',
      body: JSON.stringify(sub.toJSON()),
    });
    log(`register → ${resp.status}`);
    await refreshState();
  }

  async function disablePush() {
    const sub = await currentSubscription();
    if (!sub) return refreshState();
    log('unsubscribing from push manager…');
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
  });
})();
