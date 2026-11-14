/*
 * tests/js/test_alerts_optin_core.js — can this device take a bulletin
 * alert, and if not, why not (SNOW-1325, SNOW-1326); and the register
 * body, with the endpoint it replaces (SNOW-1494).
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/alerts_optin_core.js';

const core = self.pwaAlertsOptinCore;

const DESKTOP = {
  hasServiceWorker: true,
  hasPushManager: true,
  hasNotification: true,
  permission: 'default',
  isAppleMobile: false,
  isStandalone: false,
};

describe('pushSupport', () => {
  it('offers push on a capable desktop browser', () => {
    expect(core.pushSupport(DESKTOP)).toBe('available');
  });

  it('sends an iPhone in Safari to the Home Screen', () => {
    const env = { ...DESKTOP, hasPushManager: false, isAppleMobile: true };
    expect(core.pushSupport(env)).toBe('needs-home-screen');
  });

  it('offers push on an iPhone opened from the Home Screen', () => {
    const env = { ...DESKTOP, isAppleMobile: true, isStandalone: true };
    expect(core.pushSupport(env)).toBe('available');
  });

  it('reports a refused permission', () => {
    expect(core.pushSupport({ ...DESKTOP, permission: 'denied' })).toBe('denied');
  });

  it('reports a browser with no Push API', () => {
    expect(core.pushSupport({ ...DESKTOP, hasPushManager: false })).toBe('unsupported');
    expect(core.pushSupport({ ...DESKTOP, hasNotification: false })).toBe('unsupported');
  });
});

describe('mechanismFor', () => {
  it('picks declarative push where the browser has it', () => {
    expect(core.mechanismFor({ ...DESKTOP, hasDeclarative: true })).toBe('DECLARATIVE');
    expect(core.mechanismFor(DESKTOP)).toBe('SW');
  });
});

describe('urlBase64ToUint8Array', () => {
  it('decodes url-safe base64 without padding', () => {
    expect(Array.from(core.urlBase64ToUint8Array('AQID_-8'))).toEqual([1, 2, 3, 255, 239]);
  });
});

describe('testOutcome', () => {
  it('says sent when a device took it', () => {
    expect(core.testOutcome({ ok: true, devices: 2, delivered: 1 })).toBe('test-sent');
  });

  it('says no device when the account has none', () => {
    expect(core.testOutcome({ ok: false, devices: 0, delivered: 0 })).toBe('test-no-device');
  });

  it('says failed when every send failed', () => {
    expect(core.testOutcome({ ok: false, devices: 1, delivered: 0 })).toBe('test-failed');
  });
});

describe('registerBody', () => {
  const SUB = { endpoint: 'https://push.example/new', keys: { p256dh: 'p', auth: 'a' } };

  it('sends the subscription and the mechanism', () => {
    expect(core.registerBody(SUB, 'SW', '')).toEqual({ ...SUB, mechanism: 'SW' });
  });

  it('names the endpoint it replaces when the browser handed out a new one', () => {
    const body = core.registerBody(SUB, 'SW', 'https://push.example/old');
    expect(body.replaces).toBe('https://push.example/old');
  });

  it('replaces nothing when the endpoint is unchanged', () => {
    expect(core.registerBody(SUB, 'SW', SUB.endpoint)).not.toHaveProperty('replaces');
  });

  it('replaces nothing when no endpoint was stored', () => {
    expect(core.registerBody(SUB, 'DECLARATIVE', undefined)).not.toHaveProperty('replaces');
    expect(core.registerBody(SUB, 'SW', null)).not.toHaveProperty('replaces');
  });
});
